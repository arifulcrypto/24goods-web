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
from app.models import Attachment, Message, Order, OrderEvent, Product, User
from app.modules.catalog.service import escape_like
from app.modules.chat.messages import post_system
from app.modules.common.views import iso
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import order_views


async def lock_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
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
    order = await lock_order(db, order_id)
    if order.status != "pending_payment":
        raise InvalidState("Price can only change while awaiting payment")
    if order.product_id is not None:
        product = await db.get(Product, order.product_id)
        if product is not None and new_minor < product.floor_price_minor * order.qty:
            raise Validation("amount is below the floor price")
    before = snapshot(order)
    old_minor = order.amount_minor
    order.amount_minor = new_minor
    order.version += 1
    order.channel = None  # previously issued pay URLs no longer match (-> manual_review)
    await add_event(
        db,
        order,
        order.status,
        order.status,
        actor_id=agent.id,
        reason=f"price_changed {fmt(old_minor)} -> {fmt(new_minor)}: {reason}"[:500],
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
        old_amount=fmt(old_minor),
        new_amount=fmt(new_minor),
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
    order = await lock_order(db, order_id)
    if order.delivery_mode != "manual" or order.status != "paid":
        raise InvalidState("Only paid manual orders can be delivered")
    if attachment_id is not None:
        att = await db.get(Attachment, attachment_id)
        if att is None:
            raise NotFound("Attachment not found")
        if att.owner_id != agent.id and agent.role != "admin":
            raise NotFound("Attachment not found")
    before = snapshot(order)
    order.status = "delivered"
    order.delivered_at = datetime.now(timezone.utc)
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
    )
    await db.commit()
    return order, msg


async def cancel(
    db: AsyncSession, order_id: uuid.UUID, reason: str, agent: User, ip: str | None
) -> tuple[Order, Message | None]:
    order = await lock_order(db, order_id)
    if order.status != "pending_payment":
        raise InvalidState("Only orders awaiting payment can be cancelled")
    before = snapshot(order)
    order.status = "cancelled"
    order.version += 1
    await add_event(db, order, "pending_payment", "cancelled", actor_id=agent.id, reason=reason)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.cancelled",
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
        event="cancelled",
        order_id=str(order.id),
        reason=reason,
    )
    await db.commit()
    return order, msg


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
    return views


async def list_orders(
    db: AsyncSession, page: Page, status: str | None, q: str | None
) -> dict[str, Any]:
    stmt = select(Order)
    if status:
        stmt = stmt.where(Order.status == status)
    if q and q.strip():
        like = f"%{escape_like(q.strip().lower())}%"
        stmt = stmt.join(User, User.id == Order.buyer_id).where(
            or_(
                func.lower(Order.order_no).like(like, escape="\\"),
                func.lower(Order.title).like(like, escape="\\"),
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


async def order_detail(db: AsyncSession, order_id: uuid.UUID) -> dict[str, Any]:
    order = await db.get(Order, order_id)
    if order is None:
        raise NotFound("Order not found")
    (view,) = await admin_views(db, [order])
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
