"""Cart quote + combined checkout (docs/review-v4.md §2).

One checkout = one order per cart line (each with its own order_no, access code and fee)
+ one ``checkouts`` row paid in ONE gateway transaction (``out_trade_no = checkout_no``).
"""

from __future__ import annotations

import base64
import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import (
    FeeChannelMismatch,
    InvalidState,
    NotFound,
    OutOfStock,
    Unavailable,
    Validation,
)
from app.core.i18n import pick
from app.core.money import fmt, to_decimal
from app.core.resources import Resources
from app.models import Checkout, Order, Product, User
from app.modules.catalog.service import stock_counts
from app.modules.chat import service as chat
from app.modules.common.buyerinput import is_enabled, normalize_value
from app.modules.common.orderviews import order_views
from app.modules.common.views import iso
from app.modules.content.site import tenant_settings
from app.modules.orders.service import (
    check_channel,
    create_order,
    enforce_payables_cap,
    utcnow,
)
from app.modules.payments.channels import channel_cfg, fee_rate, split_amount

MAX_LINES = 20
MAX_QTY = 10


def new_checkout_no() -> str:
    """ "C" + 12 base32 chars (60 random bits)."""
    return "C" + base64.b32encode(secrets.token_bytes(8)).decode()[:12]


# ------------------------------------------------------------------ cart quote
async def quote_cart(
    db: AsyncSession, tenant_id: uuid.UUID, items: list[Any], lang: str
) -> dict[str, Any]:
    """Server truth for a cart. Unavailable lines are flagged (not dropped) and excluded
    from the totals."""
    ts = await tenant_settings(db, tenant_id)
    cfg = channel_cfg(ts)
    ids = list({i.product_id for i in items})
    products = {
        p.id: p
        for p in await db.scalars(
            select(Product).where(
                Product.id.in_(ids), Product.tenant_id == tenant_id, Product.status == "active"
            )
        )
    }
    stock = await stock_counts(
        db, [p.id for p in products.values() if p.delivery_mode == "auto_card"]
    )
    lines: list[dict[str, Any]] = []
    subtotal = fee = 0
    currency = "CNY"
    for it in items:
        p = products.get(it.product_id)
        if p is None:
            lines.append(
                {
                    "product_id": str(it.product_id),
                    "title": "",
                    "cover": None,
                    "unit_price": "0.00",
                    "currency": currency,
                    "stock": None,
                    "available": False,
                    "max_qty": 0,
                    "needs_input": False,
                    "input_missing": False,
                    "qty": it.qty,
                    "line_subtotal": "0.00",
                }
            )
            continue
        s = stock.get(p.id) if p.delivery_mode == "auto_card" else None
        max_qty = MAX_QTY if s is None else min(MAX_QTY, s)
        available = it.qty <= max_qty
        needs = is_enabled(p.buyer_input) and bool(p.buyer_input.get("required", True))
        line_sub = p.price_minor * it.qty
        if available:
            line_fee, _ = split_amount(line_sub, cfg.permille)
            subtotal += line_sub
            fee += line_fee
        currency = p.currency
        lines.append(
            {
                "product_id": str(p.id),
                "title": pick(p.title_i18n, lang),
                "cover": p.images[0] if p.images else None,
                "unit_price": fmt(p.price_minor),
                "currency": p.currency,
                "stock": s,
                "available": available,
                "max_qty": max_qty,
                "needs_input": needs,
                # extra: the request carried no value for a required input
                "input_missing": needs and not (it.buyer_input or "").strip(),
                "qty": it.qty,
                "line_subtotal": fmt(line_sub),
            }
        )
    return {
        "lines": lines,
        "subtotal": fmt(subtotal),
        "fee": fmt(fee),
        "fee_rate": fee_rate(cfg.permille),
        "total": fmt(subtotal + fee),
        "currency": currency,
    }


