"""Reseller storefront resolution (review-v5 s7).

Host ``{slug}.{resellers.base_domain}`` -> that active reseller; else the ``X-Storefront``
header (the SPA sets it from ``?store=`` on hosts without wildcard DNS); else none. Only when
``features.resellers`` is on. Unknown or non-active slugs resolve to the main store.

Security: a storefront can only ADD markup and change branding. A buyer can always buy at the
base price on the main store, so a forged header cannot lower a price.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_res, get_tenant_id
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.models import Reseller, Tenant

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,30}[a-z0-9])$")
_TTL_S = 15.0  # small cache; invalidate() on every reseller write; disabled when env=test


@dataclass(frozen=True)
class Storefront:
    id: uuid.UUID
    tenant_id: uuid.UUID
    slug: str
    store_name: str
    logo_url: str | None
    welcome_i18n: dict[str, Any] = field(default_factory=dict)
    markup_kind: str = "percent"
    markup_value: int = 0
    # program caps captured at resolution time (settings.resellers)
    max_permille: int = 2000
    max_fixed_minor: int = 1_000_000

    def public(self, lang: str) -> dict[str, Any]:
        w = self.welcome_i18n or {}
        return {
            "slug": self.slug,
            "store_name": self.store_name,
            "logo_url": self.logo_url,
            "welcome": w.get(lang) or w.get("en") or "",
            # the web app must fully white-label: no platform brand on a storefront
            "white_label": True,
        }


# (tenant_id) -> (expires, resellers-enabled, base_domain, max_permille, max_fixed)
_cfg_cache: dict[uuid.UUID, tuple[float, bool, str, int, int]] = {}
# (tenant_id, slug) -> (expires, Storefront | None)
_sf_cache: dict[tuple[uuid.UUID, str], tuple[float, Storefront | None]] = {}


def invalidate() -> None:
    _cfg_cache.clear()
    _sf_cache.clear()


def _ttl(res: Resources) -> float:
    return 0.0 if res.settings.env == "test" else _TTL_S


async def _cfg(db: AsyncSession, tenant_id: uuid.UUID, ttl: float) -> tuple[bool, str, int, int]:
    hit = _cfg_cache.get(tenant_id)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1:]
    ts = await db.scalar(select(Tenant.settings).where(Tenant.id == tenant_id))
    ts = dict(ts or {})
    rs = section(ts, "resellers")
    enabled = bool(section(ts, "features")["resellers"])
    out = (
        enabled,
        str(rs["base_domain"]).lower(),
        rs["max_markup_permille"],
        rs["max_markup_fixed_minor"],
    )
    if ttl > 0:
        _cfg_cache[tenant_id] = (now + ttl, *out)
    return out


def candidate_slug(host: str | None, header: str | None, base_domain: str) -> str | None:
    """THE single host -> reseller-slug seam. Today: ``{slug}.{base_domain}`` or the
    ``X-Storefront`` header. A later custom-domain lookup (hostname -> slug) slots in here;
    nothing else in the code base parses the Host header for storefronts."""
    hostname = (host or "").split(":", 1)[0].strip().lower().rstrip(".")
    base = base_domain.strip().lower()
    if base and hostname.endswith("." + base):
        prefix = hostname[: -(len(base) + 1)]
        if SLUG_RE.match(prefix):
            return prefix
    h = (header or "").strip().lower()
    return h if SLUG_RE.match(h) else None


async def resolve(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    host: str | None,
    header: str | None,
    *,
    ttl: float = 0.0,
) -> Storefront | None:
    enabled, base_domain, max_pm, max_fx = await _cfg(db, tenant_id, ttl)
    if not enabled:
        return None
    slug = candidate_slug(host, header, base_domain)
    if slug is None:
        return None
    key = (tenant_id, slug)
    hit = _sf_cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    r = await db.scalar(
        select(Reseller).where(
            Reseller.slug == slug, Reseller.tenant_id == tenant_id, Reseller.status == "active"
        )
    )
    sf = (
        Storefront(
            id=r.id,
            tenant_id=r.tenant_id,
            slug=r.slug,
            store_name=r.store_name,
            logo_url=r.logo_url,
            welcome_i18n=dict(r.welcome_i18n or {}),
            markup_kind=r.markup_kind,
            markup_value=int(r.markup_value),
            max_permille=max_pm,
            max_fixed_minor=max_fx,
        )
        if r is not None
        else None
    )
    if ttl > 0:
        _sf_cache[key] = (now + ttl, sf)
        if len(_sf_cache) > 2000:
            _sf_cache.clear()
    return sf


async def get_storefront(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Storefront | None:
    return await resolve(
        db,
        tenant_id,
        request.headers.get("host"),
        request.headers.get("x-storefront"),
        ttl=_ttl(res),
    )
