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
    FeeChannelMismatch,
    InvalidState,
    NotFound,
    OutOfStock,
    PayViaCheckout,
    RateLimited,
    Unavailable,
    Validation,
)
from app.core.features import require_feature
from app.core.i18n import pick
from app.core.money import to_decimal
from app.core.ratelimit import release, reserve
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.core.storefront import Storefront
from app.models import (
    CardStock,
    Checkout,
    Conversation,
    Message,
    Order,
    Product,
    Quote,
    Tenant,
    User,
)
from app.modules.catalog.service import claimable, get_active_product
from app.modules.chat import service as chat
from app.modules.chat.messages import (
    ensure_open_conversation,
    insert_message,
    order_card_messages,
    post_system,
)
from app.modules.common.buyerinput import chat_text, normalize_value
from app.modules.common.orderviews import latest_tickets, one_view, order_views  # noqa: F401
from app.modules.common.views import REVEALABLE, order_card_payload
from app.modules.content.site import tenant_settings
from app.modules.orders.events import add_event, snapshot
from app.modules.payments import usdt as usdt_svc
from app.modules.payments.channels import channel_cfg, entry_cfg, is_usdt, split_amount
from app.modules.promo import service as promo_svc
from app.modules.promo.service import PromoError, PromoLine
from app.modules.realtime.publish import publish_order
from app.modules.reseller.pricing import Pricer

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


async def count_payables(db: AsyncSession, buyer_id: uuid.UUID) -> int:
    """Open payables: pending single orders plus pending checkouts (a checkout's member
    orders do not count on their own)."""
    singles = await db.scalar(
        select(func.count(Order.id)).where(
            Order.buyer_id == buyer_id,
            Order.status == "pending_payment",
            Order.checkout_id.is_(None),
        )
    )
    checkouts = await db.scalar(
        select(func.count(Checkout.id)).where(
            Checkout.buyer_id == buyer_id, Checkout.status == "pending_payment"
        )
    )
    return int(singles or 0) + int(checkouts or 0)


async def tenant_order_limits(
    db: AsyncSession, settings: Settings, tenant_id: uuid.UUID
) -> tuple[int, int]:
    """(max pending orders, order timeout minutes): the tenant's Settings -> User defaults
    when it set them, else the process settings."""
    ts = await tenant_settings(db, tenant_id)
    explicit = ts.get("defaults") if isinstance(ts.get("defaults"), dict) else {}
    d = section(ts, "defaults")
    return (
        d["max_pending_orders"]
        if "max_pending_orders" in explicit
        else settings.max_pending_orders,
        d["order_timeout_minutes"]
        if "order_timeout_minutes" in explicit
        else settings.order_ttl_minutes,
    )


