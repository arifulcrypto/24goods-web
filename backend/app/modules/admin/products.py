from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.deps import Page
from app.core.errors import NotFound, Validation
from app.core.money import MoneyError, parse_amount
from app.models import Attachment, Product, User
from app.modules.admin.schemas import ProductIn, ProductPatch
from app.modules.catalog.service import apply_search, stock_counts
from app.modules.common.views import admin_product_view


def _floor(value: str) -> int:
    if value.strip() in ("0", "0.0", "0.00"):
        return 0
    return parse_amount(value)


async def _check_images(db: AsyncSession, urls: list[str]) -> None:
    ids = [uuid.UUID(u.rsplit("/", 1)[1]) for u in urls]
    if not ids:
        return
    n = await db.scalar(
        select(func.count(Attachment.id)).where(
            Attachment.id.in_(ids), Attachment.is_public.is_(True), Attachment.kind == "image"
        )
    )
    if int(n or 0) != len(set(ids)):
        raise Validation("images must reference uploaded product images")


def _summary(p: Product) -> dict[str, Any]:
    return {
        "price_minor": p.price_minor,
        "floor_price_minor": p.floor_price_minor,
        "status": p.status,
        "delivery_mode": p.delivery_mode,
    }


async def view(db: AsyncSession, p: Product) -> dict[str, Any]:
    stock = (await stock_counts(db, [p.id])).get(p.id) if p.delivery_mode == "auto_card" else None
    return admin_product_view(p, stock)


async def list_products(db: AsyncSession, page: Page, q: str | None, status: str | None) -> dict:
    stmt = select(Product)
    if status:
        stmt = stmt.where(Product.status == status)
    stmt = apply_search(stmt, q)
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
    await _check_images(db, body.images)
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
        await _check_images(db, data["images"])
        p.images = data["images"]
    if data.get("title_i18n") is not None:
        p.title_i18n = data["title_i18n"]
    if data.get("description_i18n") is not None:
        p.description_i18n = data["description_i18n"]
    for key in ("delivery_mode", "category", "status"):
        if data.get(key) is not None:
            setattr(p, key, data[key])
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