# ------------------------------------------------------------------ views
async def checkout_views(db: AsyncSession, cos: list[Checkout]) -> list[dict[str, Any]]:
    if not cos:
        return []
    rows = list(
        await db.scalars(
            select(Order)
            .where(
                Order.checkout_id.in_([c.id for c in cos]),
                Order.tenant_id.in_({c.tenant_id for c in cos}),
            )
            .order_by(Order.created_at, Order.id)
        )
    )
    by: dict[uuid.UUID, list[Order]] = {}
    for o in rows:
        by.setdefault(o.checkout_id, []).append(o)  # type: ignore[arg-type]
    out = []
    for c in cos:
        os_ = by.get(c.id, [])
        out.append(
            {
                "checkout_no": c.checkout_no,
                "status": c.status,
                "amount": fmt(c.amount_minor),
                "currency": c.currency,
                "expires_at": iso(c.expires_at),
                "order_nos": [o.order_no for o in os_],
                "subtotal": fmt(sum(o.subtotal_minor for o in os_)),
                "fee": fmt(sum(o.fee_minor for o in os_)),
                "fee_rate": fee_rate(os_[0].fee_permille) if os_ else "0%",
                "channel": c.channel,
                "paid_at": iso(c.paid_at),
                "created_at": iso(c.created_at),
            }
        )
    return out


async def get_owned_checkout(
    db: AsyncSession, checkout_no: str, user: User, *, lock: bool = False
) -> Checkout:
    stmt = select(Checkout).where(
        Checkout.checkout_no == checkout_no.upper()[:16], Checkout.tenant_id == user.tenant_id
    )
    if lock:
        stmt = stmt.with_for_update()
    co = await db.scalar(stmt)
    if co is None or co.buyer_id != user.id:
        raise NotFound("Checkout not found")
    return co


# ------------------------------------------------------------------ create
async def create_checkout(
    db: AsyncSession,
    settings: Settings,
    buyer: User,
    items: list[Any],
    source: str | None,
    ip: str | None,
    lang: str,
) -> dict[str, Any]:
    if not 1 <= len(items) <= MAX_LINES:
        raise Validation(f"a checkout has 1 to {MAX_LINES} lines")
    await enforce_payables_cap(db, settings, buyer)
    ids = list({i.product_id for i in items})
    products = {
        p.id: p
        for p in await db.scalars(
            select(Product).where(
                Product.id.in_(ids),
                Product.tenant_id == buyer.tenant_id,
                Product.status == "active",
            )
        )
    }
    if len(products) != len(ids):
        raise NotFound("Product not found")
    if len({p.currency for p in products.values()}) > 1:
        raise Validation("All items must use the same currency")
    # stock: summed per product so duplicate lines cannot oversell
    want: dict[uuid.UUID, int] = {}
    for it in items:
        want[it.product_id] = want.get(it.product_id, 0) + it.qty
    stock = await stock_counts(
        db, [p.id for p in products.values() if p.delivery_mode == "auto_card"]
    )
    for pid, qty in want.items():
        p = products[pid]
        if p.delivery_mode == "auto_card" and stock.get(pid, 0) < qty:
            raise OutOfStock("Not enough stock", extra={"product_id": str(pid)})
    cfg = channel_cfg(await tenant_settings(db, buyer.tenant_id))
    if not cfg.enabled:
        raise Unavailable("Payment channel is not available")
    inputs = [normalize_value(products[i.product_id].buyer_input, i.buyer_input) for i in items]
    total = sum(
        split_amount(products[i.product_id].price_minor * i.qty, cfg.permille)[1] for i in items
    )
    expires_at = utcnow() + timedelta(minutes=settings.order_ttl_minutes)
    co = Checkout(
        tenant_id=buyer.tenant_id,
        checkout_no=new_checkout_no(),
        buyer_id=buyer.id,
        amount_minor=total,
        currency=next(iter(products.values())).currency,
        status="pending_payment",
        expires_at=expires_at,
    )
    db.add(co)
    await db.flush()
    conv = await chat.open_conversation_for(db, buyer)
    orders: list[Order] = []
    codes: dict[str, str] = {}
    for it, value in zip(items, inputs, strict=True):
        p = products[it.product_id]
        order, _msg = await create_order(
            db,
            settings=settings,
            buyer=buyer,
            title=pick(p.title_i18n, buyer.locale),
            amount_minor=p.price_minor * it.qty,
            currency=p.currency,
            qty=it.qty,
            delivery_mode=p.delivery_mode,
            product_id=p.id,
            quote_id=None,
            conversation_id=conv.id if conv else None,
            ip=ip,
            source=source,
            buyer_input=value,
            fee_permille=cfg.permille,
            checkout_id=co.id,
            expires_at=expires_at,
            enforce_cap=False,
        )
        orders.append(order)
        codes[order.order_no] = order.access_code_plain  # type: ignore[attr-defined]
    if sum(o.amount_minor for o in orders) != co.amount_minor:
        raise InvalidState("Checkout amount does not match its orders")  # invariant: rolls back
    await audit(
        db,
        tenant_id=co.tenant_id,
        action="checkout.created",
        actor_id=buyer.id,
        actor_role=buyer.role,
        target=f"checkout:{co.id}",
        after={"status": co.status, "amount_minor": co.amount_minor},
        ip=ip,
        detail={"order_nos": [o.order_no for o in orders], "source": source},
    )
    await db.commit()
    (view,) = await checkout_views(db, [co])
    return {
        "checkout": view,
        "orders": await order_views(db, orders, owner_id=buyer.id, lang=lang),
        "access_codes": codes,
    }


