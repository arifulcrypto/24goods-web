"""Row-level security: the API role only ever sees the pinned tenant's rows."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import tenant_session


async def test_second_tenant_rows_are_invisible(env):
    await env.product(title="Mine")
    await env.product(title="Theirs", tenant_id=env.other_tenant_id)
    theirs_user = await env.user("buyer", tenant_id=env.other_tenant_id, email="t@other.io")
    async with env.api_session(env.tenant_id) as db:
        titles = (await db.execute(text("SELECT title_i18n->>'en' FROM products"))).scalars().all()
        assert titles == ["Mine"]
        assert (
            await db.scalar(text("SELECT count(*) FROM users WHERE id = :i"), {"i": theirs_user.id})
            == 0
        )
    async with env.api_session(env.other_tenant_id) as db:
        titles = (await db.execute(text("SELECT title_i18n->>'en' FROM products"))).scalars().all()
        assert titles == ["Theirs"]
    async with tenant_session(env.res.maker, None) as db:  # no tenant pinned -> nothing
        assert await db.scalar(text("SELECT count(*) FROM products")) == 0


async def test_cannot_write_into_another_tenant(env):
    async with env.api_session(env.tenant_id) as db:
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(
                text(
                    "INSERT INTO products (tenant_id, title_i18n, price_minor, delivery_mode, "
                    "category) VALUES (:t, '{\"en\": \"x\"}', 100, 'manual', 'ai_tools')"
                ),
                {"t": env.other_tenant_id},
            )


async def test_session_and_host_are_tenant_bound(env):
    from app.models import CustomDomain

    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            CustomDomain(
                tenant_id=env.other_tenant_id,
                hostname="shop.reseller.test",
                verify_token="t",
                status="active",
                verified_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await env.product(title="Platform item")
    await env.product(title="Reseller item", tenant_id=env.other_tenant_id)
    platform = env.client()
    reseller = env.client(host="shop.reseller.test")
    assert [p["title"] for p in (await platform.get("/api/v1/products")).json()["items"]] == [
        "Platform item"
    ]
    assert [p["title"] for p in (await reseller.get("/api/v1/products")).json()["items"]] == [
        "Reseller item"
    ]
    # a platform session cookie is not valid on the reseller host
    buyer, _ = await env.login("buyer")
    reseller.cookies.set("sid", buyer.cookies["sid"])
    assert (await reseller.get("/api/v1/auth/me")).status_code == 401
    assert (await buyer.get("/api/v1/auth/me")).status_code == 200


async def test_orm_schema_matches_database(env):
    from app.models import Base

    async with env.owner_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
            )
        ).all()
    cols: dict[str, set[str]] = {}
    for t, c in rows:
        cols.setdefault(t, set()).add(c)
    for table in Base.metadata.sorted_tables:
        assert table.name in cols, table.name
        missing = {c.name for c in table.columns} - cols[table.name]
        assert not missing, (table.name, missing)


async def test_every_tenant_table_has_forced_rls(env):
    async with env.owner_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN information_schema.columns i ON i.table_name = c.relname "
                    "AND i.column_name = 'tenant_id' AND i.table_schema = 'public' "
                    "WHERE c.relkind = 'r'"
                )
            )
        ).all()
    unprotected = {r[0] for r in rows if not (r[1] and r[2])}
    assert unprotected == {"custom_domains"}  # global routing table (Host -> tenant)
