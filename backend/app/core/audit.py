"""Append-only audit trail (who, what, before, after, when, ip)."""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

# Set by apitoken.authenticate for the request task: {"id", "prefix"} of the API token in use.
api_token_ctx: ContextVar[dict[str, str] | None] = ContextVar("api_token_ctx", default=None)


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
    tok = api_token_ctx.get()
    if tok is not None:
        detail = {**(detail or {}), "api_token": dict(tok)}
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
