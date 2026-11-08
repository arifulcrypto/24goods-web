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
from app.modules.common.views import order_card_payload
from app.modules.orders.events import add_event, snapshot
from app.modules.payments.channels import provider
from app.modules.payments.ledger import Line, post_txn
from app.modules.promo import service as promo_svc
from app.modules.reseller.signup import activate_signup
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
    dup_trade_no: str | None = None  # a SECOND paid trade arrived: staff must refund it


def exact_minor(amount: Decimal) -> int | None:
    """Minor units of an amount with at most 2 decimals; ``None`` for sub-fen / non-finite."""
    if not amount.is_finite():
        return None
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


async def apply_sold(db: AsyncSession, sold: dict[uuid.UUID, int]) -> None:
    """``sold += qty``, aggregated per product and applied in product_id order (stable lock
    order, one UPDATE per product)."""
    for pid in sorted(sold):
        await db.execute(
            update(Product).where(Product.id == pid).values(sold=Product.sold + sold[pid])
        )


def payment_lines(order: Order, channel: str = CHANNEL) -> list[Line]:
    """Ledger lines of a successful payment (``txn`` sums to zero because
    ``amount == subtotal - discount + fee``)::

        gateway_clearing:{provider}  +amount   (zpay | bepusdt)
        sales_revenue                -(subtotal - markup)     goods
        reseller_payable:{id}        -markup                  reseller storefront orders
        promo_discount               +discount                promo orders
        surcharge_revenue            -fee
    sign-up orders instead book ``reseller_signup_revenue -subtotal``.
    """
    lines = [Line(f"gateway_clearing:{provider(channel)}", order.amount_minor)]
    if order.kind == "reseller_signup":
        lines.append(Line("reseller_signup_revenue", -order.subtotal_minor))
    else:
        markup = order.reseller_markup_minor if order.reseller_id is not None else 0
        lines.append(Line("sales_revenue", -(order.subtotal_minor - markup)))
        if markup > 0:
            lines.append(Line(f"reseller_payable:{order.reseller_id}", -markup))
        if order.discount_minor > 0:
            lines.append(Line("promo_discount", order.discount_minor))
    if order.fee_minor > 0:
        lines.append(Line("surcharge_revenue", -order.fee_minor))
    # a zero-amount line (e.g. sales_revenue when markup == subtotal) is not allowed by
    # post_txn: dropping it keeps the transaction balanced, so settlement can never raise
    return [ln for ln in lines if ln.amount_minor != 0]


async def post_payment_ledger(
    db: AsyncSession, settings: Settings, order: Order, channel: str = CHANNEL
) -> None:
    """Payment ledger (see :func:`payment_lines`) plus the gateway cost entry (fees:zpay;
    self-hosted BEpusdt has no gateway fee)."""
    prov = provider(channel)
    await post_txn(
        db,
        tenant_id=order.tenant_id,
        currency=order.currency,
        order_id=order.id,
        memo=f"{prov} payment {order.order_no}",
        lines=payment_lines(order, channel),
    )
    gateway_cost = (
        percent_of(order.amount_minor, settings.zpay_fee_permille) if prov == "zpay" else 0
    )
    if gateway_cost > 0:
        await post_txn(
            db,
            tenant_id=order.tenant_id,
            currency=order.currency,
            order_id=order.id,
            memo=f"zpay fee {order.order_no}",
            lines=[Line("fees:zpay", gateway_cost), Line("gateway_clearing:zpay", -gateway_cost)],
        )


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


