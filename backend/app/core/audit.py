"""Append-only audit trail (who, what, before, after, when, ip)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def audit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_role: str = "system",
    target: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    await db.execute(
        insert(AuditLog).values(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target=target,
            before=before,
            after=after,
            ip=ip,
            detail=detail,
            severity=severity,
        )
    )
