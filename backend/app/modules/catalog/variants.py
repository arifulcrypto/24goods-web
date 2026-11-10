"""Product variants / SKUs (review-v11 W1).

A product with at least one ACTIVE variant is bought through a variant: price, manual stock,
card stock and (optionally) the after-sales / subscription terms come from it. A product
without active variants behaves exactly as before. All money is integer minor units and the
price is always the server's (invariant 3); every query filters by ``tenant_id`` (invariant 4).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, OutOfStock, Validation
from app.core.i18n import pick
from app.core.money import fmt
from app.models import CardStock, Product, ProductVariant
from app.modules.common.sitedefaults import resolve_after_sales_hours, resolve_after_sales_months
from app.modules.reseller.pricing import Pricer


def _claimable() -> tuple:
    """Same predicate as ``catalog.service.claimable`` (kept local: no import cycle)."""
    t = CardStock.__table__.c
    return (t.status == "available", (t.expires_at.is_(None)) | (t.expires_at > func.now()))


async def variants_for(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    product_ids: Iterable[uuid.UUID],
    *,
    include_archived: bool = False,
) -> dict[uuid.UUID, list[ProductVariant]]:
    ids = list(set(product_ids))
    out: dict[uuid.UUID, list[ProductVariant]] = {pid: [] for pid in ids}
    if not ids:
        return out
    stmt = select(ProductVariant).where(
        ProductVariant.tenant_id == tenant_id, ProductVariant.product_id.in_(ids)
    )
    if not include_archived:
        stmt = stmt.where(ProductVariant.status == "active")
    rows = await db.scalars(stmt.order_by(ProductVariant.sort, ProductVariant.created_at))
    for v in rows:
        out[v.product_id].append(v)
    return out


def pick_variant(rows: list[ProductVariant], variant_id: uuid.UUID | None) -> ProductVariant | None:
    """``rows`` = ALL variants (any status) of one product. Enforces the buy rules:
    a product with an active variant requires one (422 ``variant_required``); an unknown /
    foreign variant is a 404; an archived one a 422 ``variant_archived``."""
    active = [v for v in rows if v.status == "active"]
    if variant_id is None:
        if active:
            raise Validation("variant_id is required for this product", code="variant_required")
        return None
    v = next((x for x in rows if x.id == variant_id), None)
    if v is None:
        raise NotFound("Variant not found")
    if v.status != "active":
        raise Validation("This variant is no longer available", code="variant_archived")
    return v


async def resolve(
    db: AsyncSession, tenant_id: uuid.UUID, product: Product, variant_id: uuid.UUID | None
) -> ProductVariant | None:
    rows = (await variants_for(db, tenant_id, [product.id], include_archived=True))[product.id]
    return pick_variant(rows, variant_id)


async def card_counts(
    db: AsyncSession, tenant_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    ids = list(variant_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(CardStock.variant_id, func.count(CardStock.id))
        .where(CardStock.tenant_id == tenant_id, CardStock.variant_id.in_(ids), *_claimable())
        .group_by(CardStock.variant_id)
    )
    out = {i: 0 for i in ids}
    out.update({vid: int(n) for vid, n in rows.all()})
    return out


async def reserve_variant_stock(
    db: AsyncSession, tenant_id: uuid.UUID, variant_id: uuid.UUID, qty: int
) -> int:
    """Manual variants with a finite ``manual_stock``: decrement under the variant row lock.
    NULL = unlimited. Returns the quantity reserved (0 when unlimited), stored on the order
    as ``stock_reserved`` and returned by ``release_manual_stock``."""
    row = (
        await db.execute(
            select(ProductVariant.status, ProductVariant.manual_stock)
            .where(ProductVariant.id == variant_id, ProductVariant.tenant_id == tenant_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise NotFound("Variant not found")
    status, stock = row
    if status != "active":
        raise Validation("This variant is no longer available", code="variant_archived")
    if stock is None:
        return 0
    if stock < qty:
        raise OutOfStock("Not enough stock", extra={"variant_id": str(variant_id)})
    await db.execute(
        ProductVariant.__table__.update()
        .where(ProductVariant.id == variant_id, ProductVariant.tenant_id == tenant_id)
        .values(manual_stock=stock - qty)
    )
    return qty


async def lock_variants(
    db: AsyncSession, tenant_id: uuid.UUID, variant_ids: Iterable[uuid.UUID]
) -> None:
    """Lock variant rows in id order (after the product rows: buyer -> products -> variants ->
    promo), so concurrent carts never deadlock."""
    ids = sorted(set(variant_ids))
    if ids:
        await db.execute(
            select(ProductVariant.id)
            .where(ProductVariant.id.in_(ids), ProductVariant.tenant_id == tenant_id)
            .order_by(ProductVariant.id)
            .with_for_update()
        )


# --------------------------------------------------------------------------- terms
def effective_terms(p: Product, v: ProductVariant | None) -> dict[str, Any]:
    """Variant term fields that are set win; NULL inherits the product's. Setting either
    after-sales unit on the variant replaces the product's pair as a whole."""
    if v is None:
        return {
            "after_sales_hours": p.after_sales_hours,
            "after_sales_months": p.after_sales_months,
            "subscription": p.subscription,
        }
    own_after_sales = v.after_sales_hours is not None or v.after_sales_months is not None
    return {
        "after_sales_hours": v.after_sales_hours if own_after_sales else p.after_sales_hours,
        "after_sales_months": v.after_sales_months if own_after_sales else p.after_sales_months,
        "subscription": v.subscription if v.subscription is not None else p.subscription,
    }


