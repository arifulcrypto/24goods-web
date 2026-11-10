from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import Conflict, InvalidState, NotFound, Validation
from app.core.markdown import render_i18n
from app.core.money import MoneyError, parse_amount
from app.models import Attachment, Product, User
from app.modules.admin import variants as variant_svc
from app.modules.admin.schemas import BulkProductsIn, ProductIn, ProductPatch
from app.modules.catalog.categories import category_slugs
from app.modules.catalog.service import apply_search, prepare_search, stock_counts
from app.modules.common.views import admin_product_view


def _floor(value: str) -> int:
    if value.strip() in ("0", "0.0", "0.00"):
        return 0
    return parse_amount(value)


async def _check_images(db: AsyncSession, urls: list[str], admin: User) -> None:
    """Only images uploaded in this tenant (RLS) as product images, or by this admin.

    Accepted images become public product images; other users' chat uploads are never
    accepted, so a PATCH cannot publish someone's private attachment.
    """
    ids = {uuid.UUID(u.rsplit("/", 1)[1]) for u in urls}
    if not ids:
        return
    rows = list(
        await db.scalars(
            select(Attachment).where(
                Attachment.id.in_(ids),
                Attachment.kind == "image",
                or_(Attachment.purpose == "product", Attachment.owner_id == admin.id),
            )
        )
    )
    if len(rows) != len(ids):
        raise Validation("images must reference images uploaded to this store")
    for att in rows:
        att.purpose, att.is_public = "product", True


async def ensure_category(db: AsyncSession, tenant_id: uuid.UUID, slug: str) -> None:
    """Products may only use a category of their own tenant (any enabled state)."""
    if slug not in await category_slugs(db, tenant_id):
        raise Validation("unknown category")


def _summary(p: Product) -> dict[str, Any]:
    return {
        "price_minor": p.price_minor,
        "floor_price_minor": p.floor_price_minor,
        "status": p.status,
        "delivery_mode": p.delivery_mode,
        "after_sales_hours": p.after_sales_hours,
        "after_sales_months": p.after_sales_months,
        "subscription": p.subscription,
        "manual_stock": p.manual_stock,
        "redeem_url": p.redeem_url,
        "archived_at": p.archived_at.isoformat() if p.archived_at else None,
    }


async def view(db: AsyncSession, p: Product) -> dict[str, Any]:
    stock = (await stock_counts(db, [p.id])).get(p.id) if p.delivery_mode == "auto_card" else None
    out = admin_product_view(p, stock)
    out["variants"] = (await variant_svc.admin_views(db, p.tenant_id, [p]))[p.id]
    return out


async def list_products(db: AsyncSession, page: Page, q: str | None, status: str | None) -> dict:
    stmt = select(Product)
    if status:
        stmt = stmt.where(Product.status == status)
    else:  # archived products only show under the explicit "archived" filter
        stmt = stmt.where(Product.status != "archived")
    stmt = apply_search(stmt, q)
    await prepare_search(db, q)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(
                Product.pinned_at.desc().nullslast(),
                Product.group_sort,
                Product.created_at.desc(),
                Product.id,
            )
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    stock = await stock_counts(db, [p.id for p in rows if p.delivery_mode == "auto_card"])
    vviews = await variant_svc.admin_views(db, rows[0].tenant_id, rows) if rows else {}
    outs = [admin_product_view(p, stock.get(p.id)) for p in rows]
    for p, out in zip(rows, outs, strict=True):
        out["variants"] = vviews.get(p.id, [])
    return page.wrap(outs, int(total))


