"""Reseller program (review-v5 s7): sign-up, store settings, price overrides, earnings
balances, payouts, and the admin side. See ``signup.py`` for activation at payment."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storefront
from app.core.audit import audit
from app.core.config import get_settings
from app.core.deps import Page
from app.core.errors import (
    AlreadyPaid,
    Conflict,
    Forbidden,
    InvalidState,
    NotFound,
    Validation,
)
from app.core.i18n import pick
from app.core.money import fmt
from app.core.resources import Resources
from app.core.security import STAFF_ROLES
from app.core.settings_v5 import section
from app.models import (
    Order,
    Product,
    Reseller,
    ResellerPayout,
    ResellerPrice,
    User,
)
from app.modules.catalog.service import apply_search, escape_like, prepare_search
from app.modules.chat import service as chat
from app.modules.common.views import iso
from app.modules.content.site import tenant_settings
from app.modules.orders import cancel as cancel_svc
from app.modules.orders.service import create_order, one_view
from app.modules.payments.ledger import Line, post_txn
from app.modules.reseller.pricing import markup_per_unit

GOODS_PAID = ("paid", "delivered", "completed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ balances / stats
async def balances(
    db: AsyncSession,
    r: Reseller,
    rcfg: dict[str, Any],
    *,
    exclude_payout: uuid.UUID | None = None,
    raw: bool = False,
) -> dict[str, int]:
    """MONEY: ``pending`` = markup of paid/delivered orders + completed orders younger than
    ``settle_after_hours``; ``available`` = markup of completed orders older than that, minus
    payouts requested or paid (never below 0: a refund after payout is a clawback on the
    ledger, not a negative withdrawable). Refunded / cancelled / manual_review orders count
    for nothing."""
    cutoff = _now() - timedelta(hours=rcfg["settle_after_hours"])
    mk = Order.reseller_markup_minor
    pending, ready = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Order.status.in_(("paid", "delivered")), mk),
                            (and_(Order.status == "completed", Order.completed_at > cutoff), mk),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (and_(Order.status == "completed", Order.completed_at <= cutoff), mk),
                            else_=0,
                        )
                    ),
                    0,
                ),
            ).where(
                Order.reseller_id == r.id, Order.tenant_id == r.tenant_id, Order.kind == "goods"
            )
        )
    ).one()
    pay = dict(
        (
            await db.execute(
                select(
                    ResellerPayout.status, func.coalesce(func.sum(ResellerPayout.amount_minor), 0)
                )
                .where(
                    ResellerPayout.reseller_id == r.id,
                    ResellerPayout.tenant_id == r.tenant_id,
                    *([ResellerPayout.id != exclude_payout] if exclude_payout else []),
                )
                .group_by(ResellerPayout.status)
            )
        ).all()
    )
    paid, requested = int(pay.get("paid", 0)), int(pay.get("requested", 0))
    avail = int(ready) - paid - requested
    return {
        "pending_minor": int(pending),
        "available_minor": avail if raw else max(0, avail),
        "paid_minor": paid,
    }


async def _window(db: AsyncSession, r: Reseller, since: datetime | None) -> dict[str, int]:
    cond = [
        Order.reseller_id == r.id,
        Order.tenant_id == r.tenant_id,
        Order.kind == "goods",
        Order.status.in_(GOODS_PAID),
    ]
    if since is not None:
        cond.append(Order.paid_at >= since)
    n, e = (
        await db.execute(
            select(
                func.count(Order.id), func.coalesce(func.sum(Order.reseller_markup_minor), 0)
            ).where(*cond)
        )
    ).one()
    return {"orders": int(n), "earnings_minor": int(e)}


async def stats(db: AsyncSession, r: Reseller, rcfg: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t = await _window(db, r, today)
    w = await _window(db, r, now - timedelta(days=7))
    return {
        "today": t,
        "last_7_days": w,
        "total": await _window(db, r, None),
        "balances": await balances(db, r, rcfg),
        # flat aliases used by the buyer web app
        "today_orders": t["orders"],
        "today_earnings_minor": t["earnings_minor"],
        "week_orders": w["orders"],
        "week_earnings_minor": w["earnings_minor"],
    }


# ------------------------------------------------------------------ views
def store_url(slug: str, rcfg: dict[str, Any]) -> str:
    """THE single store-link builder: ``query`` (default) -> ``{public base}/?store=slug``;
    ``subdomain`` -> ``https://slug.{base_domain}/``."""
    if rcfg.get("link_mode") == "subdomain":
        return f"https://{slug}.{rcfg['base_domain']}/"
    return f"{get_settings().public_base_url.rstrip('/')}/?store={slug}"