# --------------------------------------------------------------------------- views
def variant_stock(p: Product, v: ProductVariant, cards: dict[uuid.UUID, int]) -> int | None:
    if p.delivery_mode == "auto_card":
        return cards.get(v.id, 0)
    return v.manual_stock  # None = unlimited


def buyer_variant_view(
    p: Product,
    v: ProductVariant,
    lang: str,
    cards: dict[uuid.UUID, int],
    ts: dict[str, Any] | None,
    pricer: Pricer | None,
) -> dict[str, Any]:
    terms = effective_terms(p, v)
    unit = pricer.unit(p, v.price_minor) if pricer else v.price_minor
    return {
        "id": str(v.id),
        "name": pick(v.name_i18n, lang),
        "price": fmt(unit),
        "stock": variant_stock(p, v, cards),
        "after_sales_hours": resolve_after_sales_hours(
            terms["after_sales_hours"], ts, terms["after_sales_months"]
        ),
        "after_sales_months": resolve_after_sales_months(
            terms["after_sales_hours"], terms["after_sales_months"], ts
        ),
        "subscription": terms["subscription"] is not None,
    }


async def decorate(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    items: list[tuple[Product, dict[str, Any]]],
    lang: str,
    ts: dict[str, Any] | None,
    pricer: Pricer | None,
) -> None:
    """Add ``has_variants`` / ``variants`` to the catalog payloads and, for products that have
    active variants, show "from" price (min variant unit price) and the summed stock."""
    if not items:
        return
    by_product = await variants_for(db, tenant_id, [p.id for p, _ in items])
    auto_ids = [v.id for p, _ in items if p.delivery_mode == "auto_card" for v in by_product[p.id]]
    cards = await card_counts(db, tenant_id, auto_ids)
    for p, out in items:
        vs = by_product[p.id]
        out["has_variants"] = bool(vs)
        out["variants"] = [buyer_variant_view(p, v, lang, cards, ts, pricer) for v in vs]
        if not vs:
            continue
        out["price"] = fmt(
            min(pricer.unit(p, v.price_minor) if pricer else v.price_minor for v in vs)
        )
        out["price_from"] = len({v.price_minor for v in vs}) > 1
        stocks = [variant_stock(p, v, cards) for v in vs]
        if p.delivery_mode in ("manual", "auto_card"):
            out["stock"] = None if any(s is None for s in stocks) else sum(s for s in stocks if s)


# --------------------------------------------------------------------------- admin
def admin_variant_view(
    v: ProductVariant, p: Product, cards: dict[uuid.UUID, int]
) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "name_i18n": {"en": v.name_i18n.get("en", ""), "zh-CN": v.name_i18n.get("zh-CN", "")},
        "price": fmt(v.price_minor),
        "floor_price": fmt(v.floor_price_minor),
        "manual_stock": v.manual_stock,
        "stock": variant_stock(p, v, cards),
        "sort": v.sort,
        "status": v.status,
        "after_sales_months": v.after_sales_months,
        "after_sales_hours": v.after_sales_hours,
        "subscription": v.subscription,
        "sold": v.sold,
    }
