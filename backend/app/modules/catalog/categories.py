"""Tenant categories: DB reader with a 60 s in-process cache (review-v5 §2).

``CATEGORIES`` stays as the default set seeded for new tenants. Readers are tenant-scoped
(the caller's session is RLS-pinned; the cache key is the tenant id) and admin writes call
:func:`invalidate`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.i18n import pick
from app.models import Category

CATEGORIES: dict[str, dict[str, str]] = {
    "game_topup": {"en": "Game top-up", "zh-CN": "游戏充值"},
    "gift_cards": {"en": "Gift cards", "zh-CN": "礼品卡"},
    "software_keys": {"en": "Software keys", "zh-CN": "软件激活码"},
    "design_services": {"en": "Design & services", "zh-CN": "设计与服务"},
    "accounts": {"en": "Accounts", "zh-CN": "账号"},
    "ai_tools": {"en": "AI tools", "zh-CN": "AI 工具"},
}

CACHE_TTL_S = 60.0


@dataclass(frozen=True)
class CategoryRow:
    id: uuid.UUID
    slug: str
    name_i18n: dict[str, str]
    icon: str | None
    sort: int
    enabled: bool
    pinned: bool = False


_cache: dict[uuid.UUID, tuple[float, tuple[CategoryRow, ...]]] = {}


def invalidate(tenant_id: uuid.UUID | None = None) -> None:
    if tenant_id is None:
        _cache.clear()
    else:
        _cache.pop(tenant_id, None)


async def all_categories(db: AsyncSession, tenant_id: uuid.UUID) -> tuple[CategoryRow, ...]:
    """Every category of the tenant (enabled or not), pinned first, then (sort, slug)."""
    hit = _cache.get(tenant_id)
    now = time.monotonic()
    if hit is not None and hit[0] > now:
        return hit[1]
    rows = tuple(
        CategoryRow(
            c.id,
            c.slug,
            dict(c.name_i18n or {}),
            c.icon,
            c.sort,
            c.enabled,
            c.pinned_at is not None,
        )
        for c in await db.scalars(
            select(Category)
            .where(Category.tenant_id == tenant_id)
            .order_by(Category.pinned_at.desc().nullslast(), Category.sort, Category.slug)
        )
    )
    _cache[tenant_id] = (now + CACHE_TTL_S, rows)
    return rows


async def enabled_categories(db: AsyncSession, tenant_id: uuid.UUID) -> list[CategoryRow]:
    return [c for c in await all_categories(db, tenant_id) if c.enabled]


async def category_slugs(db: AsyncSession, tenant_id: uuid.UUID) -> set[str]:
    return {c.slug for c in await all_categories(db, tenant_id)}


async def disabled_slugs(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """Slugs whose products are hidden from listings (products with no row stay visible)."""
    return [c.slug for c in await all_categories(db, tenant_id) if not c.enabled]


def public_view(c: CategoryRow, lang: str) -> dict[str, str | None]:
    # ``key`` is the pre-v5 field name; kept so existing clients keep working.
    return {"slug": c.slug, "key": c.slug, "name": pick(c.name_i18n, lang), "icon": c.icon}


async def seed_default_categories(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Idempotent: creates the six default categories for a tenant (ON CONFLICT DO NOTHING)."""
    rows = [
        {"tenant_id": tenant_id, "slug": slug, "name_i18n": names, "sort": (i + 1) * 10}
        for i, (slug, names) in enumerate(CATEGORIES.items())
    ]
    await db.execute(
        pg_insert(Category)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["tenant_id", "slug"])
    )
    invalidate(tenant_id)
