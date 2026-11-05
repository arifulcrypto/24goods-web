"""Async Alembic environment. Runs as the schema owner (``app_owner``).

URL: ``APP_MIGRATE_DATABASE_URL`` (preferred) or ``APP_DATABASE_URL``.
Role passwords for ``app_api`` / ``app_delivery`` come from ``APP_DB_API_PASSWORD`` and
``APP_DB_DELIVERY_PASSWORD`` (read inside the migration, never logged).
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config


def _url() -> str:
    url = os.environ.get("APP_MIGRATE_DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if not url:
        raise RuntimeError("set APP_MIGRATE_DATABASE_URL (schema owner) to run migrations")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(_url(), pool_size=1, max_overflow=0)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_run)
            await conn.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline mode is not supported; run against a database")
asyncio.run(_main())