async def enforce_payables_cap(db: AsyncSession, settings: Settings, buyer: User) -> None:
    """Serialize creation per buyer (row lock), then cap open unpaid payables."""
    await db.execute(select(User.id).where(User.id == buyer.id).with_for_update())
    max_pending, _ttl = await tenant_order_limits(db, settings, buyer.tenant_id)
    cap = settings.max_pending_orders_instant if buyer.is_instant else max_pending
    if await count_payables(db, buyer.id) >= cap:
        raise Conflict(f"At most {cap} unpaid orders; pay or wait for one to expire")


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
    buyer_input: str | None = None,
    fee_permille: int | None = None,
    checkout_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    enforce_cap: bool = True,
    kind: str = "goods",
    discount_minor: int = 0,
    promo_code_id: uuid.UUID | None = None,
    reseller_id: uuid.UUID | None = None,
    reseller_markup_minor: int = 0,
) -> tuple[Order, Message | None]:
    """Create a pending order. ``amount_minor`` is the SUBTOTAL (storefront markup included):
    the promo discount is subtracted and the channel fee added here, server-side
    (``fee = ceil((subtotal - discount) * permille / 1000)``); the order stores the applied
    rate. The one-time plaintext access code is left on ``order.access_code_plain``
    (never persisted, never logged)."""
    if enforce_cap:
        await enforce_payables_cap(db, settings, buyer)
    if fee_permille is None:
        cfg = entry_cfg(await tenant_settings(db, buyer.tenant_id))
        if not cfg.enabled:
            raise Unavailable("Payment channel is not available")
        fee_permille = cfg.permille
    # MONEY: amount (gateway charge) = subtotal - discount + fee == DB CHECK ck_orders_amount_split
    fee, total = split_amount(amount_minor, fee_permille, discount_minor)
    if expires_at is None:
        expires_at = utcnow() + timedelta(
            minutes=(await tenant_order_limits(db, settings, buyer.tenant_id))[1]
        )
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
        amount_minor=total,
        subtotal_minor=amount_minor,
        fee_minor=fee,
        fee_permille=fee_permille,
        checkout_id=checkout_id,
        buyer_input=buyer_input,
        currency=currency,
        status="pending_payment",
        version=1,
        delivery_mode=delivery_mode,
        expires_at=expires_at,
        kind=kind,
        discount_minor=discount_minor,
        promo_code_id=promo_code_id,
        reseller_id=reseller_id,
        reseller_markup_minor=reseller_markup_minor,
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
        detail={
            "quote_id": str(quote_id) if quote_id else None,
            "source": source,
            "checkout_id": str(checkout_id) if checkout_id else None,
            "fee_permille": fee_permille,
            "kind": kind,
            "discount_minor": discount_minor,
            "reseller_id": str(reseller_id) if reseller_id else None,
            "reseller_markup_minor": reseller_markup_minor,
            **({"buyer_input": True} if buyer_input else {}),  # never the value
        },
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
    buyer_input: str | None = None,
    promo_code: str | None = None,
    storefront: Storefront | None = None,
) -> tuple[Order, Message | None]:
    product = await get_active_product(db, product_id)
    pricer = await Pricer.load(db, storefront)
    if pricer is not None and pricer.is_hidden(product.id):
        raise NotFound("Product not found")
    # MONEY: the unit price is the server's (base + storefront markup); never the client's.
    unit = pricer.unit(product) if pricer else product.price_minor
    markup = pricer.markup(product) * qty if pricer else 0
    subtotal = unit * qty
    value = normalize_value(product.buyer_input, buyer_input)
    if product.delivery_mode == "auto_card" and await available_stock(db, product.id) < qty:
        raise OutOfStock("Not enough stock", extra={"product_id": str(product.id)})
    # lock order everywhere: buyer row (payables cap) BEFORE the promo row (no deadlock between
    # a buy-now and a checkout of the same buyer)
    await enforce_payables_cap(db, settings, buyer)
    ev = None
    if promo_code and promo_code.strip():
        ts = await tenant_settings(db, buyer.tenant_id)
        require_feature(ts, "promo_codes")
        if storefront is not None:  # v5: no promo on reseller storefronts
            raise PromoError("promo_not_applicable")
        ev = await promo_svc.evaluate(
            db,
            tenant_id=buyer.tenant_id,
            user=buyer,
            code=promo_code,
            lines=[PromoLine(product.id, product.category, subtotal)],
            currency=product.currency,
            lock=True,  # promo row FOR UPDATE until commit: limits + reserve are atomic
        )
    conv = await chat.open_conversation_for(db, buyer)
    order, msg = await create_order(
        db,
        settings=settings,
        buyer=buyer,
        title=pick(product.title_i18n, buyer.locale),
        amount_minor=subtotal,
        currency=product.currency,
        qty=qty,
        delivery_mode=product.delivery_mode,
        product_id=product.id,
        quote_id=None,
        conversation_id=conv.id if conv else None,
        ip=ip,
        source=source,
        buyer_input=value,
        discount_minor=ev.discount_minor if ev else 0,
        promo_code_id=ev.promo.id if ev else None,
        reseller_id=storefront.id if storefront else None,
        reseller_markup_minor=markup,
        enforce_cap=False,  # done above, before the promo lock
    )
    if ev is not None:
        await promo_svc.reserve(db, ev, buyer, order_id=order.id, ip=ip)
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
    stmt = select(Order).where(
        Order.id == oid if oid else Order.order_no == order_no, Order.tenant_id == user.tenant_id
    )
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


async def check_channel(db: AsyncSession, tenant_id: uuid.UUID, channel: str) -> int:
    """The channel's current surcharge permille; 503 when it is switched off."""
    cfg = channel_cfg(await tenant_settings(db, tenant_id), channel)
    if not cfg.enabled:
        raise Unavailable("Payment channel is not available")
    return cfg.permille


async def pay_url(db: AsyncSession, res: Resources, ref: str, user: User, channel: str) -> str:
    order = await get_owned(db, ref, user, lock=True)
    if order.checkout_id is not None:
        # a member order is never paid on its own: its money only arrives via the checkout
        checkout_no = await db.scalar(
            select(Checkout.checkout_no).where(Checkout.id == order.checkout_id)
        )
        raise PayViaCheckout(
            "This order is part of a cart checkout; pay the checkout",
            extra={"checkout_no": checkout_no or ""},
        )
    if order.status != "pending_payment":
        raise InvalidState("Order is not awaiting payment")
    if order.expires_at is not None and order.expires_at <= utcnow():
        raise InvalidState("Order has expired")
    usdt = is_usdt(channel)
    if usdt and not res.bepusdt.configured:
        raise Unavailable("Payment channel not configured")
    if not usdt and not (res.settings.zpay_pid and res.settings.zpay_key):
        raise Unavailable("Payment channel not configured")
    permille = await check_channel(db, order.tenant_id, channel)
    if permille != order.fee_permille:
        if order.channel is not None:  # a pay URL is already out for the old amount
            raise FeeChannelMismatch("The payment fee changed; please recreate the order")
        # no URL issued yet (also covers pre-v4 pending orders): re-apply the current rate
        before = snapshot(order)
        fee, total = split_amount(order.subtotal_minor, permille, order.discount_minor)
        order.fee_minor, order.fee_permille, order.amount_minor = fee, permille, total
        order.version += 1
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="order.fee_recomputed",
            actor_id=user.id,
            actor_role=user.role,
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            detail={"fee_permille": permille},
        )
    order.channel = channel
    if usdt:
        no, title, minor, cur = order.order_no, order.title, order.amount_minor, order.currency
        await db.commit()  # release the row lock before the gateway call
        await usdt_svc.start_attempt(
            res, no=no, title=title, amount_minor=minor, currency=cur, channel=channel
        )
        return f"/pay/usdt/{no}"
    url = res.zpay.build_pay_url(order.order_no, order.title, to_decimal(order.amount_minor))
    await db.commit()
    return url