# ------------------------------------------------------------------ pay
async def recompute_fees(
    db: AsyncSession, co: Checkout, orders: list[Order], permille: int, user: User
) -> None:
    """No pay URL has been issued yet: re-apply the CURRENT channel rate to every member and
    the checkout amount (caller holds the checkout + orders locks)."""
    before = {"amount_minor": co.amount_minor}
    for o in orders:
        fee, total = split_amount(o.subtotal_minor, permille)
        o.fee_minor, o.fee_permille, o.amount_minor = fee, permille, total
        o.version += 1
    co.amount_minor = sum(o.amount_minor for o in orders)
    await audit(
        db,
        tenant_id=co.tenant_id,
        action="checkout.fee_recomputed",
        actor_id=user.id,
        actor_role=user.role,
        target=f"checkout:{co.id}",
        before=before,
        after={"amount_minor": co.amount_minor},
        detail={"fee_permille": permille},
    )


async def pay_checkout(
    db: AsyncSession, res: Resources, checkout_no: str, user: User, channel: str
) -> str:
    co = await get_owned_checkout(db, checkout_no, user, lock=True)
    orders = list(
        await db.scalars(
            select(Order)
            .where(Order.checkout_id == co.id, Order.tenant_id == co.tenant_id)
            .order_by(Order.id)
            .with_for_update()
        )
    )
    if co.status != "pending_payment" or any(o.status != "pending_payment" for o in orders):
        raise InvalidState("Checkout is not awaiting payment")
    if co.expires_at is not None and co.expires_at <= utcnow():
        raise InvalidState("Checkout has expired")
    if not (res.settings.zpay_pid and res.settings.zpay_key):
        raise Unavailable("Payment channel not configured")
    permille = await check_channel(db, co.tenant_id, channel)
    if any(o.fee_permille != permille for o in orders):
        if co.channel is not None:  # a pay URL is already out for the old amount
            raise FeeChannelMismatch("The payment fee changed; please recreate the checkout")
        await recompute_fees(db, co, orders, permille, user)
    co.channel = channel
    name = orders[0].title if len(orders) == 1 else f"{orders[0].title} +{len(orders) - 1}"
    url = res.zpay.build_pay_url(co.checkout_no, name, to_decimal(co.amount_minor))
    await db.commit()
    return url
