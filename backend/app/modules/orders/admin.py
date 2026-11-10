"""Staff-side order operations: price change, manual delivery, cancel, listing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import AlreadyPaid, InvalidState, NotFound, OutOfStock, Validation
from app.core.money import MoneyError, fmt, parse_amount
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.models import (
    Attachment,
    Checkout,
    Message,
    Order,
    OrderEvent,
    Outbox,
    Payment,
    PaymentAttempt,
    Product,
    User,
)
from app.modules.aftersales import service as aftersales_svc
from app.modules.catalog.service import escape_like
from app.modules.chat.messages import post_system
from app.modules.common.sitedefaults import delivery_deadlines, order_terms
from app.modules.common.views import iso
from app.modules.content.site import tenant_settings
from app.modules.orders import cancel as cancel_svc
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import (
    available_stock,
    broadcast_order,
    lock_manual_products,
    order_views,
    release_manual_stock,
    reserve_manual_stock,
    reserved_auto_stock,
)
from app.modules.payments.channels import split_amount
from app.modules.payments.settle import apply_sold, payment_for_order
from app.modules.promo import service as promo_svc


async def lock_order(db: AsyncSession, order_id: uuid.UUID, tenant_id: uuid.UUID) -> Order:
    order = await db.scalar(
        select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id).with_for_update()
    )
    if order is None:
        raise NotFound("Order not found")
    return order


async def change_price(
    db: AsyncSession,
    order_id: uuid.UUID,
    amount: str,
    reason: str,
    agent: User,
    ip: str | None,
    remove_markup: bool = False,
) -> tuple[Order, Message | None]:
    try:
        new_minor = parse_amount(amount)
    except MoneyError as exc:
        raise Validation(f"amount: {exc}") from None
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.status != "pending_payment":
        raise InvalidState("Price can only change while awaiting payment")
    if order.checkout_id is not None:
        raise InvalidState(
            "This order is part of a cart checkout; close the checkout instead",
            code="reprice_checkout_member",
        )
    if order.kind != "goods":
        raise InvalidState("This order's price cannot be changed", code="reprice_not_goods")
    floor_total = 0
    if order.product_id is not None:
        product = await db.get(Product, order.product_id)
        if product is None:  # floor unknown: never reprice blind
            raise InvalidState("The order's product no longer exists", code="reprice_not_goods")
        floor_total = product.floor_price_minor * order.qty
        if new_minor < floor_total:
            raise Validation("amount is below the floor price", code="reprice_below_floor")
    before = snapshot(order)
    old_discount, old_markup = order.discount_minor, order.reseller_markup_minor
    if order.promo_code_id is not None or old_discount > 0:
        # the staff price replaces the promo: reserved -> released (-used_count)
        await promo_svc.release(db, order_id=order.id, tenant_id=order.tenant_id, expect=True)
        order.discount_minor = 0
        order.promo_code_id = None
    # reseller markup is a slice of the subtotal on top of the merchant's base price (as quoted
    # at order time): a staff discount comes out of the markup first, the merchant's base last
    base_total = order.subtotal_minor - old_markup
    order.reseller_markup_minor = (
        0 if remove_markup else max(0, min(old_markup, new_minor - base_total))
    )
    old_minor = order.amount_minor
    # staff set the new SUBTOTAL; the channel fee is recomputed at the rate the order carries
    fee, total = split_amount(new_minor, order.fee_permille)  # discount is 0 after a reprice
    order.subtotal_minor = new_minor
    order.fee_minor = fee
    order.amount_minor = total
    order.version += 1
    order.channel = None  # previously issued pay URLs no longer match (-> manual_review)
    await add_event(
        db,
        order,
        order.status,
        order.status,
        actor_id=agent.id,
        reason=f"price_changed {fmt(old_minor)} -> {fmt(total)}: {reason}"[:500],
    )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.price_changed",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={
            "reason": reason,
            "old_discount_minor": old_discount,
            "new_discount_minor": order.discount_minor,
            "old_markup_minor": old_markup,
            "new_markup_minor": order.reseller_markup_minor,
            "remove_markup": remove_markup,
        },
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="price_changed",
        order_id=str(order.id),
        amount=fmt(total),
        old_amount=fmt(old_minor),
        currency=order.currency,
        version=order.version,
    )
    await db.commit()
    return order, msg


async def deliver_manual(
    db: AsyncSession,
    order_id: uuid.UUID,
    text: str,
    attachment_id: uuid.UUID | None,
    agent: User,
    ip: str | None,
) -> tuple[Order, Message | None]:
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.delivery_mode != "manual" or order.status != "paid":
        raise InvalidState("Only paid manual orders can be delivered")
    if attachment_id is not None:
        att = await db.get(Attachment, attachment_id)
        # Only a file the acting staff member uploaded: never a buyer's private chat upload
        # (that would hand it to another buyer through the order), not even for admins.
        if att is None or att.owner_id != agent.id or att.purpose not in ("chat", "delivery"):
            raise NotFound("Attachment not found")
        att.purpose = "delivery"
    before = snapshot(order)
    now = datetime.now(timezone.utc)
    product = await db.get(Product, order.product_id) if order.product_id else None
    t_hours, t_months = order_terms(order, product)
    until, confirm_at = delivery_deadlines(
        now,
        mode=order.delivery_mode,
        product_hours=t_hours,
        product_months=t_months,
        tenant_settings=await tenant_settings(db, order.tenant_id),
    )
    order.status = "delivered"
    order.delivered_at = now
    order.after_sales_until = until
    order.auto_confirm_at = confirm_at
    order.manual_delivery_text = text.strip()
    order.manual_delivery_attachment_id = attachment_id
    order.version += 1
    await add_event(db, order, "paid", "delivered", actor_id=agent.id, reason="manual_delivery")
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.delivered",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={"mode": "manual"},
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="delivered",
        order_id=str(order.id),
        delivered_at=iso(order.delivered_at),
    )
    await db.commit()
    return order, msg


async def requeue_delivery(
    db: AsyncSession,
    order_id: uuid.UUID,
    agent: User,
    ip: str | None,
    reason: str | None = None,
) -> tuple[Order, Message | None]:
    """v8 A.2: put a held (out-of-stock) auto-card order back on the delivery queue once cards
    exist again. Never infers payment from the status: a succeeded payment covering the
    order and its booked ledger entry are re-checked here; the delivery worker then runs the
    normal path (it locks the order and re-claims cards)."""
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.kind != "goods" or order.delivery_mode != "auto_card":
        raise InvalidState("Only auto-delivery goods orders can be requeued")
    if order.status != "manual_review":
        raise InvalidState("Only an order held for review can be requeued")
    pay = await payment_for_order(db, order)
    if (
        pay is None
        or pay.status != "succeeded"
        or pay.currency != order.currency
        or pay.amount_minor < order.amount_minor
        or order.paid_at is None
        or not await aftersales_svc._has_open_ledger(db, order)
    ):
        raise InvalidState("A verified payment covering this order is required")
    if order.product_id is None:
        raise InvalidState("The order has no product to deliver")
    await lock_manual_products(db, order.tenant_id, [order.product_id])
    free = await available_stock(db, order.product_id) - await reserved_auto_stock(
        db, order.tenant_id, order.product_id
    )
    if free < order.qty:
        raise OutOfStock("Not enough stock to deliver this order yet")
    before = snapshot(order)
    order.status = "paid"
    order.version += 1
    await add_event(
        db, order, "manual_review", "paid", actor_id=agent.id, reason="delivery_requeued"
    )
    db.add(
        Outbox(
            tenant_id=order.tenant_id,
            topic="order.paid",
            key=f"{order.id}:requeue:{order.version}",  # one event per requeue, never twice
            payload={"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
        )
    )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.delivery_requeued",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={"payment_id": str(pay.id), "reason": reason},
        severity="warning",
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="delivery_requeued",
        order_id=str(order.id),
    )
    await db.commit()
    return order, msg


HOLD_OUTCOMES = ("accept", "close")


async def hold_reason(db: AsyncSession, order: Order) -> tuple[str | None, str | None]:
    """Reason and actor kind of the latest transition into ``manual_review``."""
    ev = await db.scalar(
        select(OrderEvent)
        .where(
            OrderEvent.order_id == order.id,
            OrderEvent.tenant_id == order.tenant_id,
            OrderEvent.to_status == "manual_review",
        )
        .order_by(OrderEvent.id.desc())
        .limit(1)
    )
    return (ev.reason, ev.actor_kind) if ev is not None else (None, None)


async def _verified_payment(db: AsyncSession, order: Order) -> Payment:
    """Same evidence rule as ``requeue_delivery``: never infer payment from the status."""
    pay = await payment_for_order(db, order)
    if (
        pay is None
        or pay.status != "succeeded"
        or pay.currency != order.currency
        or pay.amount_minor < order.amount_minor
        or not await aftersales_svc._has_open_ledger(db, order)
    ):
        raise InvalidState("A verified payment covering this order is required")
    if order.paid_at is None:  # recorded after the hold (settle._record_unrecorded_payment)
        order.paid_at = pay.created_at
    return pay


async def release_hold(
    db: AsyncSession,
    res: Resources,
    order_id: uuid.UUID,
    outcome: str,
    reason: str,
    agent: User,
    ip: str | None,
) -> tuple[Order, Message | None]:
    """#72 B2: the exit from ``manual_review``.

    - ``accept``: a verified payment covers the order -> auto-card goes back on the delivery
      queue (``requeue_delivery``), anything else becomes ``paid`` (manual stock re-reserved
      under the product lock, refused if none is left) so staff can deliver it.
    - ``close``: only when NO payment is recorded for the payable AND a fresh gateway re-query
      of every attempt says unpaid / not found (anything unresolved -> 503, a paid answer is
      settled and the close refused) -> ``cancelled``, stock and promo released. A checkout
      closes as a whole, like a cancel.
    """
    clean = (reason or "").strip()
    if not clean:
        raise Validation("A reason is required")
    clean = clean[:200]
    if outcome == "accept":
        return await _accept_held(db, order_id, clean, agent, ip)
    if outcome == "close":
        return await _close_held(db, res, order_id, clean, agent, ip)
    raise Validation("Unknown outcome")


async def _accept_held(
    db: AsyncSession, order_id: uuid.UUID, reason: str, agent: User, ip: str | None
) -> tuple[Order, Message | None]:
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.status != "manual_review":
        raise InvalidState("Only an order held for review can be released")
    if order.kind == "reseller_signup":
        raise InvalidState("A held sign-up order cannot be released here; refund it instead")
    pay = await _verified_payment(db, order)
    if order.kind == "goods" and order.delivery_mode == "auto_card":
        return await requeue_delivery(db, order_id, agent, ip, reason=reason)
    held_reason, held_by = await hold_reason(db, order)
    before = snapshot(order)
    if (
        order.kind == "goods"
        and order.delivery_mode == "manual"
        and order.product_id is not None
        and order.stock_reserved == 0
    ):
        # released when the order expired before the (late) payment arrived
        order.stock_reserved = await reserve_manual_stock(
            db, order.tenant_id, order.product_id, order.qty
        )
    if held_by == "gateway" and order.product_id is not None:
        # a held settlement never counted the sale (settle._apply_one, ``late``)
        await apply_sold(db, {order.product_id: order.qty})
    order.status = "paid"
    order.version += 1
    await add_event(
        db, order, "manual_review", "paid", actor_id=agent.id, reason=f"hold_accepted: {reason}"
    )
    db.add(
        Outbox(
            tenant_id=order.tenant_id,
            topic="order.paid",
            key=f"{order.id}:release:{order.version}",
            payload={"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
        )
    )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.hold_released",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={
            "outcome": "accept",
            "reason": reason,
            "hold_reason": held_reason,
            "payment_id": str(pay.id),
        },
        severity="warning",
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="paid",
        text=(
            "Payment confirmed. An agent will deliver your order shortly."
            if order.delivery_mode == "manual"
            else None
        ),
        order_id=str(order.id),
        awaiting_delivery=order.delivery_mode == "manual",
    )
    await db.commit()
    return order, msg


async def _group_has_payment(db: AsyncSession, co: Checkout | None, orders: list[Order]) -> bool:
    cond = Payment.order_id.in_([o.id for o in orders])
    if co is not None:
        cond = or_(cond, Payment.checkout_id == co.id)
    return (
        await db.scalar(
            select(func.count(Payment.id)).where(Payment.tenant_id == orders[0].tenant_id, cond)
        )
        or 0
    ) > 0


async def _close_held(
    db: AsyncSession,
    res: Resources,
    order_id: uuid.UUID,
    reason: str,
    agent: User,
    ip: str | None,
) -> tuple[Order, Message | None]:
    from app.modules.payments.reconcile import emit_outcome

    actor_id, actor_role, tid = agent.id, agent.role, agent.tenant_id

    async def load() -> tuple[Checkout | None, list[Order]]:
        co, orders = await cancel_svc._load_group(db, tid, order_id, None)
        if (co is not None and co.status != "manual_review") or any(
            o.status != "manual_review" for o in orders
        ):
            raise InvalidState("Only an order held for review can be closed here")
        if await _group_has_payment(db, co, orders):
            raise InvalidState(
                "A payment is recorded for this order; accept it or refund it instead"
            )
        return co, orders

    co, orders = await load()
    no = co.checkout_no if co is not None else orders[0].order_no
    amount = co.amount_minor if co is not None else orders[0].amount_minor
    channel = co.channel if co is not None else orders[0].channel
    co_id = co.id if co is not None else None
    single_id = orders[0].id if co is None else None
    await db.rollback()  # never hold row locks across the gateway re-query
    found = await cancel_svc._settle_if_paid(db, res, co_id, single_id, no, amount, channel)
    if found is not None:
        await emit_outcome(res, db, found)
        raise AlreadyPaid("The gateway reports this order as paid; accept the payment instead")
    co, orders = await load()  # re-check under the locks: nothing may have landed meanwhile
    msgs: list[Message | None] = []
    held = {o.id: (await hold_reason(db, o))[0] for o in orders}
    for o in orders:
        before = snapshot(o)
        o.status = "cancelled"
        o.cancel_reason = reason
        o.version += 1
        await add_event(
            db, o, "manual_review", "cancelled", actor_id=actor_id, reason=f"hold_closed: {reason}"
        )
        await audit(
            db,
            tenant_id=tid,
            action="order.hold_released",
            actor_id=actor_id,
            actor_role=actor_role,
            target=f"order:{o.id}",
            before=before,
            after=snapshot(o),
            ip=ip,
            detail={"outcome": "close", "reason": reason, "hold_reason": held[o.id]},
            severity="warning",
        )
        msgs.append(
            await post_system(
                db,
                tenant_id=tid,
                conversation_id=o.conversation_id,
                event="order_cancelled",
                text=f"Order closed by support: {reason}",
                order_id=str(o.id),
                reason=reason,
                by="staff",
            )
        )
    await release_manual_stock(db, tid, orders)
    if co is not None:
        await promo_svc.release(
            db, checkout_id=co.id, tenant_id=tid, expect=co.promo_code_id is not None
        )
        co.status = "cancelled"
        await audit(
            db,
            tenant_id=tid,
            action="checkout.hold_closed",
            actor_id=actor_id,
            actor_role=actor_role,
            target=f"checkout:{co.id}",
            before={"status": "manual_review"},
            after={"status": "cancelled"},
            ip=ip,
            detail={"reason": reason, "orders": len(orders)},
        )
    else:
        await promo_svc.release(
            db, order_id=orders[0].id, tenant_id=tid, expect=orders[0].promo_code_id is not None
        )
    await db.commit()
    for o, m in zip(orders, msgs, strict=True):
        await broadcast_order(res, db, o, [m])
    target = next(o for o in orders if o.id == order_id)
    msg = next(m for o, m in zip(orders, msgs, strict=True) if o.id == order_id)
    return target, msg


async def refund_undelivered(
    db: AsyncSession,
    order_id: uuid.UUID,
    reference: str,
    note: str | None,
    agent: User,
    ip: str | None,
) -> tuple[Order, Message | None]:
    """v8 A.3: refund a paid-but-undelivered goods order (admin only, see the router). Same
    routine and reference rule as after-sales refunds."""
    ref = aftersales_svc.clean_reference(reference)
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.kind != "goods" or order.status not in aftersales_svc.UNDELIVERED:
        raise InvalidState("Only a paid, undelivered order can be refunded here")
    await aftersales_svc.refund_order(db, order, agent, ip, ref, note, source="admin")
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="order_refunded",
        order_id=str(order.id),
        reference=ref,
        note=(note or "").strip() or None,
    )
    await db.commit()
    return order, msg


async def cancel(
    db: AsyncSession, res: Resources, order_id: uuid.UUID, reason: str, agent: User, ip: str | None
) -> Order:
    """Staff "Close order": same rules as the buyer's cancel (whole checkout, gateway
    re-query first) with a required reason the buyer sees in chat."""
    _co, orders = await cancel_svc.cancel_group(
        db, res, order_id=order_id, actor=agent, by="staff", reason=reason, ip=ip
    )
    return next(o for o in orders if o.id == order_id)


async def admin_views(db: AsyncSession, orders: list[Order]) -> list[dict[str, Any]]:
    views = await order_views(db, orders, owner_id=None)
    buyer_ids = {o.buyer_id for o in orders}
    buyers = (
        {u.id: u for u in await db.scalars(select(User).where(User.id.in_(buyer_ids)))}
        if buyer_ids
        else {}
    )
    for o, v in zip(orders, views, strict=True):
        b = buyers.get(o.buyer_id)
        v["buyer"] = {
            "id": str(o.buyer_id),
            "email": b.email if b else "",
            "display_name": b.display_name if b else "",
        }
        v["source"] = o.source
        v["reseller_markup_minor"] = o.reseller_markup_minor
    return views


async def list_orders(
    db: AsyncSession,
    page: Page,
    status: str | None,
    q: str | None,
    tenant_id: uuid.UUID,
    buyer_id: uuid.UUID | None = None,
    reseller_id: uuid.UUID | None = None,
    overdue_manual: bool = False,
    channel: str | None = None,
) -> dict[str, Any]:
    stmt = select(Order).where(Order.tenant_id == tenant_id)
    if channel == "none":  # no payment method chosen yet
        stmt = stmt.where(Order.channel.is_(None))
    elif channel:
        stmt = stmt.where(Order.channel == channel)
    if overdue_manual:  # manual orders still awaiting delivery past paid_at + ETA
        default_eta = section(await tenant_settings(db, tenant_id), "delivery")["manual_eta_hours"]
        eta = func.coalesce(
            select(Product.delivery_eta_hours)
            .where(Product.id == Order.product_id, Product.tenant_id == tenant_id)
            .scalar_subquery(),
            default_eta,
        )
        stmt = stmt.where(
            Order.delivery_mode == "manual",
            Order.status == "paid",
            Order.paid_at.is_not(None),
            Order.paid_at + func.make_interval(0, 0, 0, 0, eta, 0, 0) < func.now(),
        )
    if buyer_id is not None:
        stmt = stmt.where(Order.buyer_id == buyer_id)
    if reseller_id is not None:
        stmt = stmt.where(Order.reseller_id == reseller_id)
    if status:
        stmt = stmt.where(Order.status == status)
    if q and q.strip():
        like = f"%{escape_like(q.strip().lower())}%"
        stmt = stmt.join(User, User.id == Order.buyer_id).where(
            or_(
                func.lower(Order.order_no).like(like, escape="\\"),
                func.lower(Order.title).like(like, escape="\\"),
                Order.checkout_id.in_(
                    select(Checkout.id).where(
                        Checkout.tenant_id == tenant_id,
                        func.lower(Checkout.checkout_no).like(like, escape="\\"),
                    )
                ),
                User.email.like(like, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Order.created_at.desc(), Order.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    return page.wrap(await admin_views(db, rows), int(total))


async def order_detail(
    db: AsyncSession, order_id: uuid.UUID, tenant_id: uuid.UUID
) -> dict[str, Any]:
    order = await db.scalar(select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id))
    if order is None:
        raise NotFound("Order not found")
    (view,) = await admin_views(db, [order])
    product = await db.get(Product, order.product_id) if order.product_id else None
    view["floor_price"] = fmt(product.floor_price_minor) if product else None  # per unit
    if order.checkout_id is not None:
        siblings = list(
            await db.scalars(
                select(Order)
                .where(Order.checkout_id == order.checkout_id, Order.tenant_id == order.tenant_id)
                .order_by(Order.created_at, Order.id)
            )
        )
        view["checkout_orders"] = [
            {
                "id": str(o.id),
                "order_no": o.order_no,
                "title": o.title,
                "amount": fmt(o.amount_minor),
                "status": o.status,
            }
            for o in siblings
        ]
    refs = [order.order_no]
    if order.checkout_id is not None:
        co_no = await db.scalar(
            select(Checkout.checkout_no).where(
                Checkout.id == order.checkout_id, Checkout.tenant_id == order.tenant_id
            )
        )
        if co_no:
            refs.append(co_no)
    # an attempt whose outcome is unknown blocks pay/cancel until staff resolve it (v9 §1.3)
    unresolved = await db.scalars(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.tenant_id == order.tenant_id,
            PaymentAttempt.local_ref.in_(refs),
            PaymentAttempt.status.in_(("unknown", "preparing")),
        )
        .order_by(PaymentAttempt.created_at)
        .limit(10)
    )
    view["unresolved_attempts"] = [
        {"id": str(a.id), "status": a.status, "created_at": iso(a.created_at)} for a in unresolved
    ]
    pay = await payment_for_order(db, order)
    if order.status == "manual_review":
        # #72 B2: why it is held and which exit applies (accept needs a recorded payment)
        reason, by = await hold_reason(db, order)
        view["hold"] = {"reason": reason, "by": by, "has_payment": pay is not None}
    view["payment"] = (
        {
            "channel": pay.channel,
            "trade_no": pay.gateway_trade_no,
            "amount": fmt(pay.amount_minor),
            "currency": pay.currency,
            "scope": "checkout" if pay.checkout_id else "order",
            "at": iso(pay.created_at),
        }
        if pay
        else None
    )
    events = list(
        await db.scalars(
            select(OrderEvent).where(OrderEvent.order_id == order.id).order_by(OrderEvent.id)
        )
    )
    actor_ids = {e.actor_id for e in events if e.actor_id}
    actors = (
        {u.id: u for u in await db.scalars(select(User).where(User.id.in_(actor_ids)))}
        if actor_ids
        else {}
    )
    view["events"] = [
        {
            "from": e.from_status,
            "to": e.to_status,
            "actor": (
                actors[e.actor_id].display_name
                if e.actor_id and e.actor_id in actors
                else e.actor_kind
            ),
            "reason": e.reason,
            "at": iso(e.created_at),
        }
        for e in events
    ]
    return view
