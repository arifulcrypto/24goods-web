from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import NotFound, Validation
from app.core.markdown import render_i18n
from app.core.money import MoneyError, parse_amount
from app.models import Attachment, Product, User
from app.modules.admin.schemas import ProductIn, ProductPatch
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


def _summary(p: Product) -> dict[str, Any]:
    return {
        "price_minor": p.price_minor,
        "floor_price_minor": p.floor_price_minor,
        "status": p.status,
        "delivery_mode": p.delivery_mode,
        "after_sales_hours": p.after_sales_hours,
    }


async def view(db: AsyncSession, p: Product) -> dict[str, Any]:
    stock = (await stock_counts(db, [p.id])).get(p.id) if p.delivery_mode == "auto_card" else None
    return admin_product_view(p, stock)


async def list_products(db: AsyncSession, page: Page, q: str | None, status: str | None) -> dict:
    stmt = select(Product)
    if status:
        stmt = stmt.where(Product.status == status)
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
    stock = await stock_counts(db, [p.id for p in rows if p.delivery_mode == "auto_card"])
    return page.wrap([admin_product_view(p, stock.get(p.id)) for p in rows], int(total))


async def create(db: AsyncSession, body: ProductIn, admin: User, ip: str | None) -> Product:
    try:
        price = parse_amount(body.price)
        floor = _floor(body.floor_price)
    except MoneyError as exc:
        raise Validation(f"price: {exc}") from None
    if floor > price:
        raise Validation("floor_price must not exceed price")
    await _check_images(db, body.images, admin)
    p = Product(
        tenant_id=admin.tenant_id,
        title_i18n=body.title_i18n,
        description_i18n=body.description_i18n or {"en": "", "zh-CN": ""},
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
        group_sort=body.group_sort,
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
    if data.get("title_i18n") is not None:
        p.title_i18n = data["title_i18n"]
    if data.get("description_i18n") is not None:
        p.description_i18n = data["description_i18n"]
    for key in ("delivery_mode", "category", "status", "delivery_separator", "group_sort"):
        if data.get(key) is not None:
            setattr(p, key, data[key])
    # nullable fields: an explicit null clears them (site default / no format / no link)
    for key in ("delivery_format", "redeem_url", "after_sales_hours"):
        if key in data:
            setattr(p, key, data[key])
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


async def soft_delete(db: AsyncSession, product_id: uuid.UUID, admin: User, ip: str | None) -> None:
    p = await get(db, product_id, lock=True)
    before = _summary(p)
    p.status = "draft"
    await audit(
        db,
        tenant_id=p.tenant_id,
        action="product.deleted",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"product:{p.id}",
        before=before,
        after=_summary(p),
        ip=ip,
    )
    await db.commit()
