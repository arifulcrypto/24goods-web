"""review-v5.2 §5: staff API tokens (Bearer kst_...) on the admin API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from tests.helpers import open_conversation
from tests.test_chat import png_bytes

API = "/api/v1"
ADMIN = f"{API}/admin"
PRODUCT = {
    "title_i18n": {"en": "Agent item"},
    "price": "3.00",
    "delivery_mode": "manual",
    "category": "ai_tools",
}


def bearer(env, token: str, host: str = "testserver", **headers) -> httpx.AsyncClient:
    """Cookie-less client with NO CSRF header, like curl."""
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app),  # type: ignore[arg-type]
        base_url=f"http://{host}",
        headers={"Authorization": f"Bearer {token}", **headers},
    )
    env.clients.append(c)
    return c


async def mint(admin, scopes=("products:read", "products:write"), name="bot", days=30) -> dict:
    r = await admin.post(
        f"{ADMIN}/api-tokens", json={"name": name, "scopes": list(scopes), "expires_in_days": days}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _sql(env, sql: str, **params) -> None:
    async with env.owner_session() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _audit(env, action: str) -> list:
    async with env.owner_session() as db:
        rows = await db.execute(
            text("SELECT actor_id, before, after, ip FROM audit_log WHERE action = :a"),
            {"a": action},
        )
        return list(rows.all())


async def test_create_shows_token_once_and_stores_only_hash(env):
    admin, u = await env.login("admin")
    t = await mint(admin, ("products:read", "products:write", "stats"))
    token = t["token"]
    assert token.startswith("kst_") and t["prefix"] == token[:8] and len(token) >= 40
    assert t["scopes"] == ["products:read", "products:write", "stats"] and t["status"] == "active"
    listing = (await admin.get(f"{ADMIN}/api-tokens")).json()
    assert token not in str(listing) and "token_hash" not in str(listing)
    assert listing["items"][0]["prefix"] == token[:8]
    h = await env.scalar("SELECT token_hash FROM api_tokens")
    assert h != token and len(h) == 64 and token not in h
    assert await env.scalar("SELECT count(*) FROM api_tokens WHERE token_hash = :t", t=token) == 0
    rows = await _audit(env, "api_token.create")
    assert len(rows) == 1 and rows[0].actor_id == u.id and token not in str(rows[0].after)
    # validation: unknown scope, bad expiry, empty name
    for bad in (
        {"name": "x", "scopes": ["root"]},
        {"name": "x", "scopes": []},
        {"name": "x", "scopes": ["stats"], "expires_in_days": 366},
        {"name": " ", "scopes": ["stats"]},
    ):
        assert (await admin.post(f"{ADMIN}/api-tokens", json=bad)).status_code == 422
    default = (
        await admin.post(f"{ADMIN}/api-tokens", json={"name": "d", "scopes": ["stats"]})
    ).json()
    exp = datetime.fromisoformat(default["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=89) < exp - datetime.now(timezone.utc) <= timedelta(days=90)


async def test_bearer_scope_enforced_and_csrf_free(env, caplog):
    admin, _ = await env.login("admin")
    t = await mint(admin, ("products:read", "products:write"))
    caplog.set_level(logging.DEBUG)
    c = bearer(env, t["token"])
    r = await c.post(f"{ADMIN}/products", json=PRODUCT)  # no CSRF header, no cookie
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert (await c.get(f"{ADMIN}/products")).status_code == 200
    r = await c.patch(f"{ADMIN}/products/{pid}", json={"price": "4.00"})
    assert r.status_code == 200
    # scopes not granted
    for method, path in (
        ("GET", f"{ADMIN}/stats"),
        ("GET", f"{ADMIN}/orders"),
        ("GET", f"{ADMIN}/conversations"),
        ("POST", f"{API}/uploads"),
    ):
        r = await c.request(method, path)
        assert r.status_code == 403 and r.json()["error"]["code"] == "token_scope", path
    assert t["token"] not in caplog.text and "Bearer" not in caplog.text
    # last used recorded
    assert await env.scalar("SELECT last_used_at FROM api_tokens") is not None
    # audit rows for product writes carry the owner
    assert await _audit(env, "product.created")


async def test_refused_on_settings_tokens_payouts_and_others(env):
    admin, _ = await env.login("admin")
    t = await mint(
        admin,
        (
            "products:read",
            "products:write",
            "cards:import",
            "orders:read",
            "chat",
            "uploads",
            "stats",
        ),
    )
    c = bearer(env, t["token"])
    for method, path in (
        ("GET", f"{ADMIN}/api-tokens"),
        ("POST", f"{ADMIN}/api-tokens"),
        ("POST", f"{ADMIN}/api-tokens/{t['id']}/revoke"),
        ("GET", f"{ADMIN}/settings"),
        ("GET", f"{ADMIN}/site"),
        ("GET", f"{ADMIN}/users"),
        ("GET", f"{ADMIN}/blocked-ips"),
        ("GET", f"{ADMIN}/ledger"),
        ("GET", f"{ADMIN}/audit"),
        ("GET", f"{ADMIN}/resellers"),
        ("GET", f"{ADMIN}/payouts"),
        ("POST", f"{ADMIN}/cards/find"),
    ):
        r = await c.request(method, path)
        assert r.status_code in (403, 404, 405), (method, path, r.status_code)
        if r.status_code == 403:
            assert r.json()["error"]["code"] == "token_scope"
    assert (await c.get(f"{ADMIN}/api-tokens")).status_code == 403
    # not usable outside admin/uploads
    assert (await c.get(f"{API}/auth/me")).status_code == 401
    assert (await c.get(f"{API}/conversations")).status_code == 401
    assert await _list_count(admin) == 1


async def _list_count(admin) -> int:
    return len((await admin.get(f"{ADMIN}/api-tokens")).json()["items"])


async def test_invalid_expired_revoked_suspended_agent_owner(env):
    admin, u = await env.login("admin")
    t = await mint(admin)
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 200
    # garbage and wrong-prefix tokens
    assert (await bearer(env, "kst_" + "A" * 43).get(f"{ADMIN}/products")).status_code == 401
    assert (await bearer(env, "nope").get(f"{ADMIN}/products")).status_code == 401
    # expired
    await _sql(env, "UPDATE api_tokens SET expires_at = now() - interval '1 minute'")
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 401
    await _sql(env, "UPDATE api_tokens SET expires_at = now() + interval '1 day'")
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 200
    # owner suspended
    await _sql(env, "UPDATE users SET suspended_at = now() WHERE id = :i", i=u.id)
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 401
    await _sql(env, "UPDATE users SET suspended_at = NULL WHERE id = :i", i=u.id)
    # owner demoted to agent: token acts as agent, admin-only routes refuse
    await _sql(env, "UPDATE users SET role = 'agent' WHERE id = :i", i=u.id)
    c = bearer(env, t["token"])
    assert (await c.get(f"{ADMIN}/products")).status_code == 200  # staff route
    assert (await c.post(f"{ADMIN}/products", json=PRODUCT)).status_code == 403  # admin route
    # owner demoted to buyer: refused outright
    await _sql(env, "UPDATE users SET role = 'buyer' WHERE id = :i", i=u.id)
    assert (await c.get(f"{ADMIN}/products")).status_code == 401
    await _sql(env, "UPDATE users SET role = 'admin' WHERE id = :i", i=u.id)
    # revoke (audited), then refused
    r = await admin.post(f"{ADMIN}/api-tokens/{t['id']}/revoke")
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 401
    assert len(await _audit(env, "api_token.revoke")) == 1


async def test_agents_cannot_manage_tokens(env):
    agent, _ = await env.login("agent")
    assert (await agent.get(f"{ADMIN}/api-tokens")).status_code == 403
    r = await agent.post(f"{ADMIN}/api-tokens", json={"name": "x", "scopes": ["stats"]})
    assert r.status_code == 403


async def test_cross_tenant_token_refused_and_hidden(env):
    admin, _ = await env.login("admin")
    t = await mint(admin)
    # the token presented on the other tenant's host is refused (tenant-filtered lookup + RLS)
    from datetime import datetime, timezone

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
    r = await bearer(env, t["token"], host="shop.reseller.test").get(f"{ADMIN}/products")
    assert r.status_code == 401
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/products")).status_code == 200
    async with env.owner_session(env.other_tenant_id) as db:  # RLS: invisible to that tenant
        assert await db.scalar(text("SELECT count(*) FROM api_tokens")) == 0


async def test_csrf_required_when_cookie_present(env):
    admin, _ = await env.login("admin")
    t = await mint(admin)
    sid = admin.cookies.get("sid")
    assert sid
    # bearer + session cookie, no CSRF header: refused by the CSRF middleware
    c = bearer(env, t["token"])
    c.cookies.set("sid", sid)
    r = await c.post(f"{ADMIN}/products", json=PRODUCT)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"
    # with the header it is the cookie session that authenticates
    r = await c.post(f"{ADMIN}/products", json=PRODUCT, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    # cookie-less bearer to a non-token path still needs CSRF
    r = await bearer(env, t["token"]).post(f"{API}/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"


async def test_card_import_ok_and_reveal_unreachable(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="auto_card")
    t = await mint(admin, ("cards:import", "products:read"))
    c = bearer(env, t["token"])
    r = await c.post(f"{ADMIN}/products/{p.id}/cards/import", json={"lines": "AAA-111\nBBB-222"})
    assert r.status_code == 200 and r.json()["imported"] == 2
    assert "AAA-111" not in r.text
    # cannot list cards, find/void/replace, or reach the delivery reveal
    for method, path in (
        ("GET", f"{ADMIN}/products/{p.id}/cards"),
        ("GET", f"{ADMIN}/products/{p.id}/batches"),
        ("POST", f"{ADMIN}/cards/find"),
        ("POST", f"{ADMIN}/cards/void"),
        ("GET", f"{API}/orders"),
    ):
        r = await c.request(method, path)
        assert r.status_code in (401, 403), (path, r.status_code)
    # products-only token cannot import cards
    t2 = await mint(admin, ("products:read",), name="p")
    r = await bearer(env, t2["token"]).post(
        f"{ADMIN}/products/{p.id}/cards/import", json={"lines": "CCC-333"}
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "token_scope"
    assert len(await _audit(env, "cards.import")) >= 1


async def test_chat_orders_stats_uploads_scopes(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    conv = await open_conversation(buyer, (await env.product()).id)
    t = await mint(admin, ("chat", "uploads", "stats", "orders:read"))
    c = bearer(env, t["token"])
    assert (await c.get(f"{ADMIN}/stats")).status_code == 200
    assert (await c.get(f"{ADMIN}/orders")).status_code == 200
    assert (await c.get(f"{ADMIN}/conversations")).status_code == 200
    url = f"{ADMIN}/conversations/{conv['id']}/messages"
    r = await c.post(url, json={"client_msg_id": "t-1", "type": "text", "text": "hello"})
    assert r.status_code == 200
    up = await c.post(
        f"{API}/uploads",
        data={"kind": "image"},
        files={"file": ("a.png", png_bytes(), "image/png")},
    )
    assert up.status_code == 200, up.text
    r = await c.post(
        url, json={"client_msg_id": "t-2", "type": "image", "attachment_id": up.json()["id"]}
    )
    assert r.status_code == 200 and r.json()["type"] == "image"
    # chat scope does not include assigning / quoting / closing
    assert (await c.post(f"{ADMIN}/conversations/{conv['id']}/close")).status_code == 403
    assert (await c.get(f"{ADMIN}/products")).status_code == 403


async def test_token_rate_limited(env, monkeypatch):
    from app.core import apitoken

    monkeypatch.setattr(apitoken, "RATE_PER_MIN", 3)
    admin, _ = await env.login("admin")
    t = await mint(admin, ("stats",))
    c = bearer(env, t["token"])
    codes = [(await c.get(f"{ADMIN}/stats")).status_code for _ in range(5)]
    assert codes[:3] == [200] * 3 and codes[3] == 429


async def test_read_scope_cannot_write_and_write_is_audited_with_token(env):
    admin, _ = await env.login("admin")
    rw = await mint(admin, ("products:read", "products:write"), name="rw")
    ro = await mint(admin, ("products:read",), name="ro")
    r = await bearer(env, rw["token"]).post(f"{ADMIN}/products", json=PRODUCT)
    pid = r.json()["id"]
    c_ro = bearer(env, ro["token"])
    assert (await c_ro.get(f"{ADMIN}/products/{pid}")).status_code == 200
    for body in ({"price": "1.00"}, {"redeem_url": "https://evil.example/x"}, {"status": "draft"}):
        r = await c_ro.patch(f"{ADMIN}/products/{pid}", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "token_scope"
    r = await bearer(env, rw["token"]).patch(
        f"{ADMIN}/products/{pid}", json={"price": "4.00", "redeem_url": "https://ok.example/r"}
    )
    assert r.status_code == 200, r.text
    async with env.owner_session() as db:
        row = (
            await db.execute(
                text(
                    "SELECT before, after, detail FROM audit_log "
                    "WHERE action = 'product.updated' ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).one()
    assert row.before["price_minor"] != row.after["price_minor"] == 400
    assert row.after["redeem_url"] == "https://ok.example/r" and row.before["redeem_url"] is None
    assert row.detail["api_token"] == {"id": rw["id"], "prefix": rw["prefix"]}
    # a cookie session write carries no api_token marker
    await admin.patch(f"{ADMIN}/products/{pid}", json={"price": "5.00"})
    async with env.owner_session() as db:
        d = await db.scalar(
            text(
                "SELECT detail FROM audit_log WHERE action = 'product.updated' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
    assert "api_token" not in d


async def test_failed_bearer_attempts_limited_per_ip(env, monkeypatch):
    from app.core import apitoken

    monkeypatch.setattr(apitoken, "FAIL_PER_MIN_IP", 3)
    c = bearer(env, "kst_" + "B" * 43)
    codes = [(await c.get(f"{ADMIN}/products")).status_code for _ in range(5)]
    assert codes[:3] == [401] * 3 and codes[3] == 429


async def test_suspending_owner_revokes_tokens(env):
    admin, _ = await env.login("admin")
    agent, a = await env.login("agent")
    t = await mint(admin, ("stats",))
    # token owned by the agent: create directly (agents cannot mint)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE api_tokens SET user_id = :u WHERE id = :i"), {"u": a.id, "i": t["id"]}
        )
        await db.commit()
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/stats")).status_code == 200
    r = await admin.post(f"{ADMIN}/users/{a.id}/suspend", json={"reason": "x"})
    assert r.status_code == 200, r.text
    assert await env.scalar("SELECT revoked_at FROM api_tokens") is not None
    await admin.post(f"{ADMIN}/users/{a.id}/unsuspend")
    assert (await bearer(env, t["token"]).get(f"{ADMIN}/stats")).status_code == 401  # stays revoked
