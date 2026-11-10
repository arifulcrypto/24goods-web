"""Pin and manual order of products/categories + the storefront default sort (review-v11 W3)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apitoken import require_scope
from app.core.audit import audit
from app.core.deps import client_ip, get_db, require_admin
from app.core.errors import NotFound, Validation
from app.models import Category, Product, Tenant, User
from app.modules.catalog import categories as category_cache
from app.modules.catalog import sorting

router = APIRouter(prefix="/admin", tags=["admin"])

STEP = 10


class PinIn(BaseModel):
    pinned: bool


class ReorderIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class CatalogSettingsIn(BaseModel):
    default_sort: Literal["manual", "sales", "price_asc", "price_desc", "newest"]


async def _locked(
    db: AsyncSession,
    model: type[Product] | type[Category],
    tenant_id: uuid.UUID,
    ids: list[uuid.UUID],
) -> dict[uuid.UUID, Any]:
    rows = list(
        await db.scalars(
            select(model).where(model.tenant_id == tenant_id, model.id.in_(ids)).with_for_update()
        )
    )
    return {r.id: r for r in rows}


async def _pin(
    db: AsyncSession,
    admin: User,
    model: Any,
    ident: uuid.UUID,
    pinned: bool,
    kind: str,
    ip: str | None,
) -> dict:
    rows = await _locked(db, model, admin.tenant_id, [ident])
    row = rows.get(ident)
    if row is None:
        raise NotFound(f"{kind.capitalize()} not found")
    before = row.pinned_at is not None
    if pinned and row.pinned_at is None:
        row.pinned_at = func.now()
    elif not pinned:
        row.pinned_at = None
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action=f"{kind}.pinned" if pinned else f"{kind}.unpinned",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"{kind}:{ident}",
        before={"pinned": before},
        after={"pinned": pinned},
        ip=ip,
    )
    await db.commit()
    return {"id": str(ident), "pinned": pinned}


async def _reorder(
    db: AsyncSession,
    admin: User,
    model: Any,
    attr: str,
    ids: list[uuid.UUID],
    kind: str,
    ip: str | None,
) -> dict:
    if len(set(ids)) != len(ids):
        raise Validation("ids must be unique")
    rows = await _locked(db, model, admin.tenant_id, ids)
    if len(rows) != len(ids):
        raise NotFound(f"{kind.capitalize()} not found")
    before = {str(i): getattr(rows[i], attr) for i in ids}
    after: dict[str, int] = {}
    for pos, ident in enumerate(ids, start=1):
        setattr(rows[ident], attr, pos * STEP)
        after[str(ident)] = pos * STEP
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action=f"{kind}.reordered",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"{kind}:bulk",
        before=before,
        after=after,
        ip=ip,
        detail={"count": len(ids)},
    )
    await db.commit()
    return {"count": len(ids), "order": after}


@router.put("/products/{product_id}/pin", dependencies=[Depends(require_scope("products:write"))])
async def pin_product(
    product_id: uuid.UUID,
    body: PinIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _pin(db, admin, Product, product_id, body.pinned, "product", client_ip(request))


@router.post("/products/reorder", dependencies=[Depends(require_scope("products:write"))])
async def reorder_products(
    body: ReorderIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await _reorder(db, admin, Product, "group_sort", body.ids, "product", client_ip(request))


@router.put("/categories/{category_id}/pin")
async def pin_category(
    category_id: uuid.UUID,
    body: PinIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    out = await _pin(db, admin, Category, category_id, body.pinned, "category", client_ip(request))
    category_cache.invalidate(admin.tenant_id)
    return out


@router.post("/categories/reorder")
async def reorder_categories(
    body: ReorderIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    out = await _reorder(db, admin, Category, "sort", body.ids, "category", client_ip(request))
    category_cache.invalidate(admin.tenant_id)
    return out


@router.get("/catalog/settings")
async def get_catalog_settings(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    ts = dict(await db.scalar(select(Tenant.settings).where(Tenant.id == admin.tenant_id)) or {})
    return {"default_sort": sorting.default_sort(ts), "sorts": list(sorting.SORTS)}


@router.put("/catalog/settings")
async def put_catalog_settings(
    body: CatalogSettingsIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == admin.tenant_id).with_for_update())
    if tenant is None:
        raise NotFound("Tenant not found")
    cur = dict(tenant.settings or {})
    before = sorting.default_sort(cur)
    catalog = dict(cur.get("catalog") or {}) if isinstance(cur.get("catalog"), dict) else {}
    catalog["default_sort"] = body.default_sort
    tenant.settings = {**cur, "catalog": catalog}
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="catalog.sort_default",
        actor_id=admin.id,
        actor_role=admin.role,
        target="tenant:catalog",
        before={"default_sort": before},
        after={"default_sort": body.default_sort},
        ip=client_ip(request),
    )
    await db.commit()
    return {"default_sort": body.default_sort, "sorts": list(sorting.SORTS)}
