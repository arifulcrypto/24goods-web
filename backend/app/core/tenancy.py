"""Host -> tenant resolution (custom domains, else the default platform tenant)."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import tenant_session
from app.models import CustomDomain, Tenant

_TTL_S = 60.0


class TenantResolver:
    """Small in-process cache; ``tenants``/``custom_domains`` are not RLS-scoped."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], default_slug: str):
        self._maker = maker
        self._slug = default_slug
        self._cache: dict[str, tuple[float, uuid.UUID]] = {}

    def clear(self) -> None:
        self._cache.clear()

    async def default_tenant(self) -> uuid.UUID:
        return await self._lookup("", self._load_default)

    async def resolve(self, host: str | None) -> uuid.UUID:
        hostname = (host or "").split(":", 1)[0].strip().lower().rstrip(".")
        if not hostname:
            return await self.default_tenant()
        return await self._lookup(hostname, self._load_host)

    async def _lookup(self, key: str, loader) -> uuid.UUID:  # type: ignore[no-untyped-def]
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and hit[0] > now:
            return hit[1]
        tid = await loader(key)
        self._cache[key] = (now + _TTL_S, tid)
        if len(self._cache) > 1000:
            self._cache.clear()
        return tid

    async def _load_default(self, _: str) -> uuid.UUID:
        async with tenant_session(self._maker, None) as s:
            tid = await s.scalar(select(Tenant.id).where(Tenant.slug == self._slug))
        if tid is None:
            raise RuntimeError("default tenant missing; run `python -m app.devtools.seed`")
        return tid

    async def _load_host(self, hostname: str) -> uuid.UUID:
        async with tenant_session(self._maker, None) as s:
            tid = await s.scalar(
                select(CustomDomain.tenant_id).where(
                    CustomDomain.hostname == hostname,
                    CustomDomain.status == "active",
                    CustomDomain.verified_at.is_not(None),
                )
            )
        return tid if tid is not None else await self.default_tenant()