async def create(db: AsyncSession, body: ProductIn, admin: User, ip: str | None) -> Product:
    try:
        price = parse_amount(body.price)
        floor = _floor(body.floor_price)
    except MoneyError as exc:
        raise Validation(f"price: {exc}") from None
    if floor > price:
        raise Validation("floor_price must not exceed price")
    await _check_images(db, body.images, admin)
    await ensure_category(db, admin.tenant_id, body.category)
    p = Product(
        tenant_id=admin.tenant_id,
        title_i18n=body.title_i18n,
        description_i18n=body.description_i18n or {"en": "", "zh-CN": ""},
        description_html_i18n=render_i18n(body.description_i18n or {"en": "", "zh-CN": ""}),
        buyer_input=body.buyer_input.model_dump() if body.buyer_input else None,
        images=body.images,
        price_minor=price,
        floor_price_minor=floor,
        currency=body.currency,
        delivery_mode=body.delivery_mode,
        category=body.category,
        status=body.status,
        sold=0,
        created_by=admin.id,
        delivery_format=body.delivery_format,
        delivery_separator=body.delivery_separator,
        redeem_url=body.redeem_url,
        usage_i18n=body.usage_i18n,
        usage_html_i18n=render_i18n(body.usage_i18n),
        after_sales_hours=body.after_sales_hours,
        after_sales_months=body.after_sales_months if body.after_sales_hours is None else None,
        subscription=body.subscription.model_dump() if body.subscription else None,
        group_sort=body.group_sort,
        delivery_eta_hours=body.delivery_eta_hours,
        cost_minor=body.cost_minor,
        source_url=body.source_url,
        manual_stock=(
            (1 if "manual_stock" not in body.model_fields_set else body.manual_stock)
            if body.delivery_mode == "manual"
            else None
        ),
    )
    db.add(p)
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="product.created",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"product:{p.id}",
        after=_summary(p),
        ip=ip,
    )
    await db.commit()
    await db.refresh(p)
    return p


async def get(db: AsyncSession, product_id: uuid.UUID, *, lock: bool = False) -> Product:
    stmt = select(Product).where(Product.id == product_id)
    if lock:
        stmt = stmt.with_for_update()
    p = await db.scalar(stmt)
    if p is None:
        raise NotFound("Product not found")
    return p


async def patch(
    db: AsyncSession, product_id: uuid.UUID, body: ProductPatch, admin: User, ip: str | None
) -> Product:
    p = await get(db, product_id, lock=True)
    before = _summary(p)
    data = body.model_dump(exclude_unset=True)
    try:
        price = parse_amount(data["price"]) if data.get("price") is not None else p.price_minor
        floor = (
            _floor(data["floor_price"])
            if data.get("floor_price") is not None
            else p.floor_price_minor
        )
    except MoneyError as exc:
        raise Validation(f"price: {exc}") from None
    if floor > price:
        raise Validation("floor_price must not exceed price")
    if data.get("images") is not None:
        await _check_images(db, data["images"], admin)
        p.images = data["images"]
    if data.get("category") is not None:
        await ensure_category(db, p.tenant_id, data["category"])
    if data.get("title_i18n") is not None:
        p.title_i18n = data["title_i18n"]
    if data.get("description_i18n") is not None:
        p.description_i18n = data["description_i18n"]
        p.description_html_i18n = render_i18n(data["description_i18n"])
    if data.get("delivery_mode") == "manual" and p.delivery_mode != "manual":
        # switching to manual: finite default like create (unless the admin sent a value)
        if "manual_stock" not in data:
            p.manual_stock = 1
    for key in ("delivery_mode", "category", "status", "delivery_separator", "group_sort"):
        if data.get(key) is not None:
            setattr(p, key, data[key])
    # nullable fields: an explicit null clears them (site default / no format / no link)
    for key in (
        "delivery_format",
        "redeem_url",
        "after_sales_hours",
        "delivery_eta_hours",
        "cost_minor",
        "source_url",
        "manual_stock",
    ):
        if key in data:
            setattr(p, key, data[key])
    # setting one after-sales unit clears the other; both at once is ambiguous
    if data.get("after_sales_hours") is not None and data.get("after_sales_months") is not None:
        raise Validation("set either after_sales_hours or after_sales_months, not both")
    if data.get("after_sales_hours") is not None:
        p.after_sales_months = None
    elif data.get("after_sales_months") is not None:
        p.after_sales_hours = None
    if "after_sales_months" in data:
        p.after_sales_months = data["after_sales_months"]
    if "subscription" in data:  # explicit null = not a subscription
        sb = body.subscription
        p.subscription = sb.model_dump() if sb is not None else None
    if "buyer_input" in data:  # explicit null clears the popup
        bi = body.buyer_input
        p.buyer_input = bi.model_dump() if bi is not None else None
    if data.get("usage_i18n") is not None:
        p.usage_i18n = data["usage_i18n"]
        p.usage_html_i18n = render_i18n(data["usage_i18n"])
    p.price_minor, p.floor_price_minor = price, floor
    await audit(
        db,
        tenant_id=p.tenant_id,
        action="product.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"product:{p.id}",
        before=before,
        after=_summary(p),
        ip=ip,
        detail={"fields": sorted(data)},
    )
    await db.commit()
    await db.refresh(p)
    return p


