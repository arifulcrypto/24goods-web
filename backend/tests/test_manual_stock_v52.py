"""v5.2 manual-delivery stock: default, reserve, release, no oversell, tenant isolation."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.modules.orders.service import expire_pending

BODY = {
    "title_i18n": {"en": "Manual", "zh-CN": "人工"},
    "price": "5.00",
    "delivery_mode": "manual",
    "category": "gift_cards",
}


async def stock(env, pid) -> int | None:
    return await env.scalar("SELECT manual_stock FROM products WHERE id=:i", i=str(pid))


async def stocked(env, n: int | None):
    p = await env.product(mode="manual", price=500, floor=100)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE products SET manual_stock=:n WHERE id=:i"), {"n": n, "i": str(p.id)}
        )
        await db.commit()
    return p


def buy(client, p, qty=1):
    return client.post("/api/v1/orders", json={"product_id": str(p.id), "qty": qty})


async def test_default_one_and_admin_edit_audited(env):
    admin, _ = await env.login("admin")
    r = await admin.post("/api/v1/admin/products", json=BODY)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["manual_stock"] == 1 and p["stock"] == 1
    pub = (await env.client().get(f"/api/v1/products/{p['id']}")).json()
    assert pub["stock"] == 1
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"manual_stock": 5})
    assert r.json()["manual_stock"] == 5
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"manual_stock": None})
    assert r.json()["manual_stock"] is None
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='product.updated'") == 2
    r = await admin.post("/api/v1/admin/products", json={**BODY, "manual_stock": None})
    assert r.json()["manual_stock"] is None
    r = await admin.post("/api/v1/admin/products", json={**BODY, "manual_stock": -1})
    assert r.status_code == 422


async def test_order_decrements_then_out_of_stock(env):
    p = await stocked(env, 1)
    b1, _ = await env.login("buyer")
    b2, _ = await env.login("buyer")
    assert (await buy(b1, p)).status_code in (200, 201)
    assert await stock(env, p.id) == 0
    r = await buy(b2, p)
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    assert (await env.client().get(f"/api/v1/products/{p.id}")).json()["stock"] == 0


async def test_cancel_releases_once(env):
    p = await stocked(env, 3)
    b, _ = await env.login("buyer")
    o = (await buy(b, p, 2)).json()
    assert await stock(env, p.id) == 1
    r = await b.post(f"/api/v1/orders/{o['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert await stock(env, p.id) == 3
    r = await b.post(f"/api/v1/orders/{o['id']}/cancel", json={})
    assert r.status_code == 409
    assert await stock(env, p.id) == 3


async def test_expiry_releases_once(env):
    p = await stocked(env, 2)
    b, _ = await env.login("buyer")
    await buy(b, p)
    assert await stock(env, p.id) == 1
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '1 second'"))
        await db.commit()
    assert await expire_pending(env.res.maker, env.res) == 1
    assert await stock(env, p.id) == 2
    assert await expire_pending(env.res.maker, env.res) == 0
    assert await stock(env, p.id) == 2


async def test_null_is_unlimited(env):
    p = await stocked(env, None)
    b, _ = await env.login("buyer")
    for _ in range(3):
        assert (await buy(b, p, 4)).status_code in (200, 201)
    assert await stock(env, p.id) is None


async def test_concurrent_orders_do_not_oversell(env):
    p = await stocked(env, 2)
    clients = [(await env.login("buyer"))[0] for _ in range(6)]
    rs = await asyncio.gather(*[buy(c, p) for c in clients])
    ok = [r for r in rs if r.status_code in (200, 201)]
    assert len(ok) == 2, [r.status_code for r in rs]
    assert all(r.json()["error"]["code"] == "out_of_stock" for r in rs if r not in ok)
    assert await stock(env, p.id) == 0


async def test_checkout_path_reserves_and_refuses(env):
    p = await stocked(env, 2)
    b, _ = await env.login("buyer")
    r = await b.post("/api/v1/checkout", json={"items": [{"product_id": str(p.id), "qty": 3}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    assert await stock(env, p.id) == 2
    r = await b.post("/api/v1/checkout", json={"items": [{"product_id": str(p.id), "qty": 2}]})
    assert r.status_code in (200, 201), r.text
    assert await stock(env, p.id) == 0
    co = r.json()["checkout"]
    r = await b.post(f"/api/v1/checkouts/{co['checkout_no']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert await stock(env, p.id) == 2


async def test_cross_tenant_edit_refused(env):
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", tenant_id=env.other_tenant_id)
    r = await admin.patch(f"/api/v1/admin/products/{p.id}", json={"manual_stock": 99})
    assert r.status_code == 404
    assert await stock(env, p.id) is None
