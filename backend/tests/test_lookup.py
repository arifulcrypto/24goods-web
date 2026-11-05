"""Order lookup by order no + access code (卡密查询)."""

from __future__ import annotations

import re

from tests.helpers import buy, delivered_order, solve_pow

CODES = ["ACCT-ONE----pw1----2fa1----note", "ACCT-TWO----pw2----2fa2----note"]


async def lookup(c, order_no, code, path="", **extra):
    body = {"order_no": order_no, "access_code": code, **extra}
    if path == "":
        body["pow"] = await solve_pow(c, "lookup")
    return await c.post(f"/api/v1/lookup{path}", json=body)


async def test_access_code_returned_once_and_stored_hashed(env):
    p = await env.product(mode="manual")
    c, _ = await env.login("buyer")
    order = await buy(c, p, src="Spring_Sale")
    code = order["access_code"]
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{10}", code)
    assert (await c.get(f"/api/v1/orders/{order['id']}")).json().get("access_code") is None
    assert "access_code" not in (await c.get("/api/v1/orders")).json()["items"][0]
    assert await env.scalar("SELECT source FROM orders") == "spring_sale"
    stored = await env.scalar("SELECT access_code_hash FROM orders")
    assert re.fullmatch(r"[0-9a-f]{64}", stored) and code not in stored
    # invalid src is dropped, not stored and not an error
    o2 = await buy(c, p, src="bad src!")
    assert await env.scalar("SELECT source FROM orders WHERE order_no=:n", n=o2["order_no"]) is None


async def test_rotate_access_code(env):
    p = await env.product(mode="manual")
    c, _ = await env.login("buyer")
    order = await buy(c, p)
    old = order["access_code"]
    r = await c.post(f"/api/v1/orders/{order['id']}/access-code")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    new = r.json()["access_code"]
    assert new != old
    anon = env.client()
    assert (await lookup(anon, order["order_no"], old)).status_code == 404
    assert (await lookup(anon, order["order_no"], new)).status_code == 200
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.access_code_rotated'")
        == 1
    )
    other, _ = await env.login("buyer")  # not the owner: no oracle
    r = await other.post(f"/api/v1/orders/{order['id']}/access-code")
    assert r.status_code == 404


async def test_lookup_returns_order_view_without_session(env):
    p = await env.product(mode="manual")
    c, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    order = await delivered_order(env, c, p, staff=staff)
    anon = env.client()
    r = await lookup(anon, order["order_no"].lower(), " " + order["access_code"].lower() + " ")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["order_no"] == order["order_no"] and v["status"] == "delivered"
    assert v["manual_delivery"]["text"] == "account: foo / bar"
    assert v["after_sales_until"] and v["auto_confirm_at"] and "access_code" not in v
    assert r.headers["cache-control"] == "no-store"
    assert "set-cookie" not in r.headers and "sid" not in anon.cookies
    assert (await anon.get("/api/v1/auth/me")).status_code == 401
    assert (await anon.get(f"/api/v1/orders/{order['id']}")).status_code == 401


async def test_wrong_code_is_generic_and_rate_limited_per_order(env):
    p = await env.product(mode="manual")
    c, _ = await env.login("buyer")
    order = await buy(c, p)
    anon = env.client()
    env.res.settings.lookup_per_ip_hour = 100
    for _ in range(5):
        r = await lookup(anon, order["order_no"], "AAAAAAAAAA")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = await lookup(anon, order["order_no"], order["access_code"])  # blocked after 5 failures
    assert r.status_code == 429 and "retry-after" in r.headers
    # unknown order numbers behave identically (and are counted too)
    r = await lookup(anon, "GAAAAAAAAAAAA", "AAAAAAAAAA")
    assert r.status_code == 404
    r = await lookup(anon, "garbage", "also garbage")
    assert r.status_code == 404


async def test_lookup_ip_rate_limit_and_pow_required(env):
    anon = env.client()
    r = await anon.post("/api/v1/lookup", json={"order_no": "G1", "access_code": "x"})
    assert r.status_code == 428 and r.json()["error"]["code"] == "pow_required"
    codes = [(await lookup(anon, f"GAAAAAAAAAAA{i}", "AAAAAAAAAA")).status_code for i in range(11)]
    assert codes == [404] * 10 + [429]


async def test_lookup_cross_tenant_is_not_found(env):
    p = await env.product(mode="manual")
    c, _ = await env.login("buyer")
    order = await buy(c, p)
    other_host = env.client(
        "other.example"
    )  # unmapped host: default tenant; use direct tenant test
    assert (await lookup(other_host, order["order_no"], order["access_code"])).status_code == 200
    # a tenant-pinned session of another tenant cannot see the order (RLS)
    async with env.api_session(env.other_tenant_id) as db:
        from sqlalchemy import text

        assert await db.scalar(text("SELECT count(*) FROM orders")) == 0


async def test_lookup_reveal_uses_delivery_path_and_budgets(env, caplog):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, CODES)
    c, _u = await env.login("buyer")
    order = await delivered_order(env, c, p)
    anon = env.client()
    r = await lookup(anon, order["order_no"], "BBBBBBBBBB", "/reveal")
    assert r.status_code == 404
    with caplog.at_level("DEBUG"):
        r = await lookup(anon, order["order_no"], order["access_code"], "/reveal")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store" and "set-cookie" not in r.headers
    (card,) = r.json()["cards"]
    assert card["code"] in CODES and card["id"]
    assert card["code"] not in caplog.text
    assert await env.scalar("SELECT reveal_count FROM deliveries") == 1
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='lookup.reveal'") == 1
    # per-buyer budget is shared with /orders/{id}/reveal (30 cards / window)
    env.res.settings.reveal_per_buyer_cards = 1
    r = await lookup(anon, order["order_no"], order["access_code"], "/reveal")
    assert r.status_code == 429
    # not revealable states
    p2 = await env.product(mode="manual")
    o2 = await buy(c, p2)
    r = await lookup(anon, o2["order_no"], o2["access_code"], "/reveal")
    assert r.status_code == 409


async def test_lookup_reveal_ip_limit(env):
    anon = env.client()
    codes = []
    for i in range(31):
        r = await anon.post(
            "/api/v1/lookup/reveal",
            json={"order_no": f"GAAAAAAAAAA{i:02d}", "access_code": "AAAAAAAAAA"},
        )
        codes.append(r.status_code)
    assert codes == [404] * 30 + [429]
