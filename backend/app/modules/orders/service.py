from __future__ import annotations

import base64
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import audit
from app.core.config import Settings
from app.core.db import tenant_session
from app.core.deps import Page
from app.core.errors import (
    InvalidState,
    NotFound,
    OutOfStock,
    RateLimited,
    Unavailable,
)
from app.core.i18n import pick
from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import (
    Attachment,
    CardStock,
    Conversation,
    Message,
    Order,
    Quote,
    Tenant,
    User,
)
from app.modules.catalog.service import get_active_product
from app.modules.chat import service as chat
from app.modules.chat.messages import insert_message, post_system
from app.modules.common.views import REVEALABLE, order_card_payload, order_view
from app.modules.orders.events import add_event, snapshot
from app.modules.realtime.publish import publish_order

log = logging.getLogger("app.orders")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_order_no() -> str:
    """ "G" + 12 base32 chars (60 random bits): non-sequential, not enumerable."""
    return "G" + base64.b32encode(secrets.token_bytes(8)).decode()[:12]


async def available_stock(db: AsyncSession, product_id: uuid.UUID) -> int:
    n = await db.scalar(
        select(func.count(CardStock.id)).where(
            CardStock.product_id == product_id, CardStock.status == "available"
        )
    )
    return int(n or 0)


async def create_order(
    db: AsyncSession,
    *,
    settings: Settings,
    buyer: User,
    title: str,
    amount_minor: int,
    currency: str,
    qty: int,
    delivery_mode: str,
    product_id: uuid.UUID | None,
    quote_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    ip: str | None,
) -> tuple[Order, Message | None]:
    order = Order(
        tenant_id=buyer.tenant_id,
        order_no=new_order_no(),
        buyer_id=buyer.id,
        product_id=product_id,
        quote_id=quote_id,
        conversation_id=conversation_id,
        title=title[:200],
        qty=qty,
        amount_minor=amount_minor,
        currency=currency,
        status="pending_payment",
        version=1,
        delivery_mode=delivery_mode,
        expires_at=utcnow() + timedelta(minutes=settings.order_ttl_minutes),
    )
    db.add(order)
    await db.flush()
    await add_event(db, order, None, "pending_payment", actor_id=buyer.id, reason="created")
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.created",
        actor_id=buyer.id,
        actor_role=buyer.role,
        target=f"order:{order.id}",
        after=snapshot(order),
        ip=ip,
        detail={"quote_id": str(quote_id) if quote_id else None},
    )
    msg = None
    if conversation_id is not None:
        msg, _ = await insert_message(
            db,
            tenant_id=order.tenant_id,
            conversation_id=conversation_id,
            sender_id=None,
            sender_role="system",
            type="order_card",
            payload=order_card_payload(order),
        )
    return order, msg


async def buy_now(
    db: AsyncSession,
    settings: Settings,
    buyer: User,
    product_id: uuid.UUID,
    qty: int,
    ip: str | None,
) -> tuple[Order, Message | None]:
    product = await get_active_product(db, product_id)
    if product.delivery_mode == "auto_card" and await available_stock(db, product.id) < qty:
        raise OutOfStock("Not enough stock")
    conv = await chat.open_conversation_for(db, buyer)
    order, msg = await create_order(
        db,
        settings=settings,
        buyer=buyer,
        title=pick(product.title_i18n, buyer.locale),
        amount_minor=product.price_minor * qty,
        currency=product.currency,
        qty=qty,
        delivery_mode=product.delivery_mode,
        product_id=product.id,
        quote_id=None,
        conversation_id=conv.id if conv else None,
        ip=ip,
    )
    await db.commit()
    return order, msg


def _parse_ref(ref: str) -> tuple[uuid.UUID | None, str | None]:
    try:
        return uuid.UUID(ref), None
    except ValueError:
        if len(ref) == 13 and ref.startswith("G") and ref[1:].isalnum():
            return None, ref.upper()
        raise NotFound("Order not found") from None


async def get_owned(db: AsyncSession, ref: str, user: User, *, lock: bool = False) -> Order:
    oid, order_no = _parse_ref(ref)
    stmt = select(Order).where(Order.id == oid if oid else Order.order_no == order_no)
    if lock:
        stmt = stmt.with_for_update()
    order = await db.scalar(stmt)
    if order is None or order.buyer_id != user.id:
        raise NotFound("Order not found")
    return order