async def signup_order_no(db: AsyncSession, r: Reseller) -> str | None:
    if r.signup_order_id is None:
        return None
    return await db.scalar(
        select(Order.order_no).where(Order.id == r.signup_order_id, Order.tenant_id == r.tenant_id)
    )


async def me_view(db: AsyncSession, r: Reseller, rcfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": r.slug,
        "store_name": r.store_name,
        "status": r.status,
        "store_url": store_url(r.slug, rcfg),
        "markup_kind": r.markup_kind,
        "markup_value": int(r.markup_value),
        "logo_url": r.logo_url,
        "welcome_i18n": dict(r.welcome_i18n or {}),
        "contact": r.contact,
        "payout_account": r.payout_account,
        "signup_order_no": await signup_order_no(db, r),
        "balances": await balances(db, r, rcfg),
    }


async def get_mine(db: AsyncSession, user: User) -> Reseller | None:
    return await db.scalar(
        select(Reseller).where(Reseller.user_id == user.id, Reseller.tenant_id == user.tenant_id)
    )


async def require_mine(db: AsyncSession, user: User, *, active: bool = False) -> Reseller:
    r = await get_mine(db, user)
    if r is None or r.status == "pending_payment":
        raise NotFound("No reseller store")
    if active and r.status != "active":
        raise Forbidden("Your reseller store is not active")
    return r


def _check_markup(kind: str, value: int, rcfg: dict[str, Any]) -> None:
    cap = rcfg["max_markup_permille"] if kind == "percent" else rcfg["max_markup_fixed_minor"]
    if value > cap:
        raise Validation(
            f"markup_value exceeds the program maximum ({cap})", code="markup_too_high"
        )


# ------------------------------------------------------------------ apply
NON_TERMINAL = ("pending_payment", "paid", "manual_review")