async def lock_checkout(
    db: AsyncSession, checkout_id: uuid.UUID, tenant_id: uuid.UUID | None = None
) -> tuple[Checkout, list[Order]]:
    """Checkout ``FOR UPDATE`` first, then its orders ``ORDER BY id FOR UPDATE``."""
    stmt = select(Checkout).where(Checkout.id == checkout_id)
    if tenant_id is not None:
        stmt = stmt.where(Checkout.tenant_id == tenant_id)
    co = await db.scalar(stmt.with_for_update())
    assert co is not None
    orders = list(
        await db.scalars(
            select(Order)
            .where(Order.checkout_id == checkout_id, Order.tenant_id == co.tenant_id)
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
    sold: dict[uuid.UUID, int],
    channel: str = CHANNEL,
) -> list[Message | None]:
    """pending/expired/cancelled -> paid (or manual_review if ``late``), with events, ledger,
    outbox, ``sold += qty``, system + buyer-input chat messages and the audit row.

    Caller holds the order row lock and commits.
    """
    before = snapshot(order)
    prev = order.status
    order.status = "manual_review" if late else "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.channel = channel
    order.version += 1
    await add_event(
        db,
        order,
        prev,
        order.status,
        actor_kind="gateway",
        reason="late_payment" if late else f"{provider(channel)}:{trade_no[:40]}",
    )
    await post_payment_ledger(db, settings, order, channel)
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
    elif order.kind == "reseller_signup":
        # Not fulfilled by the delivery worker (no outbox, no stock): activate the store and
        # complete the order here, in the same transaction as the payment.
        msgs.append(await activate_signup(db, order))
    else:
        db.add(
            Outbox(
                tenant_id=order.tenant_id,
                topic="order.paid",
                key=str(order.id),
                payload={"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
            )
        )
        if order.product_id:  # applied by the caller via apply_sold, after the member loop
            sold[order.product_id] = sold.get(order.product_id, 0) + order.qty
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
                type="order_card",
                payload={**order_card_payload(order), "event": "order_input"},
                client_msg_id=f"order-input:{order.id}",
            )
            msgs.append(m)
    if order.checkout_id is None:  # a checkout's redemption is settled by apply_checkout_payment
        await promo_svc.mark_used(
            db,
            order_id=order.id,
            tenant_id=order.tenant_id,
            expect=order.promo_code_id is not None,
        )
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
def _payment_row(
    tenant_id: uuid.UUID,
    channel_trade_no: str,
    amount_minor: int,
    currency: str,
    raw: dict[str, str],
    *,
    order_id: uuid.UUID | None = None,
    checkout_id: uuid.UUID | None = None,
    channel: str = CHANNEL,
) -> Payment:
    return Payment(
        tenant_id=tenant_id,
        order_id=order_id,
        checkout_id=checkout_id,
        channel=channel,
        gateway_trade_no=channel_trade_no[:64],
        amount_minor=amount_minor,
        currency=currency,
        raw=safe_params({k: v for k, v in raw.items() if k not in ("sign", "signature")}),
    )


async def _record_unrecorded_payment(
    db: AsyncSession,
    settings: Settings,
    order: Order,
    trade_no: str,
    amount_minor: int,
    raw: dict[str, str],
    ip: str | None,
    reason: str,
    channel: str = CHANNEL,
) -> bool:
    """F2: a verified notify must never leave money unrecorded. Record the Payment (and the
    payment ledger lines when the amount is the order's) and leave the order as it is."""
    db.add(
        _payment_row(
            order.tenant_id,
            trade_no,
            amount_minor,
            order.currency,
            raw,
            order_id=order.id,
            channel=channel,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    if amount_minor == order.amount_minor:
        await post_payment_ledger(db, settings, order, channel)
    if order.promo_code_id is not None:  # a Payment is recorded: the discount was honoured
        await promo_svc.mark_used(
            db,
            order_id=order.id if order.checkout_id is None else None,
            checkout_id=order.checkout_id,
            tenant_id=order.tenant_id,
            expect=True,
        )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="payment.recorded_in_review",
        actor_role="gateway",
        target=f"order:{order.id}",
        ip=ip,
        detail={"reason": reason, "trade_no": trade_no[:64], "paid_minor": amount_minor},
        severity="critical" if reason == "member_order_paid_directly" else "warning",
    )
    return True


async def apply_order_payment(
    db: AsyncSession,
    settings: Settings,
    order_id: uuid.UUID,
    trade_no: str,
    paid_amount: Decimal,
    raw: dict[str, str],
    ip: str | None,
    channel: str = CHANNEL,
    hold: str | None = None,
) -> NotifyOutcome:
    """Locked transaction for a verified + re-queried payment of one order. ``hold`` (a reason)
    records the Payment but never fulfils: the order goes to manual_review."""
    amount_minor = exact_minor(paid_amount)
    if amount_minor is None:
        return await to_manual_review(db, order_id, "bad_amount", ip, {})
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None

    if order.status in PAID_STATES or order.status == "manual_review":
        existing = await payment_for_order(db, order)
        if existing is None and order.status == "manual_review":
            # reached manual review without a Payment (e.g. re-query mismatch): record it now
            if order.checkout_id is None:
                reason = "recorded_after_review"
            else:
                reason = "member_order_paid_directly"
            ok = await _record_unrecorded_payment(
                db, settings, order, trade_no, amount_minor, raw, ip, reason, channel
            )
            if ok:
                await db.commit()
            return NotifyOutcome(SUCCESS, order)
        if existing is None or existing.gateway_trade_no != trade_no:
            # A SECOND paid trade (e.g. the old pay URL, then the new one): UNIQUE(order_id)
            # holds one Payment, so this money is not recordable. Never fulfil from here:
            # critical audit with the facts, hold the order, alert staff to refund it.
            await audit(
                db,
                tenant_id=order.tenant_id,
                action="payment.duplicate",
                actor_role="gateway",
                target=f"order:{order.id}",
                ip=ip,
                detail={
                    "trade_no": trade_no[:64],
                    "paid_minor": amount_minor,
                    "channel": channel,
                    "recorded_trade_no": existing.gateway_trade_no[:64] if existing else None,
                },
                severity="critical",
            )
            msgs = await _order_to_manual_review(
                db, order, "second_payment", ip, {"paid_minor": amount_minor}
            )
            await db.commit()
            return NotifyOutcome(SUCCESS, order, msgs, [order], dup_trade_no=trade_no)
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
        # A member order is only ever paid through its checkout: the money is real, so record
        # the Payment, hold the order for review and raise a critical audit entry.
        ok = await _record_unrecorded_payment(
            db,
            settings,
            order,
            trade_no,
            amount_minor,
            raw,
            ip,
            "member_order_paid_directly",
            channel,
        )
        if not ok:
            return NotifyOutcome(SUCCESS)
        before = snapshot(order)
        prev = order.status
        order.status = "manual_review"
        order.version += 1
        await add_event(
            db, order, prev, "manual_review", actor_kind="gateway", reason="member_paid_directly"
        )
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            ip=ip,
            detail={"reason": "member_order_paid_directly", "trade_no": trade_no[:64]},
            severity="critical",
        )
        msgs = [
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="manual_review",
                order_id=str(order.id),
            )
        ]
        await db.commit()
        return NotifyOutcome(SUCCESS, order, msgs, [order])
    if hold or amount_minor != order.amount_minor:
        # price/channel changed after the pay URL was issued. The gateway re-query confirmed
        # real money, so RECORD it (Payment row, UNIQUE(channel, trade_no) keeps this idempotent)
        # and hold the order for review: received money must never vanish (audit M1).
        reason = hold or "amount_changed"
        ok = await _record_unrecorded_payment(
            db, settings, order, trade_no, amount_minor, raw, ip, reason, channel
        )
        if not ok:
            return NotifyOutcome(SUCCESS)
        order.channel = channel  # refunds mirror the clearing account the money arrived on
        msgs = await _order_to_manual_review(db, order, reason, ip, {"paid_minor": amount_minor})
        await db.commit()
        return NotifyOutcome(SUCCESS, order, msgs, [order])

    db.add(
        _payment_row(
            order.tenant_id,
            trade_no,
            amount_minor,
            order.currency,
            raw,
            order_id=order.id,
            channel=channel,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(channel, trade_no) / UNIQUE(order_id): someone else settled it first.
        await db.rollback()
        return NotifyOutcome(SUCCESS)
    sold: dict[uuid.UUID, int] = {}
    msgs = await settle_member(
        db, settings, order, late=late, trade_no=trade_no, ip=ip, sold=sold, channel=channel
    )
    await apply_sold(db, sold)
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
    channel: str = CHANNEL,
    hold: str | None = None,
) -> NotifyOutcome:
    """Locked transaction for a verified + re-queried payment of a whole checkout.

    Lock order: checkout FOR UPDATE, then member orders ORDER BY id FOR UPDATE. One Payment
    row (``checkout_id``); then the same per-order transition as a single payment.
    """
    amount_minor = exact_minor(paid_amount)
    if amount_minor is None:
        return await checkout_to_manual_review(db, checkout_id, "bad_amount", ip, {})
    co, orders = await lock_checkout(db, checkout_id)
    first = orders[0] if orders else None

    if co.status in ("paid", "manual_review"):
        existing = await db.scalar(select(Payment).where(Payment.checkout_id == co.id))
        if existing is None and co.status == "manual_review":
            # reached manual review without a Payment (e.g. re-query mismatch): record it now
            db.add(
                _payment_row(
                    co.tenant_id,
                    trade_no,
                    amount_minor,
                    co.currency,
                    raw,
                    checkout_id=co.id,
                    channel=channel,
                )
            )
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                return NotifyOutcome(SUCCESS)
            if amount_minor == co.amount_minor == sum(o.amount_minor for o in orders):
                for o in orders:
                    await post_payment_ledger(db, settings, o, channel)
            co.paid_at = co.paid_at or datetime.now(timezone.utc)
            co.channel = channel
            for o in orders:  # refunds mirror the clearing account of the member order
                o.channel = channel
            if co.promo_code_id is not None:
                await promo_svc.mark_used(
                    db, checkout_id=co.id, tenant_id=co.tenant_id, expect=True
                )
            await audit(
                db,
                tenant_id=co.tenant_id,
                action="payment.recorded_in_review",
                actor_role="gateway",
                target=f"checkout:{co.id}",
                ip=ip,
                detail={"trade_no": trade_no[:64], "paid_minor": amount_minor},
                severity="warning",
            )
            await db.commit()
            return NotifyOutcome(SUCCESS, first, [], orders)
        if existing is None or existing.gateway_trade_no != trade_no:
            await audit(
                db,
                tenant_id=co.tenant_id,
                action="payment.duplicate",
                actor_role="gateway",
                target=f"checkout:{co.id}",
                ip=ip,
                detail={
                    "trade_no": trade_no[:64],
                    "paid_minor": amount_minor,
                    "channel": channel,
                    "recorded_trade_no": existing.gateway_trade_no[:64] if existing else None,
                },
                severity="critical",
            )
            msgs: list[Message | None] = []
            if co.status == "pending_payment":
                co.status = "manual_review"
            for o in orders:
                msgs += await _order_to_manual_review(
                    db, o, "second_payment", ip, {"paid_minor": amount_minor}
                )
            await db.commit()
            return NotifyOutcome(SUCCESS, first, msgs, orders, dup_trade_no=trade_no)
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
        _payment_row(
            co.tenant_id,
            trade_no,
            amount_minor,
            co.currency,
            raw,
            checkout_id=co.id,
            channel=channel,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return NotifyOutcome(SUCCESS)

    msgs: list[Message | None] = []
    total = sum(o.amount_minor for o in orders)
    if hold or total != amount_minor or amount_minor != co.amount_minor:
        # amounts drifted after the pay URL was issued: money is recorded, nothing is fulfilled
        co.status = "manual_review"
        co.paid_at = datetime.now(timezone.utc)
        co.channel = channel
        for o in orders:  # refunds mirror the clearing account of the member order
            o.channel = channel
        if amount_minor == co.amount_minor == total:  # e.g. held for a channel mismatch only
            for o in orders:
                await post_payment_ledger(db, settings, o, channel)
        if co.promo_code_id is not None:  # Payment recorded above
            await promo_svc.mark_used(db, checkout_id=co.id, tenant_id=co.tenant_id, expect=True)
        for o in orders:
            msgs += await _order_to_manual_review(
                db, o, hold or "amount_changed", ip, {"paid_minor": amount_minor}
            )
        await audit(
            db,
            tenant_id=co.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"checkout:{co.id}",
            ip=ip,
            detail={"reason": hold or "amount_changed", "paid_minor": amount_minor},
            severity="warning",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, first, msgs, orders)

    co_before = {"status": co.status, "amount_minor": co.amount_minor}
    sold: dict[uuid.UUID, int] = {}
    for o in orders:
        if o.status in ("pending_payment", "expired", "cancelled"):
            msgs += await settle_member(
                db,
                settings,
                o,
                late=co_late or o.status in ("expired", "cancelled"),
                trade_no=trade_no,
                ip=ip,
                sold=sold,
                channel=channel,
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
    await apply_sold(db, sold)
    await promo_svc.mark_used(  # reserved (or released-late) -> used
        db, checkout_id=co.id, tenant_id=co.tenant_id, expect=co.promo_code_id is not None
    )
    co.status = "manual_review" if co_late else "paid"
    co.paid_at = datetime.now(timezone.utc)
    co.channel = channel
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
