"""review-v5.2 §1: staff access (Turnstile on admin login, IP allowlist, limiter bypass)."""

from __future__ import annotations

import httpx

from app.core import globallimit
from tests.conftest import PASSWORD
from tests.test_v5_core import SiteVerify, put_site, set_secret

API = "/api/v1"
OFFICE = "198.51.100.7"


def _from(env, ip: str, *, trusted: bool = True) -> httpx.AsyncClient:
    """Client whose request carries X-Forwarded-For (trusted proxy peer unless ``trusted``)."""
    peer = ("127.0.0.1", 1) if trusted else ("203.0.113.99", 1)
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, client=peer),  # type: ignore[arg-type]
        base_url="http://testserver",
        headers={"X-Requested-With": "fetch", "X-Forwarded-For": ip},
    )
    env.clients.append(c)
    return c


async def test_admin_login_turnstile_and_allowlist_skip(env, monkeypatch):
    admin, _ = await env.login("admin")
    await env.user("admin", email="boss@example.com")
    await put_site(env, admin, {"security": {"turnstile_enabled": True, "turnstile_site_key": "k"}})
    await set_secret(admin, "turnstile_secret", "ts-secret-1234")
    sv = SiteVerify(ok=True)
    monkeypatch.setattr(env.res.http, "post", sv.post)
    creds = {"email": "boss@example.com", "password": PASSWORD}
    c = env.client()
    r = await c.post(f"{API}/auth/login", json=creds)
    assert r.status_code == 400 and r.json()["error"]["code"] == "turnstile_failed"
    r = await c.post(f"{API}/auth/login", json={**creds, "turnstile_token": "t1"})
    assert r.status_code == 200 and len(sv.calls) == 1
    # allowlisted office IP: no token, siteverify not called
    out = await put_site(env, admin, {"security": {"staff_ip_allowlist": [OFFICE, "10.1.0.0/16"]}})
    assert out["security"]["staff_ip_allowlist"] == [OFFICE, "10.1.0.0/16"]
    r = await _from(env, OFFICE).post(f"{API}/auth/login", json=creds)
    assert r.status_code == 200 and len(sv.calls) == 1
    # other IP, and an untrusted peer claiming the office IP, still need a token
    r = await _from(env, "198.51.100.8").post(f"{API}/auth/login", json=creds)
    assert r.status_code == 400
    r = await _from(env, OFFICE, trusted=False).post(f"{API}/auth/login", json=creds)
    assert r.status_code == 400


async def test_allowlist_validation(env):
    admin, _ = await env.login("admin")
    for bad in (["not-an-ip"], ["10.0.0.0/99"], [f"10.0.0.{i}" for i in range(51)]):
        r = await admin.put(f"{API}/admin/site", json={"security": {"staff_ip_allowlist": bad}})
        assert r.status_code == 422, bad
    out = await put_site(env, admin, {"security": {"staff_ip_allowlist": ["2001:db8::/32", ""]}})
    assert out["security"]["staff_ip_allowlist"] == ["2001:db8::/32"]


async def test_global_limit_bypass_for_allowlist_and_staff(env, monkeypatch):
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    admin, _ = await env.login("admin")
    await put_site(
        env,
        admin,
        {
            "rate_limit": {"enabled": True, "limit": 10, "window_s": 60},
            "security": {"staff_ip_allowlist": [OFFICE]},
        },
    )
    path = f"{API}/categories"
    # buyer on a normal IP is limited
    buyer = _from(env, "192.0.2.50")
    codes = [(await buyer.get(path)).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    # allowlisted IP is never limited
    office = _from(env, OFFICE)
    assert [(await office.get(path)).status_code for _ in range(15)] == [200] * 15
    # a staff session is let through on the over-limit path; a buyer session is not
    staff, _u = await env.login("agent")
    staff.headers["X-Forwarded-For"] = "192.0.2.51"
    assert [(await staff.get(path)).status_code for _ in range(15)] == [200] * 15
    b2, _u = await env.login("buyer")
    b2.headers["X-Forwarded-For"] = "192.0.2.52"
    codes = [(await b2.get(path)).status_code for _ in range(12)]
    assert codes[-1] == 429
    # spoofed XFF from an untrusted peer does not bypass (peer address is used)
    spoof = _from(env, OFFICE, trusted=False)
    codes = [(await spoof.get(path)).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
