from __future__ import annotations

import uuid

ZERO = str(uuid.UUID(int=0))

STAFF = [
    ("GET", "/api/v1/admin/stats", None),
    ("GET", "/api/v1/admin/products", None),
    ("GET", "/api/v1/admin/orders", None),
    ("GET", "/api/v1/admin/conversations", None),
    ("GET", "/api/v1/admin/agents", None),
    ("GET", "/api/v1/admin/agent/status", None),
    ("PATCH", f"/api/v1/admin/orders/{ZERO}/price", {"amount": "1.00", "reason": "x"}),
    ("POST", f"/api/v1/admin/orders/{ZERO}/cancel", {"reason": "x"}),
    ("GET", "/api/v1/admin/stream", None),
]
ADMIN_ONLY = [
    (
        "POST",
        "/api/v1/admin/products",
        {
            "title_i18n": {"en": "x"},
            "price": "1.00",
            "delivery_mode": "manual",
            "category": "ai_tools",
        },
    ),
    ("PATCH", f"/api/v1/admin/products/{ZERO}", {"price": "2.00"}),
    ("DELETE", f"/api/v1/admin/products/{ZERO}", None),
    ("POST", f"/api/v1/admin/products/{ZERO}/cards/import", {"lines": "A\nB"}),
    ("GET", f"/api/v1/admin/products/{ZERO}/cards", None),
    ("GET", "/api/v1/admin/ledger", None),
    ("GET", "/api/v1/admin/audit", None),
]


async def test_anonymous_and_buyer_are_rejected(env):
    anon = env.client()
    buyer, _ = await env.login("buyer")
    for method, url, body in STAFF + ADMIN_ONLY:
        r = await anon.request(method, url, json=body)
        assert r.status_code == 401, (method, url)
        r = await buyer.request(method, url, json=body)
        assert r.status_code == 403, (method, url)
        assert r.json()["error"]["code"] == "forbidden"


async def test_agent_cannot_use_admin_only_endpoints(env):
    agent, _ = await env.login("agent")
    for method, url, body in ADMIN_ONLY:
        r = await agent.request(method, url, json=body)
        assert r.status_code == 403, (method, url)
    assert (await agent.get("/api/v1/admin/stats")).status_code == 200


async def test_admin_product_crud_and_stats(env):
    admin, _ = await env.login("admin")
    body = {
        "title_i18n": {"en": "Gift card", "zh-CN": "礼品卡"},
        "description_i18n": {"en": "desc"},
        "price": "50.00",
        "floor_price": "45.00",
        "delivery_mode": "auto_card",
        "category": "gift_cards",
        "status": "active",
    }
    r = await admin.post("/api/v1/admin/products", json=body)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["title_i18n"] == {"en": "Gift card", "zh-CN": "礼品卡"}
    assert p["floor_price"] == "45.00" and p["stock"] == 0 and "title" not in p
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"price": "40.00"})
    assert r.status_code == 422  # below floor
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"price": "55.00"})
    assert r.json()["price"] == "55.00"
    r = await admin.post("/api/v1/admin/products", json={**body, "category": "weapons"})
    assert r.status_code == 422
    r = await admin.post("/api/v1/admin/products", json={**body, "images": ["https://evil/x.png"]})
    assert r.status_code == 422
    stats = (await admin.get("/api/v1/admin/stats")).json()
    assert stats["low_stock"][0]["id"] == p["id"] and stats["revenue_today"] == "0.00"
    assert (await admin.delete(f"/api/v1/admin/products/{p['id']}")).status_code == 204
    listing = (await admin.get("/api/v1/admin/products", params={"status": "archived"})).json()
    assert listing["total"] == 1
    assert (await env.client().get(f"/api/v1/products/{p['id']}")).status_code == 404
    audit = (await admin.get("/api/v1/admin/audit")).json()
    actions = {a["action"] for a in audit["items"]}
    assert {"product.created", "product.updated", "product.archived"} <= actions


async def test_product_image_upload_is_public(env):
    from tests.test_chat import png_bytes

    admin, _ = await env.login("admin")
    p = await env.product()
    r = await admin.post(
        f"/api/v1/admin/products/{p.id}/images",
        files={"file": ("cover.png", png_bytes(), "image/png")},
    )
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    anon = env.client()
    f = await anon.get(url)
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    assert "public" in f.headers["cache-control"]
    assert (await anon.get(f"/api/v1/products/{p.id}")).json()["cover"] == url
