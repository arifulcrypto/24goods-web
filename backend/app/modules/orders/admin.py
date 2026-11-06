"""Staff-side order operations: price change, manual delivery, cancel, listing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import InvalidState, NotFound, Validation
from app.core.money import MoneyError, fmt, parse_amount
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.models import Attachment, Checkout, Message, Order, OrderEvent, Product, User
from app.modules.catalog.service import escape_like
from app.modules.chat.messages import post_system
from app.modules.common.sitedefaults import delivery_deadlines
from app.modules.common.views import iso
from app.modules.content.site import tenant_settings
from app.modules.orders import cancel as cancel_svc
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import order_views
from app.modules.payments.channels import split_amount
from app.modules.payments.settle import payment_for_order


async def lock_order(db: AsyncSession, order_id: uuid.UUID, tenant_id: uuid.UUID) -> Order:
    order = await db.scalar(
        select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id).with_for_update()
    )
    if order is None:
        raise NotFound("Order not found")
    return order


async def change_price(
    db: AsyncSession, order_id: uuid.UUID, amount: str, reason: str, agent: User, ip: str | None
) -> tuple[Order, Message | None]:
    try:
        new_minor = parse_amount(amount)
    except MoneyError as exc:
        raise Validation(f"amount: {exc}") from None
    order = await lock_order(db, order_id, agent.tenant_id)
    if order.status != "pending_payment":
        raise InvalidState("Price can only change while awaiting payment")
    if order.checkout_id is not None:
        raise InvalidState("This order is part of a cart checkout; close the checkout instead")
    if order.product_id is not None:
        product = await db.get(Product, order.product_id)
        if product is not None and new_minor < product.floor_price_minor * order.qty:
            raise Validation("amount is below the floor price")
    before = snapshot(order)
    old_minor = order.amount_minor
    # staff set the new SUBTOTAL; the channel fee is recomputed at the rate the order carries
    fee, total = split_amount(new_minor, order.fee_permille)
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
        detail={"reason": reason},
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
    until, confirm_at = delivery_deadlines(
        now,
        mode=order.delivery_mode,
        product_hours=product.after_sales_hours if product else None,
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
    return views


async def list_orders(
    db: AsyncSession,
    page: Page,
    status: str | None,
    q: str | None,
    tenant_id: uuid.UUID,
    buyer_id: uuid.UUID | None = None,
    overdue_manual: bool = False,
) -> dict[str, Any]:
    stmt = select(Order).where(Order.tenant_id == tenant_id)
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
    pay = await payment_for_order(db, order)
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
