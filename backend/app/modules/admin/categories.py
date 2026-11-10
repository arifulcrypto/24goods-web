"""Admin category management (review-v5 §2). Slug is immutable; delete only when unused."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apitoken import require_scope
from app.core.audit import audit
from app.core.deps import client_ip, get_db, require_admin, require_staff
from app.core.errors import Conflict, NotFound
from app.models import Category, Product, User
from app.modules.catalog import categories as cache

router = APIRouter(prefix="/admin/categories", tags=["admin"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LOCALES = ("en", "zh-CN")


def _names(v: dict[str, str]) -> dict[str, str]:
    if set(v) - set(LOCALES):
        raise ValueError("only 'en' and 'zh-CN' keys are allowed")
    out = {k: (v.get(k) or "").strip() for k in LOCALES}
    if not out["en"]:
        raise ValueError("'en' is required")
    if any(len(x) > 60 for x in out.values()):
        raise ValueError("at most 60 characters")
    return out


def _icon(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if len(v) > 32 or any(ord(c) < 32 for c in v):
        raise ValueError("icon is at most 32 characters")
    return v


class CategoryIn(BaseModel):
    slug: str = Field(max_length=32)
    name_i18n: dict[str, str]
    icon: str | None = None
    sort: int = Field(0, ge=-10000, le=10000)
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug must match [a-z0-9][a-z0-9_-]{0,31}")
        return v

    @field_validator("name_i18n")
    @classmethod
    def _n(cls, v: dict[str, str]) -> dict[str, str]:
        return _names(v)

    @field_validator("icon")
    @classmethod
    def _i(cls, v: str | None) -> str | None:
        return _icon(v)


class CategoryPatch(BaseModel):
    name_i18n: dict[str, str] | None = None
    icon: str | None = None
    sort: int | None = Field(None, ge=-10000, le=10000)
    enabled: bool | None = None

    @field_validator("name_i18n")
    @classmethod
    def _n(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _names(v)

    @field_validator("icon")
    @classmethod
    def _i(cls, v: str | None) -> str | None:
        return _icon(v)


def _view(c: Category, products: int) -> dict:
    return {
        "id": str(c.id),
        "slug": c.slug,
        "name_i18n": {k: (c.name_i18n or {}).get(k, "") for k in LOCALES},
        "icon": c.icon,
        "sort": c.sort,
        "enabled": c.enabled,
        "pinned": c.pinned_at is not None,
        "products": products,
    }


def _snap(c: Category) -> dict:
    return {
        "slug": c.slug,
        "name_i18n": c.name_i18n,
        "icon": c.icon,
        "sort": c.sort,
        "enabled": c.enabled,
    }


async def _counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    rows = await db.execute(
        select(Product.category, func.count(Product.id))
        .where(Product.tenant_id == tenant_id)
        .group_by(Product.category)
    )
    return {k: int(n) for k, n in rows.all()}


async def _get(db: AsyncSession, category_id: uuid.UUID, tenant_id: uuid.UUID) -> Category:
    c = await db.scalar(
        select(Category).where(Category.id == category_id, Category.tenant_id == tenant_id)
    )
    if c is None:
        raise NotFound("Category not found")
    return c


@router.get("", dependencies=[Depends(require_scope("products:read"))])
async def list_categories(
    admin: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    counts = await _counts(db, admin.tenant_id)
    rows = await db.scalars(
        select(Category)
        .where(Category.tenant_id == admin.tenant_id)
        .order_by(Category.pinned_at.desc().nullslast(), Category.sort, Category.slug)
    )
    return [_view(c, counts.get(c.slug, 0)) for c in rows]


@router.post("")
async def create_category(
    body: CategoryIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    c = Category(
        tenant_id=admin.tenant_id,
        slug=body.slug,
        name_i18n=body.name_i18n,
        icon=body.icon,
        sort=body.sort,
        enabled=body.enabled,
    )
    db.add(c)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("A category with this slug already exists") from None
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="category.created",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"category:{c.slug}",
        after=_snap(c),
        ip=client_ip(request),
    )
    await db.commit()
    cache.invalidate(admin.tenant_id)
    return _view(c, 0)


@router.patch("/{category_id}")
async def patch_category(
    category_id: uuid.UUID,
    body: CategoryPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    c = await _get(db, category_id, admin.tenant_id)
    before = _snap(c)
    data = body.model_dump(exclude_unset=True)
    if data.get("name_i18n") is not None:
        c.name_i18n = data["name_i18n"]
    if "icon" in data:
        c.icon = data["icon"]
    if data.get("sort") is not None:
        c.sort = data["sort"]
    if data.get("enabled") is not None:
        c.enabled = data["enabled"]
    c.updated_at = func.now()
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="category.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"category:{c.slug}",
        before=before,
        after=_snap(c),
        ip=client_ip(request),
        detail={"fields": sorted(data)},
    )
    await db.commit()
    await db.refresh(c)
    cache.invalidate(admin.tenant_id)
    return _view(c, (await _counts(db, admin.tenant_id)).get(c.slug, 0))


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    c = await _get(db, category_id, admin.tenant_id)
    used = await db.scalar(
        select(func.count(Product.id)).where(
            Product.tenant_id == admin.tenant_id, Product.category == c.slug
        )
    )
    if used:
        raise Conflict("Category is in use; disable it instead", code="category_in_use")
    before = _snap(c)
    await db.delete(c)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="category.deleted",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"category:{before['slug']}",
        before=before,
        ip=client_ip(request),
    )
    await db.commit()
    cache.invalidate(admin.tenant_id)
    return Response(status_code=204)
