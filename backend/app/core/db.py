"""Async SQLAlchemy engine + tenant-pinned sessions.

Every transaction opened by a :class:`TenantSession` first runs
``SELECT set_config('app.tenant_id', <tenant>, true)`` (transaction-local), so
PostgreSQL RLS policies scope every statement to that tenant — including the
transactions SQLAlchemy auto-begins after a ``commit()``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tid, true)")


class TenantSession(Session):
    """Sync session class behind :class:`AsyncSession` that pins the RLS tenant."""


@event.listens_for(TenantSession, "after_begin")
def _pin_tenant(session: Session, _tx: SessionTransaction, connection: Connection) -> None:
    tid = session.info.get("tenant_id")
    if tid is not None:
        connection.execute(_SET_TENANT, {"tid": str(tid)})


def make_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5, **kw: Any) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        hide_parameters=True,  # never echo bound values (keys, blobs) into errors/logs
        connect_args={"server_settings": {"application_name": "goods24"}},
        **kw,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine, expire_on_commit=False, autoflush=True, sync_session_class=TenantSession
    )


@asynccontextmanager
async def tenant_session(
    maker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID | None
) -> AsyncIterator[AsyncSession]:
    """Session whose every transaction is pinned to ``tenant_id`` (None = unpinned)."""
    session = maker()
    session.sync_session.info["tenant_id"] = tenant_id
    try:
        yield session
    finally:
        await session.close()


async def set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Re-pin the tenant for the rest of the current transaction (worker jobs)."""
    session.sync_session.info["tenant_id"] = tenant_id
    await session.execute(_SET_TENANT, {"tid": str(tenant_id)})
