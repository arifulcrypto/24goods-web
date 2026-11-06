"""Reseller sign-up settlement (review-v5 s7).

Called from ``settle_member`` (inside the payment transaction, order row already locked) when
a ``kind='reseller_signup'`` order is paid. Lock order everywhere: ORDER first, then the
reseller row. Kept free of ``orders.service`` so ``payments.settle`` can import it.

MONEY: a paid sign-up that cannot activate exactly the store it was issued for (store already
active/suspended, gone, or pointing at another sign-up order) is NEVER "completed with
nothing": the order goes to ``manual_review`` (money stays recorded as sign-up revenue) and a
human resolves it with ``resolve_signup`` (activate or refund).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storefront
from app.core.audit import audit
from app.core.errors import InvalidState, NotFound
from app.models import LedgerEntry, Message, Order, Reseller, User
from app.modules.chat.messages import post_system
from app.modules.orders.events import add_event, snapshot
from app.modules.payments.ledger import Line, post_txn


async def _store_of(db: AsyncSession, order: Order) -> Reseller | None:
    """The buyer's store (one per user), locked. Found by the order's BUYER, not only by
    ``signup_order_id``."""
    return await db.scalar(
        select(Reseller)
        .where(Reseller.user_id == order.buyer_id, Reseller.tenant_id == order.tenant_id)
        .with_for_update()
    )


async def _activate(db: AsyncSession, r: Reseller, order: Order, actor: str) -> None:
    now = datetime.now(timezone.utc)
    r.status, r.activated_at, r.updated_at = "active", now, now
    storefront.invalidate()
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="reseller.activated",
        actor_role=actor,
        target=f"reseller:{r.id}",
        before={"status": "pending_payment"},
        after={"status": "active"},
        detail={"order_id": str(order.id)},
    )


async def activate_signup(db: AsyncSession, order: Order) -> Message | None:
    r = await _store_of(db, order)
    prev = order.status
    if r is None or r.status != "pending_payment" or r.signup_order_id != order.id:
        order.status = "manual_review"
        order.version += 1
        await add_event(
            db, order, prev, "manual_review", actor_kind="gateway", reason="signup_cannot_activate"
        )
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="reseller.signup_manual_review",
            target=f"order:{order.id}",
            detail={
                "reseller_status": r.status if r else None,
                "signup_order_matches": bool(r and r.signup_order_id == order.id),
            },
            severity="critical",
        )
        return await post_system(
            db,
            tenant_id=order.tenant_id,
            conversation_id=order.conversation_id,
            event="manual_review",
            order_id=str(order.id),
        )
    order.status = "completed"
    order.completed_at = datetime.now(timezone.utc)
    order.version += 1
    await add_event(
        db, order, prev, "completed", actor_kind="gateway", reason="reseller_signup_paid"
    )
    await _activate(db, r, order, "gateway")
    return await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="reseller_activated",
        text=f"Your reseller store “{r.store_name}” is now active.",
        order_id=str(order.id),
    )


async def resolve_signup(
    db: AsyncSession,
    staff: User,
    order_id: uuid.UUID,
    action: str,
    note: str,
    ip: str | None,
) -> Order:
    """Staff resolution of a sign-up order held in ``manual_review``.

    * ``activate``: activate the buyer's pending store (or keep an active one) and complete.
    * ``refund``: reverse the sign-up ledger lines exactly and mark the order ``refunded``.
      There is no ZPay refund API integration in this code base (after-sales refunds are
      ledger records too): the audit row says the money must be returned out of band.
    """
    order = await db.scalar(
        select(Order)
        .where(Order.id == order_id, Order.tenant_id == staff.tenant_id)
        .with_for_update()
    )
    if order is None or order.kind != "reseller_signup":
        raise NotFound("Order not found")
    if order.status != "manual_review":
        raise InvalidState("Only sign-up orders in manual review can be resolved")
    before = snapshot(order)
    prev = order.status
    if action == "activate":
        r = await _store_of(db, order)
        if r is None:
            raise InvalidState("The buyer has no reseller store to activate")
        if r.status == "pending_payment":
            await _activate(db, r, order, staff.role)
            r.signup_order_id = order.id
        order.status = "completed"
        order.completed_at = datetime.now(timezone.utc)
        reason = "signup_activated_by_staff"
    else:
        paid_lines = list(
            await db.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.order_id == order.id,
                    LedgerEntry.tenant_id == order.tenant_id,
                    LedgerEntry.memo.like("zpay payment%"),
                )
            )
        )
        refunded = await db.scalar(
            select(LedgerEntry.id).where(
                LedgerEntry.order_id == order.id,
                LedgerEntry.tenant_id == order.tenant_id,
                LedgerEntry.memo.like("refund %"),
            )
        )
        if not paid_lines or refunded is not None:
            raise InvalidState("No recorded payment to refund (or already refunded)")
        # MONEY: exact mirror of the payment txn lines
        await post_txn(
            db,
            tenant_id=order.tenant_id,
            currency=order.currency,
            order_id=order.id,
            memo=f"refund {order.order_no}",
            lines=[Line(e.account, -e.amount_minor) for e in reversed(paid_lines)],
        )
        order.status = "refunded"
        reason = "signup_refunded_by_staff"
    order.version += 1
    await add_event(db, order, prev, order.status, actor_id=staff.id, reason=reason)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action=f"order.signup_{'activated' if action == 'activate' else 'refunded'}",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={"note": note[:200], **({"manual_refund": True} if action == "refund" else {})},
        severity="warning",
    )
    await db.commit()
    return order