async def update_input(
    db: AsyncSession, ref: str, user: User, value: str | None, ip: str | None
) -> tuple[Order, Message | None]:
    """Buyer fixes the order information while the order is pending or paid (not delivered)."""
    order = await get_owned(db, ref, user, lock=True)
    if order.status not in ("pending_payment", "paid"):
        raise InvalidState("The order information can no longer be changed")
    product = await db.get(Product, order.product_id) if order.product_id else None
    cfg = product.buyer_input if product else None
    if not (cfg and cfg.get("enabled")):
        raise Validation("This order has no order information")
    new = normalize_value(cfg, value)
    if new is None:
        raise Validation("buyer_input is required")
    order.buyer_input = new
    order.version += 1
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="order.input_updated",
        actor_id=user.id,
        actor_role=user.role,
        target=f"order:{order.id}",
        ip=ip,
        detail={"buyer_input": True},  # never the value
    )
    msg = None
    if order.status == "paid":  # CS already acted on the first value: tell them it changed
        conv_id = order.conversation_id or await ensure_open_conversation(
            db, tenant_id=order.tenant_id, buyer_id=order.buyer_id
        )
        order.conversation_id = conv_id
        msg, _ = await insert_message(
            db,
            tenant_id=order.tenant_id,
            conversation_id=conv_id,
            sender_id=order.buyer_id,
            sender_role="buyer",
            type="text",
            text=chat_text(order.order_no, order.title, new).replace("📝", "📝 (updated)", 1),
            client_msg_id=f"order-input:{order.id}:{order.version}",
        )
    await db.commit()
    return order, msg


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


async def _expire_order(db: AsyncSession, tid: uuid.UUID, order: Order) -> Message | None:
    before = snapshot(order)
    order.status = "expired"
    order.version += 1
    await add_event(
        db, order, "pending_payment", "expired", actor_kind="system", reason="payment_timeout"
    )
    await audit(
        db,
        tenant_id=tid,
        action="order.expired",
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
    )
    return await post_system(
        db,
        tenant_id=tid,
        conversation_id=order.conversation_id,
        event="expired",
        order_id=str(order.id),
    )


async def expire_pending(
    maker: async_sessionmaker[AsyncSession], res: Resources | None = None
) -> int:
    """Expire pending checkouts (with their orders), pending single orders and offered
    quotes past their ``expires_at``."""
    async with tenant_session(maker, None) as s:
        tenant_ids = list(await s.scalars(select(Tenant.id)))
    total = 0
    for tid in tenant_ids:
        async with tenant_session(maker, tid) as db:
            now = utcnow()
            rows: list[Order] = []
            msgs: dict[uuid.UUID, Message | None] = {}
            checkouts = list(
                await db.scalars(
                    select(Checkout)
                    .where(Checkout.status == "pending_payment", Checkout.expires_at < now)
                    .order_by(Checkout.id)
                    .with_for_update(skip_locked=True)
                    .limit(200)
                )
            )
            for co in checkouts:  # checkout first, then its orders ORDER BY id
                members = list(
                    await db.scalars(
                        select(Order)
                        .where(Order.checkout_id == co.id, Order.status == "pending_payment")
                        .order_by(Order.id)
                        .with_for_update()
                    )
                )
                co.status = "expired"
                await promo_svc.release(  # reserved -> released
                    db,
                    checkout_id=co.id,
                    tenant_id=tid,
                    expect=co.promo_code_id is not None,
                )
                await audit(
                    db,
                    tenant_id=tid,
                    action="checkout.expired",
                    target=f"checkout:{co.id}",
                    before={"status": "pending_payment"},
                    after={"status": "expired"},
                )
                for order in members:
                    msgs[order.id] = await _expire_order(db, tid, order)
                    rows.append(order)
            singles = list(
                await db.scalars(
                    select(Order)
                    .where(
                        Order.status == "pending_payment",
                        Order.expires_at < now,
                        Order.checkout_id.is_(None),  # members expire with their checkout
                    )
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            )
            for order in singles:
                await promo_svc.release(  # reserved -> released
                    db,
                    order_id=order.id,
                    tenant_id=tid,
                    expect=order.promo_code_id is not None,
                )
                msgs[order.id] = await _expire_order(db, tid, order)
                rows.append(order)
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
