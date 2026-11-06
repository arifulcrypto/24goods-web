"""Payment settlement shared by the gateway notify and the cancel-time re-query
(AGENTS.md invariant 2). Everything here runs AFTER the signature / merchant / amount /
status checks and the server-to-server re-query; it takes the row locks, enforces the
UNIQUE constraints and writes the ledger + outbox.

* :func:`settle_member` is the ONE per-order paid transition used by single-order payments
  and by every order of a checkout, so both paths behave identically.
* :func:`apply_order_payment` / :func:`apply_checkout_payment` are the locked transactions.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.money import percent_of
from app.models import Checkout, Message, Order, Outbox, Payment, Product
from app.modules.chat.messages import (
    ensure_open_conversation,
    insert_message,
    post_system,
)
from app.modules.common.buyerinput import chat_text
from app.modules.orders.events import add_event, snapshot
from app.modules.payments.ledger import Line, post_txn
from app.payments.zpay import CHANNEL

log = logging.getLogger("app.payments.settle")

SUCCESS, FAIL = "success", "fail"
PAID_STATES = frozenset({"paid", "delivered", "completed"})


@dataclass
class NotifyOutcome:
    reply: str
    order: Order | None = None
    messages: list[Message | None] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)  # every order touched (checkout path)


def safe_params(params: dict[str, str]) -> dict[str, str]:
    """Notify params as stored for audit (no secrets in them, but bound the size)."""
    return {str(k)[:32]: str(v)[:256] for k, v in list(params.items())[:30]}


async def payment_for_order(db: AsyncSession, order: Order) -> Payment | None:
    """An order's payment: its own row, or the checkout's (``order_id OR checkout_id``)."""
    cond = Payment.order_id == order.id
    if order.checkout_id is not None:
        cond = or_(cond, Payment.checkout_id == order.checkout_id)
    return await db.scalar(select(Payment).where(cond))


# --------------------------------------------------------------------------- manual review
async def _order_to_manual_review(
    db: AsyncSession, order: Order, reason: str, ip: str | None, detail: dict
) -> list[Message | None]:
    """Move one locked order to manual_review (or audit an anomaly if it is already settled)."""
    msgs: list[Message | None] = []
    if order.status in ("pending_payment", "expired", "cancelled"):
        before = snapshot(order)
        prev = order.status
        order.status = "manual_review"
        order.version += 1
        await add_event(db, order, prev, "manual_review", actor_kind="gateway", reason=reason)
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="manual_review",
                order_id=str(order.id),
            )
        )
    else:
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.anomaly",
            actor_role="gateway",
            target=f"order:{order.id}",
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
    return msgs


async def to_manual_review(
    db: AsyncSession, order_id: uuid.UUID, reason: str, ip: str | None, detail: dict
) -> NotifyOutcome:
    await db.rollback()
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None
    msgs = await _order_to_manual_review(db, order, reason, ip, detail)
    await db.commit()
    return NotifyOutcome(SUCCESS, order, msgs)


async def lock_checkout(db: AsyncSession, checkout_id: uuid.UUID) -> tuple[Checkout, list[Order]]:
    """Checkout ``FOR UPDATE`` first, then its orders ``ORDER BY id FOR UPDATE``."""
    co = await db.scalar(select(Checkout).where(Checkout.id == checkout_id).with_for_update())
    assert co is not None
    orders = list(
        await db.scalars(
            select(Order)
            .where(Order.checkout_id == checkout_id)
            .order_by(Order.id)
            .with_for_update()
        )
    )
    return co, orders


async def checkout_to_manual_review(
    db: AsyncSession, checkout_id: uuid.UUID, reason: str, ip: str | None, detail: dict
) -> NotifyOutcome:
    await db.rollback()
    co, orders = await lock_checkout(db, checkout_id)
    msgs: list[Message | None] = []
    if co.status in ("pending_payment", "expired", "cancelled"):
        co.status = "manual_review"
        for o in orders:
            msgs += await _order_to_manual_review(db, o, reason, ip, detail)
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"checkout:{co.id}",
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
    else:
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="payment.anomaly",
            actor_role="gateway",
            target=f"checkout:{co.id}",
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
    await db.commit()
    return NotifyOutcome(SUCCESS, orders[0] if orders else None, msgs, orders)


# --------------------------------------------------------------------------- per-order transition
async def settle_member(
    db: AsyncSession,
    settings: Settings,
    order: Order,
    *,
    late: bool,
    trade_no: str,
    ip: str | None,
) -> list[Message | None]:
    """pending/expired/cancelled -> paid (or manual_review if ``late``), with events, ledger,
    outbox, ``sold += qty``, system + buyer-input chat messages and the audit row.

    Caller holds the order row lock and commits.
    """
    before = snapshot(order)
    prev = order.status
    order.status = "manual_review" if late else "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.channel = CHANNEL
    order.version += 1
    await add_event(
        db,
        order,
        prev,
        order.status,
        actor_kind="gateway",
        reason="late_payment" if late else f"zpay:{trade_no[:40]}",
    )
    lines = [
        Line("gateway_clearing:zpay", order.amount_minor),
        Line("sales_revenue", -order.subtotal_minor),
    ]
    if order.fee_minor > 0:
        lines.append(Line("surcharge_revenue", -order.fee_minor))
    await post_txn(
        db,
        tenant_id=order.tenant_id,
        currency=order.currency,
        order_id=order.id,
        memo=f"zpay payment {order.order_no}",
        lines=lines,
    )
    gateway_cost = percent_of(order.amount_minor, settings.zpay_fee_permille)
    if gateway_cost > 0:
        await post_txn(
            db,
            tenant_id=order.tenant_id,
            currency=order.currency,
            order_id=order.id,
            memo=f"zpay fee {order.order_no}",
            lines=[Line("fees:zpay", gateway_cost), Line("gateway_clearing:zpay", -gateway_cost)],
        )
    msgs: list[Message | None] = []
    if late:
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="manual_review",
                order_id=str(order.id),
            )
        )
    else:
        db.add(
            Outbox(
                tenant_id=order.tenant_id,
                topic="order.paid",
                key=str(order.id),
                payload={"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
            )
        )
        if order.product_id:
            await db.execute(
                update(Product)
                .where(Product.id == order.product_id)
                .values(sold=Product.sold + order.qty)
            )
        if order.buyer_input and order.conversation_id is None:
            order.conversation_id = await ensure_open_conversation(
                db, tenant_id=order.tenant_id, buyer_id=order.buyer_id
            )
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="paid",
                text=(
                    "Payment received. An agent will deliver your order shortly."
                    if order.delivery_mode == "manual"
                    else None
                ),
                order_id=str(order.id),
                awaiting_delivery=order.delivery_mode == "manual",
            )
        )
        if order.buyer_input and order.conversation_id is not None:
            # From the BUYER so CS sees it in the inbox (sound + notification).
            m, _ = await insert_message(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                sender_id=order.buyer_id,
                sender_role="buyer",
                type="text",
                text=chat_text(order.order_no, order.title, order.buyer_input),
                client_msg_id=f"order-input:{order.id}",
            )
            msgs.append(m)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="payment.settled",
        actor_role="gateway",
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={
            "trade_no": trade_no[:64],
            "late": late,
            **({"buyer_input": True} if order.buyer_input else {}),  # never the value
        },
    )
    return msgs


# --------------------------------------------------------------------------- single order
async def apply_order_payment(
    db: AsyncSession,
    settings: Settings,
    order_id: uuid.UUID,
    trade_no: str,
    paid_amount: Decimal,
    raw: dict[str, str],
    ip: str | None,
) -> NotifyOutcome:
    """Locked transaction for a verified + re-queried payment of one order."""
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None
    amount_minor = int(paid_amount * 100)

    if order.status in PAID_STATES or order.status == "manual_review":
        existing = await payment_for_order(db, order)
        if existing is None or existing.gateway_trade_no != trade_no:
            await audit(
                db,
                tenant_id=order.tenant_id,
                action="payment.duplicate",
                actor_role="gateway",
                target=f"order:{order.id}",
                ip=ip,
                detail={"trade_no": trade_no[:64]},
                severity="warning",
            )
            await db.commit()
        else:
            await db.rollback()
        return NotifyOutcome(SUCCESS, order)

    # Late payments (order expired/cancelled, however late) are still RECORDED
    # (Payment + ledger) and held in manual_review: received money must never vanish.
    late = order.status in ("expired", "cancelled")
    if order.status == "refunded":
        existing = await payment_for_order(db, order)
        if existing is not None and existing.gateway_trade_no == trade_no:
            await db.rollback()  # benign duplicate of the notify that was refunded later
            return NotifyOutcome(SUCCESS, order)
    if order.status not in ("pending_payment", "expired", "cancelled"):
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.unexpected_state",
            actor_role="gateway",
            target=f"order:{order.id}",
            ip=ip,
            detail={"trade_no": trade_no[:64], "status": order.status},
            severity="critical",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, order)
    if order.checkout_id is not None:
        # A member order is only ever paid through its checkout.
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.anomaly",
            actor_role="gateway",
            target=f"order:{order.id}",
            ip=ip,
            detail={"reason": "member_order_paid_directly", "trade_no": trade_no[:64]},
            severity="critical",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, order)
    if amount_minor != order.amount_minor:  # price changed after the pay URL was issued
        return await to_manual_review(
            db, order.id, "amount_changed", ip, {"paid_minor": amount_minor}
        )

    db.add(
        Payment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            channel=CHANNEL,
            gateway_trade_no=trade_no[:64],
            amount_minor=amount_minor,
            currency=order.currency,
            raw=safe_params({k: v for k, v in raw.items() if k != "sign"}),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(channel, trade_no) / UNIQUE(order_id): someone else settled it first.
        await db.rollback()
        return NotifyOutcome(SUCCESS)
    msgs = await settle_member(db, settings, order, late=late, trade_no=trade_no, ip=ip)
    await db.commit()
    return NotifyOutcome(SUCCESS, order, msgs, [order])


# --------------------------------------------------------------------------- checkout
async def apply_checkout_payment(
    db: AsyncSession,
    settings: Settings,
    checkout_id: uuid.UUID,
    trade_no: str,
    paid_amount: Decimal,
    raw: dict[str, str],
    ip: str | None,
) -> NotifyOutcome:
    """Locked transaction for a verified + re-queried payment of a whole checkout.

    Lock order: checkout FOR UPDATE, then member orders ORDER BY id FOR UPDATE. One Payment
    row (``checkout_id``); then the same per-order transition as a single payment.
    """
    co, orders = await lock_checkout(db, checkout_id)
    first = orders[0] if orders else None
    amount_minor = int(paid_amount * 100)

    if co.status in ("paid", "manual_review"):
        existing = await db.scalar(select(Payment).where(Payment.checkout_id == co.id))
        if existing is None or existing.gateway_trade_no != trade_no:
            await audit(
                db,
                tenant_id=co.tenant_id,
                action="payment.duplicate",
                actor_role="gateway",
                target=f"checkout:{co.id}",
                ip=ip,
                detail={"trade_no": trade_no[:64]},
                severity="warning",
            )
            await db.commit()
        else:
            await db.rollback()
        return NotifyOutcome(SUCCESS, first, [], orders)
    if co.status not in ("pending_payment", "expired", "cancelled"):
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="payment.unexpected_state",
            actor_role="gateway",
            target=f"checkout:{co.id}",
            ip=ip,
            detail={"trade_no": trade_no[:64], "status": co.status},
            severity="critical",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, first, [], orders)
    # If the checkout itself is expired/cancelled every member is late.
    co_late = co.status in ("expired", "cancelled")

    db.add(
        Payment(
            tenant_id=co.tenant_id,
            checkout_id=co.id,
            channel=CHANNEL,
            gateway_trade_no=trade_no[:64],
            amount_minor=amount_minor,
            currency=co.currency,
            raw=safe_params({k: v for k, v in raw.items() if k != "sign"}),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return NotifyOutcome(SUCCESS)

    msgs: list[Message | None] = []
    total = sum(o.amount_minor for o in orders)
    if total != amount_minor or amount_minor != co.amount_minor:
        # amounts drifted after the pay URL was issued: money is recorded, nothing is fulfilled
        co.status = "manual_review"
        co.paid_at = datetime.now(timezone.utc)
        co.channel = CHANNEL
        for o in orders:
            msgs += await _order_to_manual_review(
                db, o, "amount_changed", ip, {"paid_minor": amount_minor}
            )
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"checkout:{co.id}",
            ip=ip,
            detail={"reason": "amount_changed", "paid_minor": amount_minor},
            severity="warning",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, first, msgs, orders)

    co_before = {"status": co.status, "amount_minor": co.amount_minor}
    for o in orders:
        if o.status in ("pending_payment", "expired", "cancelled"):
            msgs += await settle_member(
                db,
                settings,
                o,
                late=co_late or o.status in ("expired", "cancelled"),
                trade_no=trade_no,
                ip=ip,
            )
        else:  # a member that is already settled/refunded cannot be paid again
            await audit(
                db,
                tenant_id=o.tenant_id,
                action="payment.unexpected_state",
                actor_role="gateway",
                target=f"order:{o.id}",
                ip=ip,
                detail={"trade_no": trade_no[:64], "status": o.status},
                severity="critical",
            )
    co.status = "manual_review" if co_late else "paid"
    co.paid_at = datetime.now(timezone.utc)
    co.channel = CHANNEL
    await audit(
        db,
        tenant_id=co.tenant_id,
        action="payment.settled",
        actor_role="gateway",
        target=f"checkout:{co.id}",
        before=co_before,
        after={"status": co.status, "amount_minor": co.amount_minor},
        ip=ip,
        detail={"trade_no": trade_no[:64], "late": co_late, "orders": len(orders)},
    )
    await db.commit()
    return NotifyOutcome(SUCCESS, first, msgs, orders)
