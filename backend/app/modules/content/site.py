"""Site settings stored in ``tenants.settings`` (announcement, after-sales defaults)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tenant


async def tenant_settings(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    raw = await db.scalar(select(Tenant.settings).where(Tenant.id == tenant_id))
    return dict(raw or {})