async def apply(
    db: AsyncSession,
    res: Resources,
    user: User,
    *,
    slug: str,
    store_name: str,
    contact: str | None,
    ip: str | None,
) -> tuple[Reseller, dict[str, Any] | None, dict[str, Any]]:
    """Create (or re-use while pending) the store and, when the fee is > 0, its sign-up order.

    A superseded sign-up order is never left payable: an existing NON-TERMINAL order is
    re-used (whatever the fee is now); a new order is only created when the old one is
    cancelled / expired / refunded / completed. With a fee of 0 an outstanding pending order
    is closed first through ``cancel_group`` (gateway re-query: a paid answer settles it
    instead). Lock order: order, then reseller.
    """
    settings = res.settings
    if user.is_instant or user.role in STAFF_ROLES:
        raise Forbidden("Register a full buyer account to open a store")
    uid, tid = user.id, user.tenant_id
    ts = await tenant_settings(db, tid)
    rcfg = section(ts, "resellers")
    if slug in set(rcfg["reserved_slugs"]) or slug.split("-")[0] in {"www", "api", "admin"}:
        raise Validation("This store address is reserved", code="slug_reserved")
    fee = int(rcfg["signup_fee_minor"])

    async def mine() -> Reseller | None:
        return await db.scalar(
            select(Reseller).where(Reseller.user_id == uid, Reseller.tenant_id == tid)
        )

    r0 = await mine()
    if r0 is not None and r0.status != "pending_payment":
        raise Conflict("You already have a reseller store", code="reseller_exists")
    if fee == 0 and r0 is not None and r0.signup_order_id is not None:
        o0 = await db.scalar(
            select(Order).where(Order.id == r0.signup_order_id, Order.tenant_id == tid)
        )
        if o0 is not None and o0.status == "pending_payment":
            try:
                await cancel_svc.cancel_group(
                    db,
                    res,
                    order_id=o0.id,
                    actor=user,
                    by="buyer",
                    reason="store is free now",
                    ip=ip,
                )
            except AlreadyPaid:
                pass  # settled (store activated): re-read below -> reseller_exists
            await db.refresh(user)
            r0 = await mine()
            if r0 is not None and r0.status != "pending_payment":
                raise Conflict("You already have a reseller store", code="reseller_exists")
    # W1: lock the sign-up ORDER first (settlement locks order -> reseller), then the store
    old: Order | None = None
    if r0 is not None and r0.signup_order_id is not None:
        old = await db.scalar(
            select(Order)
            .where(Order.id == r0.signup_order_id, Order.tenant_id == tid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    r = await db.scalar(
        select(Reseller).where(Reseller.user_id == uid, Reseller.tenant_id == tid).with_for_update()
    )
    if r is not None and r.status != "pending_payment":
        raise Conflict("You already have a reseller store", code="reseller_exists")
    taken_stmt = select(Reseller.id).where(Reseller.slug == slug)
    if r is not None:
        taken_stmt = taken_stmt.where(Reseller.id != r.id)
    if await db.scalar(taken_stmt) is not None:
        raise Conflict("This store address is taken", code="slug_taken")
    now = _now()
    open_old = old is not None and old.status in NON_TERMINAL
    activate_now = fee == 0 and not open_old
    if r is None:
        r = Reseller(
            tenant_id=tid,
            user_id=uid,
            slug=slug,
            store_name=store_name,
            contact=contact,
            status="active" if activate_now else "pending_payment",
            markup_kind=rcfg["default_markup_kind"],
            markup_value=rcfg["default_markup_value"],
            activated_at=now if activate_now else None,
        )
        db.add(r)
    else:
        r.slug, r.store_name, r.contact = slug, store_name, contact
        if activate_now:
            r.status, r.activated_at = "active", now
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if await mine() is not None:
            raise Conflict("You already have a reseller store", code="reseller_exists") from None
        raise Conflict("This store address is taken", code="slug_taken") from None
    order_view: dict[str, Any] | None = None
    if open_old:
        assert old is not None
        order_view = await one_view(db, old, uid, user.locale)  # re-use, never a second order
    elif fee > 0:
        conv = await chat.open_conversation_for(db, user)
        # MONEY: sign-up fee from settings only; fee rule + CHECK as any order, no promo,
        # no reseller attribution, delivery_mode 'system' (never reaches the worker)
        order, _msg = await create_order(
            db,
            settings=settings,
            buyer=user,
            title=f"Reseller sign-up: {store_name}"[:200],
            amount_minor=fee,
            currency="CNY",
            qty=1,
            delivery_mode="system",
            product_id=None,
            quote_id=None,
            conversation_id=conv.id if conv else None,
            ip=ip,
            kind="reseller_signup",
        )
        r.signup_order_id = order.id
        order_view = await one_view(db, order, uid, user.locale)
    await audit(
        db,
        tenant_id=tid,
        action="reseller.applied",
        actor_id=uid,
        actor_role=user.role,
        target=f"reseller:{r.id}",
        after={"slug": r.slug, "status": r.status},
        ip=ip,
        detail={"fee_minor": fee, "reused_order": open_old},
    )
    await db.commit()
    storefront.invalidate()
    return r, order_view, rcfg


# ------------------------------------------------------------------ store settings
async def patch_me(
    db: AsyncSession, user: User, data: dict[str, Any], ip: str | None
) -> tuple[Reseller, dict[str, Any]]:
    ts = await tenant_settings(db, user.tenant_id)
    rcfg = section(ts, "resellers")
    r = await db.scalar(
        select(Reseller)
        .where(Reseller.user_id == user.id, Reseller.tenant_id == user.tenant_id)
        .with_for_update()
    )
    if r is None:
        raise NotFound("No reseller store")
    kind = data.get("markup_kind") or r.markup_kind
    value = data["markup_value"] if data.get("markup_value") is not None else int(r.markup_value)
    if "markup_kind" in data or "markup_value" in data:
        _check_markup(kind, value, rcfg)
    before = {"store_name": r.store_name, "markup": [r.markup_kind, int(r.markup_value)]}
    for k in ("store_name", "logo_url", "welcome_i18n", "contact"):
        if k in data:
            setattr(r, k, data[k] if data[k] is not None or k != "welcome_i18n" else {})
    r.markup_kind, r.markup_value = kind, value
    if "payout_account" in data:
        r.payout_account = data["payout_account"]  # already a dict (or None to clear)
    r.updated_at = _now()
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="reseller.updated",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller:{r.id}",
        before=before,
        after={"store_name": r.store_name, "markup": [r.markup_kind, int(r.markup_value)]},
        ip=ip,
        detail={"fields": sorted(k for k in data if k != "payout_account")},  # never the account
    )
    await db.commit()
    storefront.invalidate()
    return r, rcfg


# ------------------------------------------------------------------ products
async def list_products(
    db: AsyncSession, r: Reseller, rcfg: dict[str, Any], page: Page, q: str | None, lang: str
) -> dict[str, Any]:
    stmt = select(Product).where(Product.tenant_id == r.tenant_id, Product.status == "active")
    stmt = apply_search(stmt, q)
    await prepare_search(db, q)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Product.created_at.desc(), Product.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    ov = (
        {
            o.product_id: o
            for o in await db.scalars(
                select(ResellerPrice).where(
                    ResellerPrice.reseller_id == r.id,
                    ResellerPrice.tenant_id == r.tenant_id,
                    ResellerPrice.product_id.in_([p.id for p in rows]),
                )
            )
        }
        if rows
        else {}
    )
    sf = _sf(r, rcfg)
    items = []
    for p in rows:
        o = ov.get(p.id)
        kind, value = (
            (o.markup_kind, int(o.markup_value)) if o else (r.markup_kind, int(r.markup_value))
        )
        mk = markup_per_unit(p.price_minor, kind, value, sf)
        items.append(
            {
                "id": str(p.id),
                "product_id": str(p.id),
                "title": pick(p.title_i18n, lang),
                "cover": p.images[0] if p.images else None,
                "currency": p.currency,
                "base_price": fmt(p.price_minor),
                "base_price_minor": p.price_minor,
                "markup_kind": kind,
                "markup_value": value,
                "markup_minor": mk,
                "price": fmt(p.price_minor + mk),
                "price_minor": p.price_minor + mk,
                "hidden": bool(o and o.hidden),
                "custom": o is not None,
            }
        )
    return page.wrap(items, int(total))


def _sf(r: Reseller, rcfg: dict[str, Any]) -> storefront.Storefront:
    return storefront.Storefront(
        id=r.id,
        tenant_id=r.tenant_id,
        slug=r.slug,
        store_name=r.store_name,
        logo_url=r.logo_url,
        markup_kind=r.markup_kind,
        markup_value=int(r.markup_value),
        max_permille=rcfg["max_markup_permille"],
        max_fixed_minor=rcfg["max_markup_fixed_minor"],
    )


async def put_product(
    db: AsyncSession,
    user: User,
    r: Reseller,
    rcfg: dict[str, Any],
    product_id: uuid.UUID,
    data: dict[str, Any],
) -> None:
    p = await db.scalar(
        select(Product.id).where(
            Product.id == product_id, Product.tenant_id == r.tenant_id, Product.status == "active"
        )
    )
    if p is None:
        raise NotFound("Product not found")
    cur = await db.scalar(
        select(ResellerPrice).where(
            ResellerPrice.reseller_id == r.id, ResellerPrice.product_id == product_id
        )
    )
    kind = data.get("markup_kind") or (cur.markup_kind if cur else r.markup_kind)
    value = (
        data["markup_value"]
        if data.get("markup_value") is not None
        else (int(cur.markup_value) if cur else int(r.markup_value))
    )
    hidden = data["hidden"] if data.get("hidden") is not None else (cur.hidden if cur else False)
    _check_markup(kind, value, rcfg)
    vals = {
        "tenant_id": r.tenant_id,
        "reseller_id": r.id,
        "product_id": product_id,
        "markup_kind": kind,
        "markup_value": value,
        "hidden": hidden,
    }
    await db.execute(
        pg_insert(ResellerPrice)
        .values(**vals)
        .on_conflict_do_update(
            index_elements=["reseller_id", "product_id"],
            set_={k: vals[k] for k in ("markup_kind", "markup_value", "hidden")}
            | {"updated_at": func.now()},
        )
    )
    await audit(
        db,
        tenant_id=r.tenant_id,
        action="reseller.price_set",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller:{r.id}",
        detail={"product_id": str(product_id), "kind": kind, "value": value, "hidden": hidden},
    )
    await db.commit()


async def reset_product(db: AsyncSession, user: User, r: Reseller, product_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    await db.execute(
        delete(ResellerPrice).where(
            ResellerPrice.reseller_id == r.id,
            ResellerPrice.product_id == product_id,
            ResellerPrice.tenant_id == r.tenant_id,
        )
    )
    await audit(
        db,
        tenant_id=r.tenant_id,
        action="reseller.price_reset",
        actor_id=user.id,
        actor_role=user.role,
        target=f"reseller:{r.id}",
        detail={"product_id": str(product_id)},
    )
    await db.commit()


# ------------------------------------------------------------------ orders
def mask_handle(u: User | None) -> str:
    if u is None:
        return "***"
    base = u.handle or u.email.split("@", 1)[0]
    return base[:1] + "***"


async def list_orders(db: AsyncSession, r: Reseller, page: Page) -> dict[str, Any]:
    """No buyer_input, no delivery content, no access code: only what the reseller earns on."""
    base = select(Order).where(
        Order.reseller_id == r.id, Order.tenant_id == r.tenant_id, Order.kind == "goods"
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        await db.scalars(
            base.order_by(Order.created_at.desc(), Order.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    users = (
        {
            u.id: u
            for u in await db.scalars(
                select(User).where(
                    User.id.in_({o.buyer_id for o in rows}), User.tenant_id == r.tenant_id
                )
            )
        }
        if rows
        else {}
    )
    return page.wrap(
        [
            {
                "order_no": o.order_no,
                "title": o.title,
                "qty": o.qty,
                "amount": fmt(o.amount_minor),
                "amount_minor": o.amount_minor,
                "currency": o.currency,
                "markup_minor": o.reseller_markup_minor,
                "status": o.status,
                "created_at": iso(o.created_at),
                "buyer": mask_handle(users.get(o.buyer_id)),
            }
            for o in rows
        ],
        int(total),
    )


# ------------------------------------------------------------------ payouts
def payout_view(p: ResellerPayout) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "amount_minor": int(p.amount_minor),
        "currency": p.currency,
        "status": p.status,
        "note": p.note,
        "account": p.account_snapshot,
        "created_at": iso(p.created_at),
        "decided_at": iso(p.decided_at),
    }


async def list_payouts(db: AsyncSession, r: Reseller) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(ResellerPayout)
        .where(ResellerPayout.reseller_id == r.id, ResellerPayout.tenant_id == r.tenant_id)
        .order_by(ResellerPayout.created_at.desc())
        .limit(100)
    )
    return [payout_view(p) for p in rows]


async def request_payout(
    db: AsyncSession, user: User, amount_minor: int, ip: str | None
) -> dict[str, Any]:
    ts = await tenant_settings(db, user.tenant_id)
    rcfg = section(ts, "resellers")
    # the reseller row lock serialises concurrent requests (one open request, no overdraw)
    r = await db.scalar(
        select(Reseller)
        .where(Reseller.user_id == user.id, Reseller.tenant_id == user.tenant_id)
        .with_for_update()
    )
    if r is None or r.status == "pending_payment":
        raise NotFound("No reseller store")
    if r.status != "active":
        raise Forbidden("Your reseller store is not active")
    if not r.payout_account:
        raise Validation("Set a payout account first", code="payout_account_required")
    if amount_minor < rcfg["min_payout_minor"]:
        raise Validation(
            f"Minimum payout is {fmt(rcfg['min_payout_minor'])}", code="payout_below_minimum"
        )
    if await db.scalar(
        select(ResellerPayout.id).where(
            ResellerPayout.reseller_id == r.id, ResellerPayout.status == "requested"
        )
    ):
        raise Conflict("A payout request is already open", code="payout_open")
    avail = (await balances(db, r, rcfg))["available_minor"]
    if amount_minor > avail:
        raise Validation("Amount exceeds the available balance", code="insufficient_balance")
    p = ResellerPayout(
        tenant_id=r.tenant_id,
        reseller_id=r.id,
        amount_minor=amount_minor,
        status="requested",
        account_snapshot=dict(r.payout_account),
    )
    db.add(p)
    await db.flush()
    await audit(
        db,
        tenant_id=r.tenant_id,
        action="payout.requested",
        actor_id=user.id,
        actor_role=user.role,
        target=f"payout:{p.id}",
        ip=ip,
        detail={"amount_minor": amount_minor, "reseller_id": str(r.id)},
    )
    await db.commit()
    return payout_view(p)


# ------------------------------------------------------------------ admin
async def admin_view(
    db: AsyncSession, r: Reseller, rcfg: dict[str, Any], u: User | None
) -> dict[str, Any]:
    st = await stats(db, r, rcfg)
    return {
        "id": str(r.id),
        "slug": r.slug,
        "store_name": r.store_name,
        "store_url": store_url(r.slug, rcfg),
        "status": r.status,
        "user": {"id": str(r.user_id), "email": u.email if u else ""},
        "markup_kind": r.markup_kind,
        "markup_value": int(r.markup_value),
        "contact": r.contact,
        "activated_at": iso(r.activated_at),
        "created_at": iso(r.created_at),
        "signup_order_no": await signup_order_no(db, r),
        "stats": st,
    }


async def admin_list(
    db: AsyncSession, admin: User, page: Page, status: str | None, q: str | None
) -> dict[str, Any]:
    rcfg = section(await tenant_settings(db, admin.tenant_id), "resellers")
    stmt = select(Reseller).where(Reseller.tenant_id == admin.tenant_id)
    if status:
        stmt = stmt.where(Reseller.status == status)
    if q and q.strip():
        like = f"%{escape_like(q.strip().lower())}%"
        stmt = stmt.join(User, User.id == Reseller.user_id).where(
            or_(
                func.lower(Reseller.slug).like(like, escape="\\"),
                func.lower(Reseller.store_name).like(like, escape="\\"),
                User.email.like(like, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(Reseller.created_at.desc(), Reseller.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    users = (
        {
            u.id: u
            for u in await db.scalars(
                select(User).where(
                    User.id.in_({r.user_id for r in rows}), User.tenant_id == admin.tenant_id
                )
            )
        }
        if rows
        else {}
    )
    return page.wrap(
        [await admin_view(db, r, rcfg, users.get(r.user_id)) for r in rows], int(total)
    )


async def _get_admin(db: AsyncSession, admin: User, rid: uuid.UUID, lock: bool = False) -> Reseller:
    stmt = select(Reseller).where(Reseller.id == rid, Reseller.tenant_id == admin.tenant_id)
    r = await db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt
    )
    if r is None:
        raise NotFound("Reseller not found")
    return r


async def admin_detail(db: AsyncSession, admin: User, rid: uuid.UUID) -> dict[str, Any]:
    rcfg = section(await tenant_settings(db, admin.tenant_id), "resellers")
    r = await _get_admin(db, admin, rid)
    u = await db.scalar(select(User).where(User.id == r.user_id, User.tenant_id == admin.tenant_id))
    out = await admin_view(db, r, rcfg, u)
    out["payouts"] = await list_payouts(db, r)
    out["payout_account"] = r.payout_account
    return out


async def admin_patch(
    db: AsyncSession,
    res: Resources,
    admin: User,
    rid: uuid.UUID,
    data: dict[str, Any],
    ip: str | None,
) -> dict[str, Any]:
    rcfg = section(await tenant_settings(db, admin.tenant_id), "resellers")
    r0 = await _get_admin(db, admin, rid)
    # cancel_group rolls back (expiring ORM instances): capture plain values first
    r0_status, signup_oid, tid = r0.status, r0.signup_order_id, admin.tenant_id
    new = data.get("status")
    if new == "active" and r0_status == "pending_payment" and signup_oid is not None:
        # MONEY: waiving the fee closes the open sign-up order through cancel_group: the
        # gateway is re-queried FIRST, so an outstanding pay URL that was already paid
        # settles (-> 409 already_paid, store activated by that payment) instead of being
        # cancelled over money that arrived.
        o0 = await db.scalar(select(Order).where(Order.id == signup_oid, Order.tenant_id == tid))
        if o0 is not None and o0.status == "pending_payment":
            await cancel_svc.cancel_group(
                db,
                res,
                order_id=o0.id,
                actor=admin,
                by="staff",
                reason="store activated by admin",
                ip=ip,
            )
            await db.refresh(admin)
    # W1: order lock before the reseller lock (settlement locks order -> reseller)
    if signup_oid is not None:
        await db.scalar(
            select(Order.id).where(Order.id == signup_oid, Order.tenant_id == tid).with_for_update()
        )
    r = await _get_admin(db, admin, rid, lock=True)
    before = {"status": r.status, "markup": [r.markup_kind, int(r.markup_value)]}
    if new is not None and new != r.status:
        ok = (r.status, new) in {
            ("pending_payment", "active"),  # waive the fee
            ("active", "suspended"),
            ("suspended", "active"),
        }
        if not ok:
            raise InvalidState(f"Cannot move a store from {r.status} to {new}")
        if r.status == "pending_payment":
            r.activated_at = _now()
        r.status = new
    kind = data.get("markup_kind") or r.markup_kind
    value = data["markup_value"] if data.get("markup_value") is not None else int(r.markup_value)
    if "markup_kind" in data or "markup_value" in data:
        _check_markup(kind, value, rcfg)
        r.markup_kind, r.markup_value = kind, value
    r.updated_at = _now()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="reseller.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"reseller:{r.id}",
        before=before,
        after={"status": r.status, "markup": [r.markup_kind, int(r.markup_value)]},
        ip=ip,
        severity="warning" if new == "suspended" else "info",
    )
    await db.commit()
    storefront.invalidate()
    return await admin_detail(db, admin, rid)


async def admin_payouts(
    db: AsyncSession, admin: User, page: Page, status: str | None
) -> dict[str, Any]:
    stmt = select(ResellerPayout).where(ResellerPayout.tenant_id == admin.tenant_id)
    if status:
        stmt = stmt.where(ResellerPayout.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(ResellerPayout.created_at.desc(), ResellerPayout.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    rs = (
        {
            r.id: r
            for r in await db.scalars(
                select(Reseller).where(
                    Reseller.id.in_({p.reseller_id for p in rows}),
                    Reseller.tenant_id == admin.tenant_id,
                )
            )
        }
        if rows
        else {}
    )
    items = []
    for p in rows:
        v = payout_view(p)
        r = rs.get(p.reseller_id)
        v["reseller"] = {
            "id": str(p.reseller_id),
            "slug": r.slug if r else "",
            "store_name": r.store_name if r else "",
        }
        items.append(v)
    return page.wrap(items, int(total))


async def decide_payout(
    db: AsyncSession,
    admin: User,
    payout_id: uuid.UUID,
    action: str,
    note: str,
    ip: str | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    ts = await tenant_settings(db, admin.tenant_id)
    rcfg = section(ts, "resellers")
    p0 = await db.scalar(
        select(ResellerPayout).where(
            ResellerPayout.id == payout_id, ResellerPayout.tenant_id == admin.tenant_id
        )
    )
    if p0 is None:
        raise NotFound("Payout not found")
    # same lock order as request_payout: reseller row, then the payout row
    r = await db.scalar(
        select(Reseller)
        .where(Reseller.id == p0.reseller_id, Reseller.tenant_id == admin.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    p = await db.scalar(
        select(ResellerPayout)
        .where(ResellerPayout.id == payout_id, ResellerPayout.tenant_id == admin.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)  # fresh status: the unlocked read is stale
    )
    assert p is not None and r is not None
    if p.status != "requested":
        raise InvalidState("This payout was already decided")
    before = {"status": p.status}
    forced = False
    if action == "paid":
        # MONEY: re-check the withdrawable balance NOW (orders may have been refunded since the
        # request): the payout, excluded from "requested", must fit what is still available.
        avail = (await balances(db, r, rcfg, exclude_payout=p.id, raw=True))["available_minor"]
        if int(p.amount_minor) > avail:
            if not force:
                raise Conflict(
                    "The payout exceeds the balance available now", code="insufficient_balance"
                )
            forced = True
        # reseller_payable +amount (debit: liability settled) / cash:payouts -amount
        await post_txn(
            db,
            tenant_id=p.tenant_id,
            currency=p.currency,
            order_id=None,
            memo=f"reseller payout {p.id}",
            lines=[
                Line(f"reseller_payable:{p.reseller_id}", int(p.amount_minor)),
                Line("cash:payouts", -int(p.amount_minor)),
            ],
        )
    p.status = action
    p.note = note.strip() or None
    p.decided_by, p.decided_at, p.updated_at = admin.id, _now(), _now()
    await audit(
        db,
        tenant_id=p.tenant_id,
        action=f"payout.{action}",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"payout:{p.id}",
        before=before,
        after={"status": p.status},
        ip=ip,
        detail={
            "amount_minor": int(p.amount_minor),
            "reseller_id": str(p.reseller_id),
            **({"forced_over_balance": True} if forced else {}),
        },
        severity="critical" if forced else ("warning" if action == "paid" else "info"),
    )
    await db.commit()
    return payout_view(p)
