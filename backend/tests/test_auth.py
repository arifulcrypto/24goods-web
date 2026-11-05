from __future__ import annotations

from tests.conftest import PASSWORD

REG = {
    "email": "Alice@Example.com",
    "password": "s3cret-password",
    "display_name": "Alice",
    "locale": "zh-CN",
}


async def test_register_login_logout_and_cookie_flags(env):
    c = env.client()
    r = await c.post("/api/v1/auth/register", json=REG)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "id": r.json()["id"],
        "email": "alice@example.com",
        "display_name": "Alice",
        "role": "buyer",
        "locale": "zh-CN",
    }
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("sid=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=604800" in cookie  # buyer: 7 days
    assert (await c.get("/api/v1/auth/me")).status_code == 200
    pw_hash = await env.scalar("SELECT pw_hash FROM users WHERE email='alice@example.com'")
    assert pw_hash.startswith("$argon2id$")
    assert (await c.post("/api/v1/auth/logout")).status_code == 204
    r = await c.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


async def test_duplicate_and_validation(env):
    c = env.client()
    assert (await c.post("/api/v1/auth/register", json=REG)).status_code == 200
    r = await env.client().post("/api/v1/auth/register", json={**REG, "email": "alice@EXAMPLE.com"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    r = await env.client().post(
        "/api/v1/auth/register", json={**REG, "email": "b@x.io", "password": "short"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    r = await env.client().post("/api/v1/auth/register", json={**REG, "email": "not-an-email"})
    assert r.status_code == 422


async def test_login_generic_errors_and_session_rotation(env):
    user = await env.user("buyer", email="bob@example.com")
    c = env.client()
    bad = await c.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "nope"})
    unknown = await env.client().post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "nope"}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()  # no user enumeration
    r = await c.post("/api/v1/auth/login", json={"email": "BOB@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["id"] == str(user.id)
    first = c.cookies["sid"]
    r = await c.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": PASSWORD})
    second = c.cookies["sid"]
    assert first != second
    stale = env.client()
    stale.cookies.set("sid", first)
    assert (await stale.get("/api/v1/auth/me")).status_code == 401  # old session destroyed


async def test_staff_session_ttl(env):
    await env.user("agent", email="agent@example.com")
    r = await env.client().post(
        "/api/v1/auth/login", json={"email": "agent@example.com", "password": PASSWORD}
    )
    assert "Max-Age=43200" in r.headers["set-cookie"]


async def test_login_rate_limit_per_ip(env):
    c = env.client()
    codes = [
        (
            await c.post("/api/v1/auth/login", json={"email": f"x{i}@e.com", "password": "x"})
        ).status_code
        for i in range(6)
    ]
    assert codes == [401] * 5 + [429]
    r = await c.post("/api/v1/auth/login", json={"email": "x@e.com", "password": "x"})
    assert r.json()["error"]["code"] == "rate_limited" and "retry-after" in r.headers


async def test_login_rate_limit_per_email(env, services):
    await env.user("buyer", email="target@example.com")
    codes = []
    for i in range(11):
        c = env.client()
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "target@example.com", "password": "wrong"},
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        )
        codes.append(r.status_code)
    assert codes[:10] == [401] * 10 and codes[10] == 429


async def test_register_rate_limit(env):
    c = env.client()
    codes = []
    for i in range(6):
        r = await c.post("/api/v1/auth/register", json={**REG, "email": f"u{i}@example.com"})
        codes.append(r.status_code)
    assert codes == [200] * 5 + [429]


async def test_untrusted_forwarded_for_is_ignored():
    from ipaddress import ip_network

    from app.core.middleware import client_ip_from

    trusted = (ip_network("10.0.0.0/8"),)
    assert client_ip_from("198.51.100.7", "1.2.3.4", trusted) == "198.51.100.7"
    assert client_ip_from("10.0.0.2", "1.2.3.4, 10.0.0.9", trusted) == "1.2.3.4"
    assert client_ip_from("10.0.0.2", "6.6.6.6, 1.2.3.4", trusted) == "1.2.3.4"
    assert client_ip_from("10.0.0.2", "garbage", trusted) == "10.0.0.2"


async def test_csrf_header_required_for_unsafe_methods(env):
    raw = env.client(csrf=False)
    r = await raw.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"})
    assert r.status_code == 403 and r.json() == {
        "error": {"code": "csrf", "message": "Missing or invalid CSRF header"}
    }
    r = await raw.post("/api/v1/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 403
    assert (await raw.get("/api/v1/categories")).status_code == 200  # safe method


async def test_security_headers_and_request_id(env):
    r = await env.client().get("/api/v1/categories", headers={"X-Request-ID": "abcdef123456"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"] == "abcdef123456"
    r = await env.client().get("/api/v1/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
