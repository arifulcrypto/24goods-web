"""Tenant-scoped primary-key lookup (AGENTS.md invariant 4, defence in depth beside RLS).

``db.get(Model, id)`` filters by id only and leans on PostgreSQL RLS for tenant isolation.
Use :func:`get_scoped` instead: the tenant predicate is in the statement itself. A Semgrep
rule (``.semgrep/tenant-scope.yml``) forbids bare ``db.get`` in ``app/``.
"""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


def session_tenant(db: AsyncSession) -> uuid.UUID:
    """The tenant this session is pinned to (see ``app.core.db``); refuses an unpinned one."""
    tid = db.info.get("tenant_id")
    if tid is None:
        raise RuntimeError("session is not pinned to a tenant")
    return tid  # type: ignore[no-any-return]


async def get_scoped(
    db: AsyncSession,
    model: type[T],
    pk: Any,
    tenant_id: uuid.UUID,
    *,
    populate_existing: bool = False,
) -> T | None:
    """Row of ``model`` with primary key ``pk`` AND ``tenant_id``; ``None`` when absent."""
    (pk_col,) = inspect(model).primary_key  # single-column primary keys only
    stmt = select(model).where(pk_col == pk, model.tenant_id == tenant_id)  # type: ignore[attr-defined]
    if populate_existing:
        stmt = stmt.execution_options(populate_existing=True)
    return await db.scalar(stmt)