async def _in_use(db: AsyncSession, p: Product) -> bool:
    """Any row that references the product (hard delete would orphan or violate an FK)."""
    row = await db.scalar(
        text(
            """SELECT
                 EXISTS (SELECT 1 FROM orders WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM card_stock WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM card_batches WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM conversations WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM quotes WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM reseller_prices WHERE product_id = :pid)
              OR EXISTS (SELECT 1 FROM promo_codes WHERE :pid = ANY(product_ids))"""
        ),
        {"pid": p.id},
    )
    return bool(row)


def _archive(p: Product) -> None:
    p.status = "archived"
    p.archived_at = datetime.now(timezone.utc)


async def archive(
    db: AsyncSession, product_id: uuid.UUID, admin: User, ip: str | None, *, hard: bool = False
) -> None:
    """DELETE /admin/products/{id}: archive (hidden everywhere), or ``hard`` delete the row
    when nothing references it (409 ``product_in_use`` otherwise)."""
    p = await get(db, product_id, lock=True)
    before = _summary(p)
    if hard:
        if await _in_use(db, p):
            raise Conflict(
                "Product is referenced by orders, stock or other records; archive it",
                code="product_in_use",
            )
        await db.execute(
            delete(Product).where(Product.id == p.id, Product.tenant_id == p.tenant_id)
        )
        await audit(
            db,
            tenant_id=p.tenant_id,
            action="product.hard_deleted",
            actor_id=admin.id,
            actor_role=admin.role,
            target=f"product:{product_id}",
            before=before,
            ip=ip,
        )
    else:
        _archive(p)
        await audit(
            db,
            tenant_id=p.tenant_id,
            action="product.archived",
            actor_id=admin.id,
            actor_role=admin.role,
            target=f"product:{p.id}",
            before=before,
            after=_summary(p),
            ip=ip,
        )
    await db.commit()


async def restore(db: AsyncSession, product_id: uuid.UUID, admin: User, ip: str | None) -> Product:
    p = await get(db, product_id, lock=True)
    if p.status != "archived":
        raise InvalidState("Product is not archived")
    before = _summary(p)
    p.status, p.archived_at = "draft", None
    await audit(
        db,
        tenant_id=p.tenant_id,
        action="product.restored",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"product:{p.id}",
        before=before,
        after=_summary(p),
        ip=ip,
    )
    await db.commit()
    await db.refresh(p)
    return p


async def bulk(db: AsyncSession, body: BulkProductsIn, admin: User, ip: str | None) -> int:
    """publish | unpublish | archive | restore | set_category over up to 200 products.
    Rows that cannot take the action (e.g. restore of a non-archived product) are skipped."""
    ids = list(dict.fromkeys(body.ids))
    if body.action == "set_category":
        if not body.category:
            raise Validation("category is required")
        await ensure_category(db, admin.tenant_id, body.category)
    rows = list(
        await db.scalars(
            select(Product)
            .where(Product.id.in_(ids), Product.tenant_id == admin.tenant_id)
            .order_by(Product.id)
            .with_for_update()
        )
    )
    changed: list[str] = []
    for p in rows:
        if body.action == "publish" and p.status == "draft":
            p.status = "active"
        elif body.action == "unpublish" and p.status == "active":
            p.status = "draft"
        elif body.action == "archive" and p.status != "archived":
            _archive(p)
        elif body.action == "restore" and p.status == "archived":
            p.status, p.archived_at = "draft", None
        elif body.action == "set_category" and p.category != body.category:
            p.category = body.category  # type: ignore[assignment]
        else:
            continue
        changed.append(str(p.id))
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="product.bulk",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"products:{len(changed)}",
        ip=ip,
        detail={"action": body.action, "category": body.category, "ids": changed},
    )
    await db.commit()
    return len(changed)
