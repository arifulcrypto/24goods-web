from __future__ import annotations

import base64
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import accesscode
from app.core.audit import audit
from app.core.config import Settings
from app.core.db import tenant_session
from app.core.deps import Page
from app.core.errors import (
    Conflict,
    InvalidState,
    NotFound,
    OutOfStock,
    RateLimited,
    Unavailable,
)
from app.core.i18n import pick
from app.core.money import to_decimal
from app.core.ratelimit import release, reserve
from app.core.resources import Resources
from app.models import (
    CardStock,
    Conversation,
    Message,
    Order,
    Quote,
    Tenant,
    User,
)
from app.modules.catalog.service import claimable, get_active_product
from app.modules.chat import service as chat
from app.modules.chat.messages import insert_message, order_card_messages, post_system
from app.modules.common.orderviews import latest_tickets, one_view, order_views  # noqa: F401
from app.modules.common.views import REVEALABLE, order_card_payload
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
        select(func.count(CardStock.id)).where(CardStock.product_id == product_id, *claimable())
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
    source: str | None = None,
) -> tuple[Order, Message | None]:
    """Create a pending order. The one-time plaintext access code is left on
    ``order.access_code_plain`` (never persisted, never logged)."""
    # Serialize order creation per buyer (row lock), then cap open unpaid orders.
    await db.execute(select(User.id).where(User.id == buyer.id).with_for_update())
    pending = await db.scalar(
        select(func.count(Order.id)).where(
            Order.buyer_id == buyer.id, Order.status == "pending_payment"
        )
    )
    cap = settings.max_pending_orders_instant if buyer.is_instant else settings.max_pending_orders
    if int(pending or 0) >= cap:
        raise Conflict(f"At most {cap} unpaid orders; pay or wait for one to expire")
    order_no = new_order_no()
    code = accesscode.generate()
    order = Order(
        tenant_id=buyer.tenant_id,
        order_no=order_no,
        access_code_hash=accesscode.digest(settings.lookup_secret(), order_no, code),
        source=source,
        buyer_id=buyer.id,
        product_id=product_id,
        quote_id=quote_id,
        conversation_id=conversation_id,
        title=title[:200],
        qty=qty,
        amount_minor=amount_minor,
        subtotal_minor=amount_minor,  # v4 base: fee split lands with the checkout module
        currency=currency,
        status="pending_payment",
        version=1,
        delivery_mode=delivery_mode,
        expires_at=utcnow() + timedelta(minutes=settings.order_ttl_minutes),
    )
    db.add(order)
    await db.flush()
    order.access_code_plain = code  # type: ignore[attr-defined]
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
        detail={"quote_id": str(quote_id) if quote_id else None, "source": source},
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
    source: str | None = None,
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
        source=source,
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


async def broadcast_order(
    res: Resources, db: AsyncSession, order: Order, msgs: list[Message] | None = None
) -> None:
    """Publish the order (+ any chat messages) to buyer and staff. Call after commit."""
    await db.refresh(order)
    buyer = await db.get(User, order.buyer_id)
    bview = await one_view(db, order, order.buyer_id, buyer.locale if buyer else "en")
    sview = await one_view(db, order, None, "en")
    await publish_order(res.redis, order.tenant_id, order.buyer_id, bview, sview)
    if order.conversation_id is None:
        return
    # Re-publish the order_card (same message id, fresh status/amount) + any new messages.
    cards = await order_card_messages(db, order.conversation_id, order.id)
    seen: set[int] = set()
    out: list[Message] = []
    for m in [*cards, *(msgs or [])]:
        if m is not None and m.id not in seen:
            seen.add(m.id)
            out.append(m)
    conv = await db.get(Conversation, order.conversation_id)
    if conv is not None:
        await chat.broadcast(res, db, conv, out)


async def list_own(db: AsyncSession, user: User, page: Page, lang: str = "en") -> dict[str, Any]:
    base = select(Order).where(Order.buyer_id == user.id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        await db.scalars(
            base.order_by(Order.created_at.desc(), Order.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    return page.wrap(await order_views(db, rows, owner_id=user.id, lang=lang), int(total))


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


async def rotate_access_code(
    db: AsyncSession, settings: Settings, ref: str, user: User, ip: str | None
) -> str:
    order = await get_owned(db, ref, user, lock=True)
    code = accesscode.generate()
    order.access_code_hash = accesscode.digest(settings.lookup_secret(), order.order_no, code)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.access_code_rotated",
        actor_id=user.id,
        actor_role=user.role,
        target=f"order:{order.id}",
        ip=ip,
    )
    await db.commit()
    return code


async def reveal(res: Resources, order: Order, user: User) -> list[dict[str, str]]:
    """Reveal an order's codes for its buyer. Returns ``[{id, code}]``."""
    return await reveal_for(res, order, user.id)


async def reveal_for(res: Resources, order: Order, buyer_id: uuid.UUID) -> list[dict[str, str]]:
    user = SimpleNamespace(id=buyer_id)
    if not (
        order.buyer_id == buyer_id
        and order.delivery_mode == "auto_card"
        and order.status in REVEALABLE
    ):
        raise InvalidState("Nothing to reveal for this order")
    token = res.settings.internal_token
    if not token:
        raise Unavailable("Delivery service not configured")
    s = res.settings
    ok, reset = await reserve(
        res.redis,
        "reveal:buyer:api",
        str(user.id),
        order.qty,
        s.reveal_per_buyer_cards,
        s.reveal_per_buyer_window_s,
    )
    if not ok:
        raise RateLimited(retry_after=reset, message="Too many codes revealed recently")
    try:
        return await _call_reveal(res, order, user, token)
    except Exception:
        # nothing was revealed: give the buyer's budget back
        await release(
            res.redis, "reveal:buyer:api", str(user.id), order.qty, s.reveal_per_buyer_window_s
        )
        raise


async def _call_reveal(
    res: Resources, order: Order, user: SimpleNamespace | User, token: str
) -> list[dict[str, str]]:
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
        data = resp.json()
        cards = data.get("cards")
        if not isinstance(cards, list):
            raise Unavailable("Delivery service error")
        return [{"id": str(c["id"]), "code": str(c["code"])} for c in cards]
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
            expired_quotes = (
                await db.execute(
                    update(Quote)
                    .where(Quote.status == "offered", Quote.expires_at < now)
                    .values(status="expired", updated_at=func.now())
                    .returning(Quote.conversation_id, Quote.message_id)
                )
            ).all()
            await db.commit()
            total += len(rows)
            if res is not None:
                for order in rows:
                    await broadcast_order(res, db, order, [msgs.get(order.id)])
                # quote cards: same message id re-published with status "expired"
                for conv_id, message_id in expired_quotes:
                    conv = await db.get(Conversation, conv_id)
                    card = await db.get(Message, message_id) if message_id else None
                    if conv is not None and card is not None:
                        await chat.broadcast(res, db, conv, [card])
    return total
