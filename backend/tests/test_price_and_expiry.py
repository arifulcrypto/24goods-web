from __future__ import annotations

from sqlalchemy import text

from app.modules.orders.service import expire_pending
from tests.helpers import notify_params, open_conversation, send_quote


async def _setup(env):
    product = await env.product(mode="manual", price=10000, floor=8000)
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer, product.id)
    order = (
        await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 2})
    ).json()
    return product, buyer, agent, conv, order


async def test_price_change_rules(env):
    _product, buyer, agent, conv, order = await _setup(env)
    url = f"/api/v1/admin/orders/{order['id']}/price"
    r = await agent.patch(url, json={"amount": "159.99", "reason": "too low"})
    assert r.status_code == 422  # floor 80.00 x 2
    r = await agent.patch(url, json={"amount": "abc", "reason": "x"})
    assert r.status_code == 422
    r = await agent.patch(url, json={"amount": "160.00", "reason": "bulk discount"})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["amount"] == "160.00" and o["version"] == order["version"] + 1
    assert o["buyer"]["id"]
    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    sysmsg = [m for m in msgs if m["type"] == "system"][-1]
    assert sysmsg["payload"]["event"] == "price_changed"
    assert (
        sysmsg["payload"]["old_amount"] == "200.00" and sysmsg["payload"]["new_amount"] == "160.00"
    )
    order_card = [m for m in msgs if m["type"] == "order_card"][-1]
    assert order_card["payload"]["amount"] == "160.00"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.price_changed'") == 1
    )
    # pay with the new amount
    pay = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert "money=160.00" in pay.json()["pay_url"]


async def test_price_change_only_while_pending(env):
    _product, buyer, agent, _conv, order = await _setup(env)
    r = await agent.post(f"/api/v1/admin/orders/{order['id']}/cancel", json={"reason": "dup"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = await agent.patch(
        f"/api/v1/admin/orders/{order['id']}/price", json={"amount": "190.00", "reason": "x"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409
    r = await agent.post(f"/api/v1/admin/orders/{order['id']}/cancel", json={"reason": "again"})
    assert r.status_code == 409


async def test_pending_orders_expire(env):
    _product, buyer, _agent, _conv, order = await _setup(env)
    assert order["expires_at"]
    assert await expire_pending(env.res.maker, env.res) == 0
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '1 second'"))
        await db.commit()
    assert await expire_pending(env.res.maker, env.res) == 1
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "expired"
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409


async def test_expired_quote_cannot_be_accepted(env):
    product, buyer, agent, conv, _ = await _setup(env)
    q = await send_quote(agent, conv["id"], amount="170.00", qty=2, product_id=str(product.id))
    async with env.owner_session() as db:
        await db.execute(text("UPDATE quotes SET expires_at = now() - interval '1 second'"))
        await db.commit()
    r = await buyer.post(f"/api/v1/quotes/{q['quote_id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "quote_expired"
    assert await env.scalar("SELECT status FROM quotes") == "expired"


async def test_quote_below_floor_and_tampered_quote(env):
    product, buyer, agent, conv, _ = await _setup(env)
    r = await agent.post(
        f"/api/v1/admin/conversations/{conv['id']}/quotes",
        json={
            "title": "x",
            "amount": "100.00",
            "qty": 2,
            "expires_in_minutes": 30,
            "product_id": str(product.id),
        },
    )
    assert r.status_code == 422
    q = await send_quote(agent, conv["id"], amount="170.00", qty=2, product_id=str(product.id))
    # a DB-level tamper (e.g. SQL injection) of the amount breaks the HMAC
    async with env.owner_session() as db:
        await db.execute(text("UPDATE quotes SET amount_minor = 1"))
        await db.commit()
    r = await buyer.post(f"/api/v1/quotes/{q['quote_id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='quote.signature_invalid'")
        == 1
    )
    assert await env.scalar("SELECT count(*) FROM orders WHERE quote_id IS NOT NULL") == 0


async def test_admin_ledger_and_audit_pages(env):
    _product, _buyer, agent, _conv, order = await _setup(env)
    admin, _ = await env.login("admin")
    c = env.client(csrf=False)
    r = await c.get("/api/v1/pay/notify/zpay", params=notify_params(order["order_no"], "200.00"))
    # re-query hits the in-process mock gateway, which has no record of this order
    assert r.text == "success"
    led = (await admin.get("/api/v1/admin/ledger")).json()
    assert led["total"] == 0
    audit = (await admin.get("/api/v1/admin/audit", params={"page_size": 50})).json()
    assert any(a["action"] == "payment.manual_review" for a in audit["items"])
    orders = (await agent.get("/api/v1/admin/orders", params={"status": "manual_review"})).json()
    assert orders["total"] == 1
    found = (await agent.get("/api/v1/admin/orders", params={"q": order["order_no"]})).json()
    assert found["total"] == 1
