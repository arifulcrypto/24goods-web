from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, get_db, get_lang, get_page
from app.core.i18n import pick
from app.modules.catalog import service
from app.modules.catalog.categories import CATEGORIES

router = APIRouter(tags=["catalog"])


@router.get("/categories")
async def categories(lang: str = Depends(get_lang)) -> list[dict[str, str]]:
    return [{"key": k, "name": pick(v, lang)} for k, v in CATEGORIES.items()]


@router.get("/products")
async def products(
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=32),
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.list_products(db, lang, page, q, category)


@router.get("/products/{product_id}")
async def product(
    product_id: uuid.UUID, lang: str = Depends(get_lang), db: AsyncSession = Depends(get_db)
) -> dict:
    return await service.product_detail(db, lang, product_id)


@router.get("/support/status")
async def support_status(db: AsyncSession = Depends(get_db)) -> dict:
    return await service.support_status(db)
