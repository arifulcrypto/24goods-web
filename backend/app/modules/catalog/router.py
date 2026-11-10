from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, get_db, get_lang, get_page, get_res, get_tenant_id
from app.core.resources import Resources
from app.core.storefront import Storefront, get_storefront
from app.modules.catalog import service
from app.modules.catalog.categories import enabled_categories, public_view

router = APIRouter(tags=["catalog"])


@router.get("/categories")
@router.get("/catalog/categories")
async def categories(
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, str | None]]:
    return [public_view(c, lang) for c in await enabled_categories(db, tenant_id)]


@router.get("/products")
async def products(
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=32),
    ids: str | None = Query(None, max_length=1000),
    sort: Literal["manual", "sales", "price_asc", "price_desc", "newest"] | None = None,
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    if ids is not None:  # recently viewed: summaries in the requested order
        return {
            "items": await service.products_by_ids(db, lang, service.parse_ids(ids), tenant_id, sf)
        }
    return await service.list_products(db, lang, page, q, category, tenant_id, sf, sort)


@router.get("/products/recommended")
async def recommended(
    for_: str | None = Query(None, alias="for", max_length=36),
    recent: str | None = Query(None, max_length=1000),
    limit: int = Query(8, ge=1, le=20),
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    for_id = service.parse_ids(for_, 1)[0] if for_ else None
    items = await service.recommended(
        db, res.redis, lang, tenant_id, for_id, service.parse_ids(recent), limit, sf
    )
    return {"items": items}


@router.get("/products/{product_id}")
async def product(
    product_id: uuid.UUID,
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    return await service.product_detail(db, lang, product_id, tenant_id, sf)


@router.get("/support/status")
async def support_status(db: AsyncSession = Depends(get_db)) -> dict:
    return await service.support_status(db)