async def order_views(
    db: AsyncSession, orders: list[Order], *, owner_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    att_ids = {o.manual_delivery_attachment_id for o in orders if o.manual_delivery_attachment_id}
    atts: dict[uuid.UUID, Attachment] = {}
    if att_ids:
        atts = {
            a.id: a for a in await db.scalars(select(Attachment).where(Attachment.id.in_(att_ids)))
        }
    return [
        order_view(
            o,
            viewer_is_owner=owner_id is not None and o.buyer_id == owner_id,
            attachment=atts.get(o.manual_delivery_attachment_id)
            if o.manual_delivery_attachment_id
            else None,
        )
        for o in orders
    ]


async def one_view(db: AsyncSession, order: Order, owner_id: uuid.UUID | None) -> dict[str, Any]:
    return (await order_views(db, [order], owner_id=owner_id))[0]


async def broadcast_order(
    res: Resources, db: AsyncSession, order: Order, msgs: list[Message] | None = None
) -> None:
    """Publish the order (+ any chat messages) to buyer and staff. Call after commit."""
    await db.refresh(order)
    bview = await one_view(db, order, order.buyer_id)
    sview = await one_view(db, order, None)
    await publish_order(res.redis, order.tenant_id, order.buyer_id, bview, sview)
    msgs = [m for m in (msgs or []) if m is not None]
    if msgs and order.conversation_id:
        conv = await db.get(Conversation, order.conversation_id)
        if conv is not None:
            await chat.broadcast(res, db, conv, msgs)


async def list_own(db: AsyncSession, user: User, page: Page) -> dict[str, Any]:
    base = select(Order).where(Order.buyer_id == user.id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        await db.scalars(
            base.order_by(Order.created_at.desc(), Order.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    return page.wrap(await order_views(db, rows, owner_id=user.id), int(total))


async def pay_url(db: AsyncSession, res: Resources, ref: str, user: User, channel: str) -> str:
    order = await get_owned(db, ref, user, lock=True)
    if order.status != "pending_payment":
        raise InvalidState("Order is not awaiting payment")
    if order.expires_at is not None and order.expires_at <= utcnow():
        raise InvalidState("Order has expired")
    if not (res.settings.zpay_pid and res.settings.zpay_key):
        raise Unavailable("Payment channel not configured")
    order.channel = channel
    url = res.zpay.build_pay_url(order.order_no, order.title, to_decimal(order.amount_minor))
    await db.commit()
    return url


async def confirm(
    db: AsyncSession, ref: str, user: User, ip: str | None
) -> tuple[Order, Message | None]:
    order = await get_owned(db, ref, user, lock=True)
    if order.status != "delivered":
        raise InvalidState("Only delivered orders can be confirmed")
    before = snapshot(order)
    order.status = "completed"
    order.completed_at = utcnow()
    order.version += 1
    await add_event(db, order, "delivered", "completed", actor_id=user.id, reason="buyer_confirmed")
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.completed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="completed",
        order_id=str(order.id),
    )
    await db.commit()
    return order, msg


async def reveal(res: Resources, order: Order, user: User) -> list[str]:
    if not (
        order.buyer_id == user.id
        and order.delivery_mode == "auto_card"
        and order.status in REVEALABLE
    ):
        raise InvalidState("Nothing to reveal for this order")
    token = res.settings.internal_token
    if not token:
        raise Unavailable("Delivery service not configured")
    try:
        resp = await res.delivery.post(
            "/internal/reveal",
            json={"order_id": str(order.id), "buyer_id": str(user.id)},
            headers={"X-Internal-Token": token},
        )
    except httpx.HTTPError as exc:
        log.error("delivery service unreachable error=%s", type(exc).__name__)
        raise Unavailable("Delivery service unavailable") from None
    if resp.status_code == 200:
        secrets_ = resp.json().get("secrets")
        if not isinstance(secrets_, list):
            raise Unavailable("Delivery service error")
        return [str(s) for s in secrets_]
    if resp.status_code == 429:
        raise RateLimited(retry_after=int(resp.headers.get("Retry-After", "3600")))
    if resp.status_code in (403, 404):
        raise NotFound("Order not found")
    if resp.status_code == 409:
        raise InvalidState("Nothing to reveal for this order")
    raise Unavailable("Delivery service unavailable")


async def expire_pending(
    maker: async_sessionmaker[AsyncSession], res: Resources | None = None
) -> int:
    """Expire pending orders past ``expires_at`` and offered quotes past theirs."""
    async with tenant_session(maker, None) as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = 0
    for tid in tenant_ids:
        async with tenant_session(maker, tid) as db:
            now = utcnow()
            rows = list(
                await db.scalars(
                    select(Order)
                    .where(Order.status == "pending_payment", Order.expires_at < now)
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            )
            msgs: dict[uuid.UUID, Message | None] = {}
            for order in rows:
                before = snapshot(order)
                order.status = "expired"
                order.version += 1
                await add_event(
                    db,
                    order,
                    "pending_payment",
                    "expired",
                    actor_kind="system",
                    reason="payment_timeout",
                )
                await audit(
                    db,
                    tenant_id=tid,
                    action="order.expired",
                    target=f"order:{order.id}",
                    before=before,
                    after=snapshot(order),
                )
                msgs[order.id] = await post_system(
                    db,
                    tenant_id=tid,
                    conversation_id=order.conversation_id,
                    event="expired",
                    order_id=str(order.id),
                )
            await db.execute(
                update(Quote)
                .where(Quote.status == "offered", Quote.expires_at < now)
                .values(status="expired", updated_at=func.now())
            )
            await db.commit()
            total += len(rows)
            if res is not None:
                for order in rows:
                    await broadcast_order(res, db, order, [msgs.get(order.id)])
    return total
