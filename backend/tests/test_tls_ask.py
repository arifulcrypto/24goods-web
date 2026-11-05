from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.internal import create_internal_app, normalize_hostname
from app.models import CustomDomain


@pytest.fixture
async def ask(env):
    app = create_internal_app(env.res)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")
    yield c
    await c.aclose()


async def _domain(env, hostname: str, status: str = "active", verified: bool = True) -> None:
    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            CustomDomain(
                tenant_id=env.other_tenant_id,
                hostname=hostname,
                verify_token="tok",
                status=status,
                verified_at=datetime.now(timezone.utc) if verified else None,
            )
        )
        await db.commit()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "127.0.0.1",
        "::1",
        "[::1]",
        "10.0.0.1:443",
        "a b.com",
        "a/b.com",
        "localhost",
        "-bad.com",
        "bad-.com",
        "x" * 64 + ".com",
        ("a." * 127) + "com",
        "user@host.com",
        "host.123",
        "evil.com%00",
        "*.example.com",
    ],
)
def test_hostname_rejected(raw):
    assert normalize_hostname(raw) is None


def test_hostname_normalized():
    assert normalize_hostname("Shop.Example.COM.") == "shop.example.com"
    assert normalize_hostname("bücher.example") == "xn--bcher-kva.example"


async def test_ask_flow(env, ask):
    await _domain(env, "shop.reseller.test")
    await _domain(env, "pending.reseller.test", status="pending")
    await _domain(env, "unverified.reseller.test", verified=False)
    assert (
        await ask.get("/internal/tls/ask", params={"domain": "SHOP.reseller.test"})
    ).status_code == 200
    for host in ("pending.reseller.test", "unverified.reseller.test", "unknown.example.com"):
        assert (await ask.get("/internal/tls/ask", params={"domain": host})).status_code == 404
    assert (await ask.get("/internal/tls/ask", params={"domain": "127.0.0.1"})).status_code == 400
    assert (await ask.get("/internal/tls/ask")).status_code == 400
    assert await env.s.redis.exists("tls:neg:unknown.example.com")


async def test_ask_negative_cache(env, ask):
    assert (
        await ask.get("/internal/tls/ask", params={"domain": "late.example.com"})
    ).status_code == 404
    await _domain(env, "late.example.com")
    # cached negative for 10 minutes: no DB hit, still 404
    assert (
        await ask.get("/internal/tls/ask", params={"domain": "late.example.com"})
    ).status_code == 404
    await env.s.redis.delete("tls:neg:late.example.com")
    assert (
        await ask.get("/internal/tls/ask", params={"domain": "late.example.com"})
    ).status_code == 200


async def test_ask_rate_limited(env, ask):
    await _domain(env, "shop.reseller.test")
    codes = [
        (await ask.get("/internal/tls/ask", params={"domain": "shop.reseller.test"})).status_code
        for _ in range(31)
    ]
    assert codes[:30] == [200] * 30 and codes[30] == 429


async def test_ask_not_exposed_on_public_app(env):
    await _domain(env, "shop.reseller.test")
    r = await env.client().get("/internal/tls/ask", params={"domain": "shop.reseller.test"})
    assert r.status_code == 404
    r = await env.client().get("/api/v1/internal/tls/ask", params={"domain": "shop.reseller.test"})
    assert r.status_code == 404
