"""End-to-end: chat -> quote -> accept -> pay URL -> mock gateway -> notify -> delivery
-> reveal."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import text

from app.payments import epay
from tests.conftest import ZPAY_KEY
from tests.helpers import open_conversation, send_quote, simulate_gateway_payment

CODES = ["DEMO-AAAA-1111-ZZZZ", "DEMO-BBBB-2222-YYYY", "DEMO-CCCC-3333-XXXX"]


async def test_quote_to_reveal_auto_card(env, caplog):
    product = await env.product(mode="auto_card", price=1990, floor=1200)
    await env.import_codes(product, CODES)
    buyer, buyer_user = await env.login("buyer")
    agent, _ = await env.login("agent")

    conv = await open_conversation(buyer, product.id)
    assert conv["product"]["product_id"] == str(product.id)

    quote = await send_quote(agent, conv["id"], amount="15.00", product_id=str(product.id))
    assert quote["status"] == "offered" and quote["amount"] == "15.00"

    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    assert [m["type"] for m in msgs] == ["product_card", "quote_card"]

    r = await buyer.post(f"/api/v1/quotes/{quote['quote_id']}/accept")
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["status"] == "pending_payment"
    assert order["amount"] == "15.00" and order["currency"] == "CNY"
    assert order["quote_id"] == quote["quote_id"]
    assert order["order_no"].startswith("G") and len(order["order_no"]) == 13
    assert order["can_reveal"] is False

    # accepting twice is refused
    r = await buyer.post(f"/api/v1/quotes/{quote['quote_id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"

    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    assert [m["type"] for m in msgs[-2:]] == ["order_card", "system"]
    assert msgs[-1]["payload"]["event"] == "quote_accepted" and msgs[-1]["text"]
    assert msgs[1]["payload"]["status"] == "accepted"  # quote card reflects current status
    assert msgs[1]["payload"]["order_id"] == order["id"]

    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200, r.text
    pay_url = r.json()["pay_url"]
    u = urlsplit(pay_url)
    assert u.scheme == "https" and u.path.endswith("/submit.php")
    params = dict(parse_qsl(u.query))
    assert params["money"] == "15.00" and params["type"] == "alipay"
    assert params["out_trade_no"] == order["order_no"]
    assert epay.verify_signature(params, ZPAY_KEY)

    # return_url never fulfils
    r = await buyer.get(
        "/api/v1/pay/return/zpay",
        params={"out_trade_no": order["order_no"]},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == f"/orders/{order['order_no']}"
    assert (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"] == "pending_payment"

    resp = await simulate_gateway_payment(env, pay_url)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("http://testserver/api/v1/pay/return/zpay?")

    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "paid" and o["paid_at"] and o["channel"] == "zpay_alipay"

    assert await env.drain() == 1
    o = (await buyer.get(f"/api/v1/orders/{order['order_no']}")).json()  # lookup by order_no too
    assert o["status"] == "delivered" and o["can_reveal"] is True

    with caplog.at_level("DEBUG"):
        r = await buyer.post(f"/api/v1/orders/{order['id']}/reveal")
    assert r.status_code == 200, r.text
    (secret,) = r.json()["secrets"]
    assert secret in CODES
    assert r.headers.get("cache-control") is None or "no-store" in r.headers.get(
        "cache-control", ""
    )
    assert secret not in caplog.text  # never logged
    assert ZPAY_KEY not in caplog.text

    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    events = [m["payload"]["event"] for m in msgs if m["type"] == "system"]
    assert events == ["quote_accepted", "paid", "delivered"]
    assert all(m["text"] for m in msgs if m["type"] == "system")  # text fallback
    assert next(m for m in msgs if m["type"] == "order_card")["payload"]["status"] == "delivered"

    # exactly one card sold, ledger balanced, reveal audited
    assert await env.scalar("SELECT count(*) FROM card_stock WHERE status='sold'") == 1
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    async with env.owner_session() as db:
        sums = (
            await db.execute(
                text("SELECT txn_id, sum(amount_minor) FROM ledger_entries GROUP BY txn_id")
            )
        ).all()
    assert len(sums) == 2 and all(s == 0 for _, s in sums)
    assert (
        await env.scalar("SELECT amount_minor FROM ledger_entries WHERE account='fees:zpay'") == 24
    )  # 1.6 % of 15
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.reveal'") == 1
    assert await env.scalar("SELECT reveal_count FROM deliveries") == 1

    r = await buyer.post(f"/api/v1/orders/{order['id']}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert r.json()["can_reveal"] is True

    # reveal is rate limited to 10/h/order
    codes = [
        (await buyer.post(f"/api/v1/orders/{order['id']}/reveal")).status_code for _ in range(10)
    ]
    assert codes[:9] == [200] * 9 and codes[9] == 429
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/reveal")).json()["error"]["code"] == (
        "rate_limited"
    )
    assert buyer_user.id is not None


async def test_buy_now_manual_delivery(env):
    product = await env.product(
        mode="manual", price=8900, floor=7000, title="Logo design", category="design_services"
    )
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 2})
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["amount"] == "178.00" and order["delivery_mode"] == "manual"
    pay_url = (
        await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    ).json()["pay_url"]
    await simulate_gateway_payment(env, pay_url)
    assert await env.drain() == 1
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "paid" and o["can_reveal"] is False
    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    (paid,) = [m for m in msgs if m["type"] == "system"]
    assert paid["payload"]["event"] == "paid" and paid["payload"]["awaiting_delivery"] is True
    assert "agent will deliver" in paid["text"]

    # reveal is not available for manual orders
    r = await buyer.post(f"/api/v1/orders/{order['id']}/reveal")
    assert r.status_code == 409

    r = await agent.post(
        f"/api/v1/admin/orders/{order['id']}/deliver",
        json={"text": "Your logo files: see attached link"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "delivered"
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["manual_delivery"]["text"].startswith("Your logo files")
    r = await agent.post(f"/api/v1/admin/orders/{order['id']}/deliver", json={"text": "again"})
    assert r.status_code == 409
    assert await env.scalar("SELECT sold FROM products WHERE id=:id", id=product.id) == 2

    detail = (await agent.get(f"/api/v1/admin/orders/{order['id']}")).json()
    assert [e["to"] for e in detail["events"]] == ["pending_payment", "paid", "delivered"]
    assert detail["buyer"]["email"].endswith("@example.com")


async def test_buy_now_checks_stock(env):
    product = await env.product(mode="auto_card")
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    await env.import_codes(product, ["ONE-CODE-0001"])
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 2})
    assert r.json()["error"]["code"] == "out_of_stock"
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 11})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 200


async def test_paid_but_out_of_stock_goes_to_manual_review(env):
    # v8: the last card is reserved at order creation, so a second buyer is refused up front;
    # an order only ends up held when the stock disappears AFTER payment (e.g. cards voided)
    product = await env.product(mode="auto_card")
    await env.import_codes(product, ["ONLY-ONE-CODE"])
    b1, _ = await env.login("buyer")
    b2, _ = await env.login("buyer")
    o = (await b1.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    r = await b2.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    url = (await b1.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})).json()
    await simulate_gateway_payment(env, url["pay_url"])
    async with env.owner_session() as db:
        await db.execute(text("UPDATE card_stock SET status='void'"))
        await db.commit()
    assert await env.drain() == 1
    assert (await b1.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "manual_review"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='delivery.out_of_stock'") == 1
    )
