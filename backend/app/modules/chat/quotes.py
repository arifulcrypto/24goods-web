"""Signed, versioned price quotes (Xianyu-style negotiation, architecture §8)."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import Conflict, InvalidState, NotFound, OutOfStock, QuoteExpired, Validation
from app.core.money import MoneyError, fmt, parse_amount
from app.models import Conversation, Message, Order, Product, Quote, User
from app.modules.chat.messages import insert_message, post_system
from app.modules.chat.schemas import QuoteIn
from app.modules.common.views import iso
from app.modules.orders import service as orders

MAX_AUTO_CARD_QTY = 10  # == buy-now qty cap


def canonical(q: Quote) -> bytes:
    fields = [
        "v1",
        str(q.id),
        str(q.tenant_id),
        str(q.conversation_id),
        str(q.product_id or ""),
        q.title,
        str(q.amount_minor),
        q.currency,
        str(q.qty),
        str(int(q.expires_at.timestamp())),
        str(q.version),
    ]
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()


def sign_quote(key: bytes, q: Quote) -> str:
    return hmac.new(key, canonical(q), hashlib.sha256).hexdigest()


def verify_quote(key: bytes, q: Quote) -> bool:
    return hmac.compare_digest(q.signature or "", sign_quote(key, q))


def quote_payload(q: Quote) -> dict[str, Any]:
    return {
        "quote_id": str(q.id),
        "title": q.title,
        "amount": fmt(q.amount_minor),
        "currency": q.currency,
        "qty": q.qty,
        "note": q.note,
        "status": q.status,
        "expires_at": iso(q.expires_at),
        "order_id": str(q.order_id) if q.order_id else None,
    }


async def create_quote(
    db: AsyncSession,
    settings: Settings,
    conv: Conversation,
    agent: User,
    body: QuoteIn,
    ip: str | None,
) -> tuple[Quote, Message]:
    if conv.status != "open":
        raise InvalidState("Conversation is closed")
    try:
        amount = parse_amount(body.amount)
    except MoneyError as exc:
        raise Validation(f"amount: {exc}") from None
    product: Product | None = None
    if body.product_id is not None:
        product = await db.get(Product, body.product_id)
        if product is None:
            raise NotFound("Product not found")
        if product.delivery_mode == "auto_card" and body.qty > MAX_AUTO_CARD_QTY:
            # same cap as buy-now: every delivered order must stay revealable in one go
            raise Validation(f"qty must be at most {MAX_AUTO_CARD_QTY} for auto_card products")
        if amount < product.floor_price_minor * body.qty:
            raise Validation("amount is below the product floor price")
    q = Quote(
        id=uuid.uuid4(),
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        product_id=product.id if product else None,
        created_by=agent.id,
        title=body.title.strip(),
        note=(body.note or "").strip() or None,
        amount_minor=amount,
        currency=product.currency if product else "CNY",
        qty=body.qty,
        status="offered",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=body.expires_in_minutes)
        ).replace(microsecond=0),
        version=1,
    )
    q.signature = sign_quote(settings.quote_key(), q)
    db.add(q)
    await db.flush()
    msg, _ = await insert_message(
        db,
        tenant_id=conv.tenant_id,
        conversation_id=conv.id,
        sender_id=agent.id,
        sender_role="agent",
        type="quote_card",
        payload=quote_payload(q),
    )
    q.message_id = msg.id
    await audit(
        db,
        tenant_id=conv.tenant_id,
        action="quote.offered",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"quote:{q.id}",
        ip=ip,
        after={"amount_minor": q.amount_minor, "qty": q.qty, "expires_at": iso(q.expires_at)},
    )
    await db.commit()
    return q, msg


async def _locked_quote(db: AsyncSession, quote_id: uuid.UUID) -> Quote:
    q = await db.scalar(select(Quote).where(Quote.id == quote_id).with_for_update())
    if q is None:
        raise NotFound("Quote not found")
    return q


async def withdraw_quote(
    db: AsyncSession, settings: Settings, quote_id: uuid.UUID, agent: User, ip: str | None
) -> tuple[Quote, Message | None]:
    q = await _locked_quote(db, quote_id)
    if q.status != "offered":
        raise InvalidState("Only offered quotes can be withdrawn")
    q.status = "withdrawn"
    q.version += 1
    q.signature = sign_quote(settings.quote_key(), q)
    await audit(
        db,
        tenant_id=q.tenant_id,
        action="quote.withdrawn",
        actor_id=agent.id,
        actor_role=agent.role,
        target=f"quote:{q.id}",
        ip=ip,
    )
    sysmsg = await post_system(
        db,
        tenant_id=q.tenant_id,
        conversation_id=q.conversation_id,
        event="quote_withdrawn",
        quote_id=str(q.id),
    )
    await db.commit()
    return q, sysmsg


async def accept_quote(
    db: AsyncSession, settings: Settings, quote_id: uuid.UUID, buyer: User, ip: str | None
) -> tuple[Order, Quote, list[Message]]:
    """One transaction: lock quote -> checks (owner, status, expiry, HMAC) -> order."""
    q = await _locked_quote(db, quote_id)
    conv = await db.get(Conversation, q.conversation_id)
    if conv is None or conv.buyer_id != buyer.id:
        raise NotFound("Quote not found")
    if q.status != "offered":
        raise InvalidState("Quote is no longer available")
    if q.expires_at <= datetime.now(timezone.utc):
        q.status = "expired"
        await db.commit()
        raise QuoteExpired("Quote has expired")
    if not verify_quote(settings.quote_key(), q):
        # rollback() expires every loaded instance: capture what the audit row needs first.
        tenant_id, qid, buyer_id, buyer_role = q.tenant_id, q.id, buyer.id, buyer.role
        await db.rollback()
        await audit(
            db,
            tenant_id=tenant_id,
            action="quote.signature_invalid",
            actor_id=buyer_id,
            actor_role=buyer_role,
            target=f"quote:{qid}",
            ip=ip,
            severity="critical",
        )
        await db.commit()
        raise Conflict("Quote integrity check failed")
    product = await db.get(Product, q.product_id) if q.product_id else None
    delivery_mode = product.delivery_mode if product else "manual"
    if delivery_mode == "auto_card" and q.qty > MAX_AUTO_CARD_QTY:
        raise InvalidState("Quote quantity exceeds the auto-delivery limit")
    if (
        product is not None
        and delivery_mode == "auto_card"
        and await orders.available_stock(db, product.id) < q.qty
    ):
        raise OutOfStock("Not enough stock")
    order, msg = await orders.create_order(
        db,
        settings=settings,
        buyer=buyer,
        title=q.title,
        amount_minor=q.amount_minor,
        currency=q.currency,
        qty=q.qty,
        delivery_mode=delivery_mode,
        product_id=q.product_id,
        quote_id=q.id,
        conversation_id=conv.id,
        ip=ip,
        # attribution only: a quote is a negotiated price, no markup and no promo (v5)
        reseller_id=conv.reseller_id,
        reseller_markup_minor=0,
    )
    q.status = "accepted"
    q.order_id = order.id
    q.version += 1
    q.signature = sign_quote(settings.quote_key(), q)
    accepted = await post_system(
        db,
        tenant_id=q.tenant_id,
        conversation_id=conv.id,
        event="quote_accepted",
        quote_id=str(q.id),
        order_id=str(order.id),
    )
    await audit(
        db,
        tenant_id=q.tenant_id,
        action="quote.accepted",
        actor_id=buyer.id,
        actor_role=buyer.role,
        target=f"quote:{q.id}",
        ip=ip,
        detail={"order_id": str(order.id)},
    )
    await db.commit()
    return order, q, [m for m in (msg, accepted) if m is not None]


async def quote_message(db: AsyncSession, q: Quote) -> Message | None:
    return await db.get(Message, q.message_id) if q.message_id else None
