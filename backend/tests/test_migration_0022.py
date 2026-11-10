"""Regression: migration 0022 must backfill rows that exist BEFORE it runs.

Production 2026-11-10: ``service_entitlements`` has FORCE ROW LEVEL SECURITY, which applies to the
migrate (owner) role too, so the ``ref`` backfill saw no rows and ``SET NOT NULL`` failed on the one
existing record. The suite's database is migrated empty, so nothing caught it. This test migrates a
scratch database to 0021 as the owner role, adds a record (and later a standalone record), then runs
0022 up and down as the owner role.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import ALEMBIC_INI, OWNER_PW, _with_db

SCRATCH_DB = "g24_mig0022"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _alembic(owner_url: str, target: str, *, down: bool = False) -> None:
    prev = os.environ.get("APP_MIGRATE_DATABASE_URL")
    os.environ["APP_MIGRATE_DATABASE_URL"] = owner_url
    try:
        cfg = Config(str(ALEMBIC_INI))
        fn = command.downgrade if down else command.upgrade
        await asyncio.to_thread(fn, cfg, target)
    finally:
        if prev is None:
            os.environ.pop("APP_MIGRATE_DATABASE_URL", None)
        else:
            os.environ["APP_MIGRATE_DATABASE_URL"] = prev


_INSERT = """INSERT INTO service_entitlements
    (id, tenant_id, buyer_id, order_id, owner_id, source, label, status, timezone, period_policy,
     period_days, grace_days, version, email_reminders, telegram_reminders)
    VALUES ($1, $2, $3, $4, $5, 'manual', 'Legacy service', 'active', 'UTC', 'calendar_month',
     30, 0, 1, false, false)"""
_INSERT_STANDALONE = """INSERT INTO service_entitlements
    (id, tenant_id, buyer_id, order_id, owner_id, source, label, status, timezone, period_policy,
     period_days, grace_days, version, email_reminders, telegram_reminders, ref)
    VALUES ($1, $2, NULL, NULL, $3, 'manual', 'Off-site service', 'active', 'UTC',
     'calendar_month', 30, 0, 1, false, false, 'SV-TEST01')"""
TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


async def _insert_entitlement(su: asyncpg.Connection, *, standalone: bool) -> uuid.UUID:
    """Superuser insert with FK triggers off (replica role): only the row shape matters here."""
    ident = uuid.uuid4()
    await su.execute("SET session_replication_role = replica")
    if standalone:
        await su.execute(_INSERT_STANDALONE, ident, TENANT, uuid.uuid4())
    else:
        await su.execute(_INSERT, ident, TENANT, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    await su.execute("SET session_replication_role = origin")
    return ident


async def test_0022_backfills_existing_rows_and_downgrades_under_force_rls(services):
    dsn = os.environ["TEST_DATABASE_URL"]
    admin = await asyncpg.connect(_plain(dsn), timeout=5)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
        await admin.execute(f"CREATE DATABASE {SCRATCH_DB} OWNER app_owner")
    finally:
        await admin.close()
    owner_url = _with_db(dsn, "app_owner", OWNER_PW, SCRATCH_DB)
    su_url = _plain(_with_db(dsn, *_superuser(dsn), SCRATCH_DB))
    try:
        await _alembic(owner_url, "0021_chat_v11")
        su = await asyncpg.connect(su_url, timeout=5)
        try:
            legacy = await _insert_entitlement(su, standalone=False)
        finally:
            await su.close()

        await _alembic(owner_url, "0022_v11_content_ops")  # failed in production before the fix

        su = await asyncpg.connect(su_url, timeout=5)
        try:
            ref = await su.fetchval("SELECT ref FROM service_entitlements WHERE id=$1", legacy)
            assert ref is not None and ref.startswith("SV-") and len(ref) == 9
            forced = await su.fetch(
                "SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY($1)",
                ["service_entitlements", "service_renewals", "service_reminders", "articles"],
            )
            assert {r["relname"]: r["relforcerowsecurity"] for r in forced} == {
                "service_entitlements": True,
                "service_renewals": True,
                "service_reminders": True,
                "articles": True,
            }  # FORCE is restored after the data step
            standalone = await _insert_entitlement(su, standalone=True)
        finally:
            await su.close()

        # downgrade must remove standalone rows it cannot keep (also under FORCE RLS)
        await _alembic(owner_url, "0021_chat_v11", down=True)
        su = await asyncpg.connect(su_url, timeout=5)
        try:
            left = {r["id"] for r in await su.fetch("SELECT id FROM service_entitlements")}
            assert left == {legacy}
            assert standalone not in left
        finally:
            await su.close()
        await _alembic(owner_url, "head")
    finally:
        admin = await asyncpg.connect(_plain(dsn), timeout=5)
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
        finally:
            await admin.close()


def _superuser(dsn: str) -> tuple[str, str]:
    from urllib.parse import unquote, urlsplit

    u = urlsplit(dsn)
    if not u.username:
        pytest.skip("TEST_DATABASE_URL has no superuser credentials")
    return unquote(u.username), unquote(u.password or "")
