"""review-v5.1 §5 (v5.2): user suspension + blocked IPs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core import ipblock
from tests.conftest import PASSWORD
from tests.test_staff_access_v52 import _from
from tests.test_v5_core import put_site

API = "/api/v1"
BAD = "198.51.100.77"


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setattr(ipblock, "CFG_TTL_S", 0.0)


async def _audit(env, action: str) -> list:
    async with env.owner_session() as db:
        rows = await db.execute(
            text("SELECT actor_id, before, after, ip, detail FROM audit_log WHERE action = :a"),
            {"a": action},
        )
        return list(rows.all())


async def test_suspend_revokes_sessions_and_refuses_login(env):
    admin, _ = await env.login("admin")
    buyer, b = await env.login("buyer")
    assert (await buyer.get(f"{API}/auth/me")).status_code == 200
    r = await admin.post(f"{API}/admin/users/{b.id}/suspend", json={"reason": "fraud"})
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    assert (await buyer.get(f"{API}/auth/me")).status_code == 401  # session revoked
    r = await env.client().post(f"{API}/auth/login", json={"email": b.email, "password": PASSWORD})
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["reason"] == "fraud"
    assert err["code"] == "account_suspended"
    # a surviving/forged session is still refused on every authenticated request
    token = await env.res.sessions.create(b.id, b.tenant_id, b.role)
    c = env.client()
    c.cookies.set("sid", token)
    r = await c.get(f"{API}/auth/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_suspended"
    # wrong password is still the generic 401 (no probing of suspended accounts)
    r = await env.client().post(f"{API}/auth/login", json={"email": b.email, "password": "nope"})
    assert r.status_code == 401
    rows = await _audit(env, "user.suspend")
    assert len(rows) == 1 and rows[0].after["suspended_at"] and rows[0].detail["reason"] == "fraud"
    assert rows[0].before["suspended_at"] is None
    # unsuspend
    r = await admin.post(f"{API}/admin/users/{b.id}/unsuspend")
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = await env.client().post(f"{API}/auth/login", json={"email": b.email, "password": PASSWORD})
    assert r.status_code == 200
    assert len(await _audit(env, "user.unsuspend")) == 1


async def test_suspension_expiry_auto_lifts(env):
    admin, _ = await env.login("admin")
    _, b = await env.login("buyer")
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await admin.post(
        f"{API}/admin/users/{b.id}/suspend", json={"reason": "cool off", "until": until}
    )
    assert r.status_code == 200
    r = await env.client().post(f"{API}/auth/login", json={"email": b.email, "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["until"].endswith("Z")
    past = await admin.post(
        f"{API}/admin/users/{b.id}/suspend",
        json={
            "reason": "x",
            "until": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        },
    )
    assert past.status_code == 422
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE users SET suspended_until = now() - interval '1 minute' WHERE id = :i"),
            {"i": b.id},
        )
        await db.commit()
    r = await env.client().post(f"{API}/auth/login", json={"email": b.email, "password": PASSWORD})
    assert r.status_code == 200
    assert await env.scalar("SELECT suspended_at FROM users WHERE id = :i", i=b.id) is None


async def test_who_may_suspend_whom(env):
    agent, a = await env.login("agent")
    _, other_agent = await env.login("agent")
    _, boss = await env.login("admin")
    _, buyer = await env.login("buyer")
    body = {"reason": "r"}
    for target in (other_agent, boss, a):
        r = await agent.post(f"{API}/admin/users/{target.id}/suspend", json=body)
        assert r.status_code == 403, target.role
    assert (await agent.post(f"{API}/admin/users/{buyer.id}/suspend", json=body)).status_code == 200
    admin, adm = await env.login("admin")
    for target in (boss, adm):  # never an admin, never self
        r = await admin.post(f"{API}/admin/users/{target.id}/suspend", json=body)
        assert r.status_code == 403
    assert (
        await admin.post(f"{API}/admin/users/{other_agent.id}/suspend", json=body)
    ).status_code == 200
    # buyers cannot use the endpoints; agents only list buyers
    bc, _ = await env.login("buyer")
    assert (await bc.get(f"{API}/admin/users")).status_code == 403
    items = (await agent.get(f"{API}/admin/users")).json()["items"]
    assert items and {i["role"] for i in items} == {"buyer"}


async def test_cross_tenant_suspend_is_404(env):
    admin, _ = await env.login("admin")
    victim = await env.user("buyer", tenant_id=env.other_tenant_id)
    r = await admin.post(f"{API}/admin/users/{victim.id}/suspend", json={"reason": "r"})
    assert r.status_code == 404
    ids = [u["id"] for u in (await admin.get(f"{API}/admin/users")).json()["items"]]
    assert str(victim.id) not in ids
    assert await env.scalar("SELECT suspended_at FROM users WHERE id = :i", i=victim.id) is None


async def test_user_list_filters(env):
    admin, _ = await env.login("admin")
    _, b = await env.login("buyer", email="findme@example.com")
    await admin.post(f"{API}/admin/users/{b.id}/suspend", json={"reason": "r"})
    r = await admin.get(f"{API}/admin/users", params={"q": "FINDME", "status": "suspended"})
    assert [u["email"] for u in r.json()["items"]] == ["findme@example.com"]
    r = await admin.get(f"{API}/admin/users", params={"q": "findme", "status": "active"})
    assert r.json()["items"] == []


async def test_cidr_validation_and_crud(env):
    admin, _ = await env.login("admin")
    for bad in ("0.0.0.0/0", "::/0", "1.0.0.0/7", "2001:db8::/31", "nope", "10.0.0.0/99", ""):
        r = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": bad})
        assert r.status_code in (400, 422), bad
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": BAD, "expires_at": past})
    assert r.status_code == 422
    ok = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "203.0.113.0/24", "note": "x"})
    assert ok.status_code == 201 and ok.json()["cidr"] == "203.0.113.0/24"
    assert (
        await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "203.0.113.5/24"})
    ).status_code == 409
    assert (
        await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "10.0.0.0/8"})
    ).status_code == 201
    listing = (await admin.get(f"{API}/admin/blocked-ips")).json()
    assert listing["total"] == 2
    agent, _ = await env.login("agent")
    assert (await agent.get(f"{API}/admin/blocked-ips")).status_code == 403
    assert (await agent.post(f"{API}/admin/blocked-ips", json={"cidr": BAD})).status_code == 403
    # another tenant's row is invisible and cannot be deleted
    async with env.owner_session(env.other_tenant_id) as db:
        await db.execute(
            text("INSERT INTO blocked_ips (tenant_id, cidr) VALUES (:t, '192.0.2.0/24')"),
            {"t": env.other_tenant_id},
        )
        foreign = await db.scalar(text("SELECT id FROM blocked_ips"))
        await db.commit()
    assert str(foreign) not in [b["id"] for b in listing["items"]]
    assert (await admin.delete(f"{API}/admin/blocked-ips/{foreign}")).status_code == 404
    bid = ok.json()["id"]
    assert (await admin.delete(f"{API}/admin/blocked-ips/{bid}")).status_code == 204
    assert len(await _audit(env, "blocked_ip.add")) == 2
    assert len(await _audit(env, "blocked_ip.remove")) == 1


async def test_cannot_block_own_ip(env):
    admin, _ = await env.login("admin")
    r = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "127.0.0.0/8"})
    assert r.status_code == 422


async def test_blocked_ip_enforcement(env):
    admin, _ = await env.login("admin")
    r = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": BAD})
    assert r.status_code == 201
    r = await _from(env, BAD).get(f"{API}/products")
    assert r.status_code == 403
    assert r.json() == {"error": {"code": "ip_blocked", "message": "Access denied"}}
    assert (await _from(env, "198.51.100.78").get(f"{API}/products")).status_code == 200
    # non-API paths and the notify endpoint are exempt
    assert (await _from(env, BAD).get("/healthz")).status_code == 200
    r = await _from(env, BAD).post(f"{API}/pay/notify/zpay", data={"x": "1"})
    assert "ip_blocked" not in r.text
    # an untrusted peer cannot frame another IP (XFF ignored) nor evade by spoofing
    c = _from(env, BAD, trusted=False)
    assert (await c.get(f"{API}/products")).status_code == 200  # peer 203.0.113.99, not blocked
    r = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "203.0.113.99"})
    assert r.status_code == 201
    r = await _from(env, "192.0.2.1", trusted=False).get(f"{API}/products")
    assert r.status_code == 403  # judged by the real peer address
    # allowlist wins
    await put_site(env, admin, {"security": {"staff_ip_allowlist": [BAD]}})
    assert (await _from(env, BAD).get(f"{API}/products")).status_code == 200
    # expiry
    async with env.owner_session() as db:
        await db.execute(text("UPDATE blocked_ips SET expires_at = now() - interval '1 second'"))
        await db.commit()
    await env.res.redis.delete(ipblock.cfg_key(env.tenant_id))
    await put_site(env, admin, {"security": {"staff_ip_allowlist": []}})
    assert (await _from(env, BAD).get(f"{API}/products")).status_code == 200
    # deleting a block lifts it at once
    ok = await admin.post(f"{API}/admin/blocked-ips", json={"cidr": "192.0.2.0/24"})
    assert (await _from(env, "192.0.2.9").get(f"{API}/products")).status_code == 403
    await admin.delete(f"{API}/admin/blocked-ips/{ok.json()['id']}")
    assert (await _from(env, "192.0.2.9").get(f"{API}/products")).status_code == 200


async def test_fail_open_on_redis_error(env, monkeypatch):
    admin, _ = await env.login("admin")
    await admin.post(f"{API}/admin/blocked-ips", json={"cidr": BAD})

    async def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(env.res.redis, "get", boom)
    r = await _from(env, BAD).get(f"{API}/products")
    assert r.json().get("error", {}).get("code") != "ip_blocked"
