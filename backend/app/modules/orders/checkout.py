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
    ApiError,
    FeeChannelMismatch,
    InvalidState,
    NotFound,
    OutOfStock,
    Unavailable,
    Validation,
)
from app.core.features import require_feature
from app.core.i18n import pick
from app.core.money import fmt, to_decimal
from app.core.resources import Resources
from app.core.storefront import Storefront
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
    tenant_order_limits,
    utcnow,
)
from app.modules.payments.channels import channel_cfg, fee_rate, split_amount
from app.modules.promo import service as promo_svc
from app.modules.promo.service import PromoError, PromoLine
from app.modules.reseller.pricing import Pricer

MAX_LINES = 20
MAX_QTY = 10


def new_checkout_no() -> str:
    """ "C" + 12 base32 chars (60 random bits)."""
    return "C" + base64.b32encode(secrets.token_bytes(8)).decode()[:12]


# ------------------------------------------------------------------ cart quote
async def quote_cart(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    items: list[Any],
    lang: str,
    *,
    promo_code: str | None = None,
    user: User | None = None,
    storefront: Storefront | None = None,
) -> dict[str, Any]:
    """Server truth for a cart. Unavailable lines are flagged (not dropped) and excluded
    from the totals. Prices are the storefront's (base + markup); an optional promo code is
    evaluated read-only (no lock, no reservation): the result is shown, never trusted."""
    ts = await tenant_settings(db, tenant_id)
    require_feature(ts, "cart")
    cfg = channel_cfg(ts)
    pricer = await Pricer.load(db, storefront)
    ids = list({i.product_id for i in items})
    products = {
        p.id: p
        for p in await db.scalars(
            select(Product).where(Product.id.in_(ids), Product.tenant_id == tenant_id)
        )
        if pricer is None or not pricer.is_hidden(p.id)  # hidden on this storefront
    }
    stock = await stock_counts(
        db, [p.id for p in products.values() if p.delivery_mode == "auto_card"]
    )
    lines: list[dict[str, Any]] = []
    avail: list[tuple[int, Product, int]] = []  # (line index, product, line subtotal)
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
                    "unavailable_reason": "archived",
                    "max_qty": 0,
                    "needs_input": False,
                    "input_missing": False,
                    "qty": it.qty,
                    "line_subtotal": "0.00",
                    "discount_minor": 0,
                }
            )
            continue
        s = stock.get(p.id) if p.delivery_mode == "auto_card" else None
        max_qty = MAX_QTY if s is None else min(MAX_QTY, s)
        if p.status != "active":
            max_qty = 0
        reason = (
            "archived"
            if p.status == "archived"
            else "unpublished"
            if p.status != "active"
            else "out_of_stock"
            if it.qty > max_qty
            else None
        )
        available = reason is None
        needs = is_enabled(p.buyer_input) and bool(p.buyer_input.get("required", True))
        unit = pricer.unit(p) if pricer else p.price_minor
        line_sub = unit * it.qty
        if available:
            avail.append((len(lines), p, line_sub))
        currency = p.currency
        lines.append(
            {
                "product_id": str(p.id),
                "title": pick(p.title_i18n, lang),
                "cover": p.images[0] if p.images else None,
                "unit_price": fmt(unit),
                "currency": p.currency,
                "stock": s,
                "available": available,
                "unavailable_reason": reason,
                "max_qty": max_qty,
                "needs_input": needs,
                # extra: the request carried no value for a required input
                "input_missing": needs and not (it.buyer_input or "").strip(),
                "qty": it.qty,
                "line_subtotal": fmt(line_sub),
                "discount_minor": 0,
            }
        )
    promo: dict[str, Any] | None = None
    alloc = [0] * len(avail)
    if promo_code and promo_code.strip():
        try:
            require_feature(ts, "promo_codes")
            if storefront is not None:
                raise PromoError("promo_not_applicable")
            if not avail:
                raise PromoError("promo_not_applicable")
            ev = await promo_svc.evaluate(
                db,
                tenant_id=tenant_id,
                user=user,
                code=promo_code,
                lines=[PromoLine(p.id, p.category, sub) for _i, p, sub in avail],
                currency=currency,
                lock=False,
            )
            alloc = ev.allocation
            promo = {"code": ev.promo.code, "discount_minor": ev.discount_minor}
        except ApiError as exc:
            promo = {"error": exc.code}
    subtotal = fee = discount = 0
    for (idx, _p, sub), disc in zip(avail, alloc, strict=True):
        # MONEY: fee on the discounted line, exactly as create_checkout will charge it
        line_fee, _ = split_amount(sub, cfg.permille, disc)
        subtotal += sub
        discount += disc
        fee += line_fee
        lines[idx]["discount_minor"] = disc
    return {
        "lines": lines,
        "subtotal": fmt(subtotal),
        "discount": fmt(discount),
        "discount_minor": discount,
        "fee": fmt(fee),
        "fee_rate": fee_rate(cfg.permille),
        "total": fmt(subtotal - discount + fee),
        "currency": currency,
        "promo": promo,
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
                "discount": fmt(sum(o.discount_minor for o in os_)),
                "discount_minor": sum(o.discount_minor for o in os_),
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
    *,
    promo_code: str | None = None,
    storefront: Storefront | None = None,
) -> dict[str, Any]:
    if not 1 <= len(items) <= MAX_LINES:
        raise Validation(f"a checkout has 1 to {MAX_LINES} lines")
    ts = await tenant_settings(db, buyer.tenant_id)
    require_feature(ts, "cart")
    await enforce_payables_cap(db, settings, buyer)  # buyer row lock BEFORE the promo row lock
    pricer = await Pricer.load(db, storefront)
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
        if pricer is None or not pricer.is_hidden(p.id)
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
    # MONEY: unit price = base + storefront markup (server side); one order per cart line.
    units = [
        pricer.unit(products[i.product_id]) if pricer else products[i.product_id].price_minor
        for i in items
    ]
    markups = [pricer.markup(products[i.product_id]) * i.qty if pricer else 0 for i in items]
    subs = [u * i.qty for u, i in zip(units, items, strict=True)]
    currency = next(iter(products.values())).currency
    ev = None
    discounts = [0] * len(items)
    if promo_code and promo_code.strip():
        require_feature(ts, "promo_codes")
        if storefront is not None:  # v5: no promo on reseller storefronts
            raise PromoError("promo_not_applicable")
        ev = await promo_svc.evaluate(
            db,
            tenant_id=buyer.tenant_id,
            user=buyer,
            code=promo_code,
            lines=[
                PromoLine(products[i.product_id].id, products[i.product_id].category, sub)
                for i, sub in zip(items, subs, strict=True)
            ],
            currency=currency,
            lock=True,  # promo row FOR UPDATE until commit
        )
        discounts = ev.allocation  # sums to ev.discount_minor, each < its line subtotal
    # fee per order on (subtotal - discount); the checkout amount is the sum of the orders
    total = sum(
        split_amount(sub, cfg.permille, disc)[1] for sub, disc in zip(subs, discounts, strict=True)
    )
    ttl = (await tenant_order_limits(db, settings, buyer.tenant_id))[1]
    expires_at = utcnow() + timedelta(minutes=ttl)
    co = Checkout(
        tenant_id=buyer.tenant_id,
        checkout_no=new_checkout_no(),
        buyer_id=buyer.id,
        amount_minor=total,
        currency=currency,
        status="pending_payment",
        expires_at=expires_at,
        reseller_id=storefront.id if storefront else None,
        promo_code_id=ev.promo.id if ev else None,
    )
    db.add(co)
    await db.flush()
    conv = await chat.open_conversation_for(db, buyer)
    orders: list[Order] = []
    codes: dict[str, str] = {}
    for it, value, sub, disc, mk in zip(items, inputs, subs, discounts, markups, strict=True):
        p = products[it.product_id]
        order, _msg = await create_order(
            db,
            settings=settings,
            buyer=buyer,
            title=pick(p.title_i18n, buyer.locale),
            amount_minor=sub,
            discount_minor=disc,
            promo_code_id=ev.promo.id if ev else None,
            reseller_id=storefront.id if storefront else None,
            reseller_markup_minor=mk,
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
    if ev is not None:
        if sum(o.discount_minor for o in orders) != ev.discount_minor:
            raise InvalidState("Promo allocation does not match")  # invariant: rolls back
        await promo_svc.reserve(db, ev, buyer, checkout_id=co.id, ip=ip)
    await audit(
        db,
        tenant_id=co.tenant_id,
        action="checkout.created",
        actor_id=buyer.id,
        actor_role=buyer.role,
        target=f"checkout:{co.id}",
        after={"status": co.status, "amount_minor": co.amount_minor},
        ip=ip,
        detail={
            "order_nos": [o.order_no for o in orders],
            "source": source,
            "discount_minor": sum(o.discount_minor for o in orders),
            "reseller_id": str(storefront.id) if storefront else None,
        },
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
        fee, total = split_amount(o.subtotal_minor, permille, o.discount_minor)
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
