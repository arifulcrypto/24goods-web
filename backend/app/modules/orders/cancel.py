"""Cancel / close a payable (docs/review-v4.md §3).

Shared by the buyer endpoints and the staff "Close order": only ``pending_payment``; an
order inside a checkout cancels the WHOLE checkout (a partial cancel would change the amount
owed); and if a pay URL was ever issued the gateway is re-queried FIRST (Alipay's "query
before close" rule): paid -> settle through the normal path and answer 409 ``already_paid``;
query failure -> 503 so the caller retries.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import notify_tg
from app.core.audit import audit
from app.core.errors import AlreadyPaid, InvalidState, NotFound, Unavailable
from app.core.resources import Resources
from app.models import Checkout, Message, Order, User
from app.modules.chat.messages import post_system
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import broadcast_order, release_manual_stock
from app.modules.payments import usdt as usdt_svc
from app.modules.payments.channels import is_usdt
from app.modules.payments.settle import (
    PAID_STATES,
    NotifyOutcome,
    apply_checkout_payment,
    apply_order_payment,
    lock_checkout,
)
from app.modules.promo import service as promo_svc
from app.payments import bepusdt
from app.payments.bepusdt import BepusdtError
from app.payments.zpay import ZPayQueryError


async def _load_group(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID | None,
    checkout_id: uuid.UUID | None,
) -> tuple[Checkout | None, list[Order]]:
    """Lock the payable: checkout FOR UPDATE then orders ORDER BY id FOR UPDATE."""
    if checkout_id is None:
        assert order_id is not None
        checkout_id = await db.scalar(
            select(Order.checkout_id).where(Order.id == order_id, Order.tenant_id == tenant_id)
        )
        if checkout_id is None:
            order = await db.scalar(
                select(Order)
                .where(Order.id == order_id, Order.tenant_id == tenant_id)
                .with_for_update()
            )
            if order is None:
                raise NotFound("Order not found")
            return None, [order]
    exists = await db.scalar(
        select(Checkout.id).where(Checkout.id == checkout_id, Checkout.tenant_id == tenant_id)
    )
    if exists is None:
        raise NotFound("Checkout not found")
    return await lock_checkout(db, checkout_id, tenant_id)


def _status(co: Checkout | None, orders: list[Order]) -> str:
    return co.status if co is not None else orders[0].status


async def _settle_if_paid(
    db: AsyncSession,
    res: Resources,
    checkout_id: uuid.UUID | None,
    order_id: uuid.UUID | None,
    out_trade_no: str,
    amount_minor: int,
    channel: str | None = None,
) -> NotifyOutcome | None:
    """Re-query the gateway (no row locks held). If it says paid, settle and return the
    outcome; if it says unpaid / not found, ``None``; if the query fails or is malformed, 503."""
    if is_usdt(channel):
        return await _settle_usdt_if_paid(
            db, res, checkout_id, order_id, out_trade_no, amount_minor, channel or ""
        )
    try:
        q = await res.zpay.query_order(out_trade_no)
    except ZPayQueryError:
        raise Unavailable("Could not verify the payment status; please try again") from None
    if not (q.found and q.paid) or q.out_trade_no != out_trade_no:
        return None
    # a "paid" answer we cannot trust field-by-field is never settled (and never cancelled over)
    paid = q.money
    if (
        paid is None
        or not paid.is_finite()
        or paid != paid.quantize(Decimal("0.01"))
        or not q.trade_no
    ):
        raise Unavailable("Could not verify the payment status; please try again")
    raw = {"out_trade_no": out_trade_no, "money": str(paid), "trade_no": q.trade_no}
    if checkout_id is not None:
        return await apply_checkout_payment(
            db, res.settings, checkout_id, q.trade_no, paid, raw, None
        )
    assert order_id is not None
    return await apply_order_payment(db, res.settings, order_id, q.trade_no, paid, raw, None)


async def _settle_usdt_if_paid(
    db: AsyncSession,
    res: Resources,
    checkout_id: uuid.UUID | None,
    order_id: uuid.UUID | None,
    no: str,
    amount_minor: int,
    channel: str,
) -> NotifyOutcome | None:
    """Same as the ZPay path for a USDT order: re-query the stored trade_id (BEpusdt cannot be
    queried by our order number)."""
    att = await usdt_svc.load_attempt(res, no)
    if not att or att.get("channel") != channel or not att.get("trade_id"):
        return None  # no transaction was ever opened: nothing can have been paid
    try:
        info = await res.bepusdt.query_info(str(att["trade_id"]))
    except BepusdtError:
        raise Unavailable("Could not verify the payment status; please try again") from None
    if not info.found or info.status != bepusdt.STATUS_PAID:
        return None
    expected = Decimal(amount_minor) / 100
    if (
        info.order_id != no
        or info.trade_id != att["trade_id"]
        or info.money != expected
        or info.fiat != bepusdt.FIAT
        or bepusdt.TRADE_TYPE_TO_CHANNEL.get(info.trade_type) != channel
    ):
        raise Unavailable("Could not verify the payment status; please try again")
    raw = {
        "order_id": no,
        "trade_id": info.trade_id,
        "amount": str(info.money),
        "actual_amount": str(info.actual_amount),
        "token": info.address,
        "via": "cancel_requery",
    }
    if checkout_id is not None:
        return await apply_checkout_payment(
            db, res.settings, checkout_id, info.trade_id, expected, raw, None, channel
        )
    assert order_id is not None
    return await apply_order_payment(
        db, res.settings, order_id, info.trade_id, expected, raw, None, channel
    )


async def cancel_group(
    db: AsyncSession,
    res: Resources,
    *,
    order_id: uuid.UUID | None = None,
    checkout_id: uuid.UUID | None = None,
    actor: User,
    by: str,  # "buyer" | "staff"
    reason: str | None,
    ip: str | None,
) -> tuple[Checkout | None, list[Order]]:
    # a rollback below expires ORM instances: capture what is needed afterwards
    actor_id, actor_role, tid = actor.id, actor.role, actor.tenant_id
    co, orders = await _load_group(db, tid, order_id, checkout_id)
    if _status(co, orders) != "pending_payment":
        raise InvalidState("Only orders awaiting payment can be cancelled")

    # Query the gateway whenever it is configured (not only when a pay URL is known to have
    # been issued): "not found / unpaid" means proceed, a paid answer settles instead.
    group_channel = co.channel if co is not None else orders[0].channel
    gateway_ready = (
        res.bepusdt.configured
        if is_usdt(group_channel)
        else bool(res.settings.zpay_pid and res.settings.zpay_key)
    )
    if gateway_ready:
        no = co.checkout_no if co is not None else orders[0].order_no
        amount = co.amount_minor if co is not None else orders[0].amount_minor
        oid, cid = order_id, checkout_id
        co_id = co.id if co is not None else None
        single_id = orders[0].id if co is None else None
        await db.rollback()  # never hold row locks across the network call
        outcome = await _settle_if_paid(db, res, co_id, single_id, no, amount, group_channel)
        if outcome is not None:
            touched = outcome.orders or ([outcome.order] if outcome.order else [])
            for o in touched:
                await broadcast_order(
                    res,
                    db,
                    o,
                    [m for m in outcome.messages if m and m.conversation_id == o.conversation_id],
                )
            notify_tg.outcome_events(res, touched)
            raise AlreadyPaid("This order has already been paid")
        co, orders = await _load_group(db, tid, oid, cid)
        st = _status(co, orders)
        if st in PAID_STATES or st == "manual_review":
            raise AlreadyPaid("This order has already been paid")
        if st != "pending_payment":
            raise InvalidState("Only orders awaiting payment can be cancelled")

    clean = (reason or "").strip() or None
    msgs: list[Message | None] = []
    for o in orders:
        before = snapshot(o)
        await release_manual_stock(db, o)
        o.status = "cancelled"
        o.cancel_reason = clean[:200] if clean else None
        o.version += 1
        await add_event(
            db,
            o,
            "pending_payment",
            "cancelled",
            actor_id=actor_id,
            reason=(clean or f"{by}_cancelled")[:500],
        )
        await audit(
            db,
            tenant_id=o.tenant_id,
            action="order.cancelled",
            actor_id=actor_id,
            actor_role=actor_role,
            target=f"order:{o.id}",
            before=before,
            after=snapshot(o),
            ip=ip,
            detail={
                "reason": clean,
                "by": by,
                "checkout_id": str(o.checkout_id) if o.checkout_id else None,
            },
        )
        text = (
            "Order cancelled by buyer"
            if by == "buyer"
            else f"Order closed by support: {clean}"
            if clean
            else "Order closed by support"
        )
        msgs.append(
            await post_system(
                db,
                tenant_id=o.tenant_id,
                conversation_id=o.conversation_id,
                event="order_cancelled",
                text=text,
                order_id=str(o.id),
                reason=clean,
                by=by,
            )
        )
    # promo: reserved -> released (-used_count), buyer and staff cancel alike
    has_promo = (co.promo_code_id is not None) if co is not None else bool(orders[0].promo_code_id)
    if co is not None:
        await promo_svc.release(db, checkout_id=co.id, tenant_id=tid, expect=has_promo)
    else:
        await promo_svc.release(db, order_id=orders[0].id, tenant_id=tid, expect=has_promo)
    if co is not None:
        co.status = "cancelled"
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="checkout.cancelled",
            actor_id=actor_id,
            actor_role=actor_role,
            target=f"checkout:{co.id}",
            before={"status": "pending_payment"},
            after={"status": "cancelled"},
            ip=ip,
            detail={"reason": clean, "by": by, "orders": len(orders)},
        )
    await db.commit()
    for o, m in zip(orders, msgs, strict=True):
        await broadcast_order(res, db, o, [m])
    return co, orders
