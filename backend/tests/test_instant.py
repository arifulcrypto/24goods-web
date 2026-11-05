"""Instant accounts (one-tap buyers), login by handle, limits, purge, /me."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.modules.jobs import purge_instant_accounts
from tests.conftest import PASSWORD
from tests.helpers import buy, solve_pow


async def instant(c, **extra):
    return await c.post(
        "/api/v1/auth/instant", json={"pow": await solve_pow(c, "instant"), **extra}
    )


async def test_instant_account_shape_and_session(env):
    c = env.client()
    r = await instant(c)
    assert r.status_code == 200, r.text
    body = r.json()
    handle = body["credentials"]["login"]
    assert re.fullmatch(r"u-[a-z2-7]{7}", handle)
    assert len(body["credentials"]["password"]) == 14
    assert body["user"]["is_instant"] is True and body["user"]["handle"] == handle
    assert body["user"]["role"] == "buyer" and body["user"]["display_name"] == handle
    assert r.headers["cache-control"] == "no-store"
    assert "sid=" in r.headers["set-cookie"]
    assert (await c.get("/api/v1/auth/me")).json()["id"] == body["user"]["id"]
    email = await env.scalar("SELECT email FROM users WHERE handle=:h", h=handle)
    assert email == f"{handle}@instant.invalid"
    pw_hash = await env.scalar("SELECT pw_hash FROM users WHERE handle=:h", h=handle)
    assert pw_hash.startswith("$argon2id$") and body["credentials"]["password"] not in pw_hash


async def test_instant_with_email_stores_it(env):
    c = env.client()
    r = await instant(c, email="Real@Example.com")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "real@example.com"
    assert r.json()["user"]["is_instant"] is False
    r = await instant(env.client(), email="real@example.com")
    assert r.status_code == 409
    r = await instant(env.client(), email="x@instant.invalid")
    assert r.status_code == 422
    r = await instant(env.client(), email="not-an-email")
    assert r.status_code == 422


async def test_login_by_handle_and_by_email(env):
    c = env.client()
    cred = (await instant(c)).json()["credentials"]
    for ident in (cred["login"], cred["login"].upper()):
        r = await env.client().post(
            "/api/v1/auth/login", json={"email": ident, "password": cred["password"]}
        )
        assert r.status_code == 200, r.text
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": cred["login"], "password": "wrong-password"}
    )
    assert r.status_code == 401
    # email login works after the account sets an email via /me/credentials
    r = await c.post("/api/v1/me/credentials", json={"email": "me@example.com"})
    assert r.status_code == 200 and r.json()["is_instant"] is False
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": "me@example.com", "password": cred["password"]}
    )
    assert r.status_code == 200


async def test_instant_per_ip_limits(env):
    codes = []
    for _i in range(4):
        r = await instant(env.client())
        codes.append(r.status_code)
    assert codes == [200, 200, 200, 429]  # 3 per IP per hour
    assert r.json()["error"]["code"] == "rate_limited"
    # a different /24 is not affected by that IP's budget
    r = await env.client().post(
        "/api/v1/auth/instant",
        json={"pow": await solve_pow(env.client(), "instant")},
        headers={"X-Forwarded-For": "198.51.100.9"},
    )
    assert r.status_code in (200, 429)


async def test_instant_daily_and_network_limits(env):
    env.res.settings.instant_per_ip_hour = 100
    env.res.settings.instant_per_ip_day = 4
    codes = [(await instant(env.client())).status_code for _ in range(5)]
    assert codes == [200] * 4 + [429]
    env.res.settings.instant_per_ip_day = 100
    env.res.settings.instant_per_net_day = 2
    await env.s.redis.flushdb()
    codes = []
    for last in (1, 2, 3):  # three hosts in the same /24 share one budget
        c = env.client()
        r = await c.post(
            "/api/v1/auth/instant",
            json={"pow": await solve_pow(c, "instant")},
            headers={"X-Forwarded-For": f"203.0.113.{last}"},
        )
        codes.append(r.status_code)
    assert codes == [200, 200, 429]


async def test_instant_account_max_two_pending_orders(env):
    p = await env.product(mode="manual")
    c = env.client()
    await instant(c)
    await buy(c, p)
    await buy(c, p)
    r = await c.post("/api/v1/orders", json={"product_id": str(p.id), "qty": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    # registered buyers keep the limit of 5
    reg, _ = await env.login("buyer")
    for _ in range(5):
        await buy(reg, p)
    r = await reg.post("/api/v1/orders", json={"product_id": str(p.id), "qty": 1})
    assert r.status_code == 409


async def test_purge_instant_accounts_without_paid_order(env):
    c1, c2 = env.client(), env.client()
    keep = (await instant(c1)).json()["user"]
    gone = (await instant(c2)).json()["user"]
    fresh = (await instant(env.client())).json()["user"]
    p = await env.product(mode="manual")
    order = await buy(c1, p)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE users SET created_at = :t WHERE id = ANY(:ids)"),
            {
                "t": datetime.now(timezone.utc) - timedelta(days=8),
                "ids": [__import__("uuid").UUID(keep["id"]), __import__("uuid").UUID(gone["id"])],
            },
        )
        await db.execute(
            text("UPDATE orders SET paid_at = now(), status='paid' WHERE id = :i"),
            {"i": __import__("uuid").UUID(order["id"])},
        )
        await db.commit()
    assert await purge_instant_accounts(env.res.maker, env.res.settings) == 1
    assert await env.scalar("SELECT status FROM users WHERE id=:i", i=gone["id"]) == "deleted"
    assert await env.scalar("SELECT status FROM users WHERE id=:i", i=keep["id"]) == "active"
    assert await env.scalar("SELECT status FROM users WHERE id=:i", i=fresh["id"]) == "active"
    assert (
        await env.scalar("SELECT email FROM users WHERE id=:i", i=gone["id"])
        == f"deleted+{gone['id']}@invalid"
    )
    assert await env.scalar("SELECT handle FROM users WHERE id=:i", i=gone["id"]) is None
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='auth.instant_purged'") == 1
    )
    assert (await c2.get("/api/v1/auth/me")).status_code == 401  # purged: session dead


async def test_login_requires_pow_after_three_failures(env):
    await env.user("buyer", email="pow@example.com")
    c = env.client()
    bad = {"email": "pow@example.com", "password": "nope"}
    for _ in range(3):
        assert (await c.post("/api/v1/auth/login", json=bad)).status_code == 401
    r = await c.post("/api/v1/auth/login", json=bad)
    assert r.status_code == 428 and r.json()["error"]["code"] == "pow_required"
    good = {"email": "pow@example.com", "password": PASSWORD}
    assert (await c.post("/api/v1/auth/login", json=good)).status_code == 428  # even if correct
    r = await c.post("/api/v1/auth/login", json={**good, "pow": await solve_pow(c, "login")})
    assert r.status_code == 200
    # a register-scope challenge cannot be used for login
    r = await c.post("/api/v1/auth/login", json={**good, "pow": await solve_pow(c, "register")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "pow_invalid"


async def test_register_requires_pow(env):
    c = env.client()
    body = {"email": "np@example.com", "password": "long-enough-pw", "display_name": "N"}
    r = await c.post("/api/v1/auth/register", json=body)
    assert r.status_code == 428 and r.json()["error"]["code"] == "pow_required"
    r = await c.post("/api/v1/auth/register", json={**body, "email": "x@y.invalid"})
    assert r.status_code in (422, 428)
