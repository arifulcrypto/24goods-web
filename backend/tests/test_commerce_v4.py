"""Commerce v4: channel surcharge, cart checkout, cancel/close, buyer input, product cards,
site settings, global rate limit, markdown descriptions (docs/review-v4.md)."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.core import globallimit
from app.modules.orders.service import expire_pending
from tests.helpers import buy, notify_params, open_conversation, pay_order, simulate_gateway_payment
from tests.test_chat import png_bytes

NOTIFY = "/api/v1/pay/notify/zpay"
CODES = [f"ACC{i}----pw{i}----2fa{i}----n" for i in range(8)]


async def set_settings(env, patch: dict, tenant_id=None) -> None:
    async with env.owner_session(tenant_id) as db:
        await db.execute(
            text("UPDATE tenants SET settings = settings || CAST(:p AS jsonb) WHERE id = :t"),
            {"p": json.dumps(patch), "t": str(tenant_id or env.tenant_id)},
        )
        await db.commit()


async def set_fee(env, permille: int | None) -> None:
    if permille is None:
        async with env.owner_session() as db:
            await db.execute(text("UPDATE tenants SET settings = settings - 'payments'"))
            await db.commit()
    else:
        await set_settings(
            env, {"payments": {"channels": {"zpay_alipay": {"surcharge_permille": permille}}}}
        )


async def leaks(env, table: str, needle: str) -> int:
    """Rows of an audit-like table whose text form contains ``needle``."""
    q = f"SELECT count(*) FROM {table} t WHERE t::text LIKE :n"  # noqa: S608
    return await env.scalar(q, n=f"%{needle}%")


async def checkout(client, items, **extra):
    r = await client.post("/api/v1/checkout", json={"items": items, **extra})
    return r


def line(p, qty=1, **kw):
    return {"product_id": str(p.id), "qty": qty, **kw}


async def three_way(env):
    """A buyer with a manual product (19.90) and an auto_card product (9.90, 3 codes)."""
    manual = await env.product(mode="manual", price=1990, floor=100, title="Manual")
    card = await env.product(mode="auto_card", price=990, floor=100, title="Card")
    await env.import_codes(card, CODES[:3])
    buyer, user = await env.login("buyer")
    return manual, card, buyer, user


# ------------------------------------------------------------------ §1 surcharge
async def test_fee_is_ceiled_server_side_and_default_is_30_permille(env):
    await set_fee(env, None)  # no config at all -> zpay_alipay enabled at 30 permille
    p = await env.product(mode="manual", price=333, floor=100)  # 3.33 * 3% = 0.0999 -> 0.10
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    assert (o["subtotal"], o["fee"], o["amount"], o["fee_rate"]) == ("3.33", "0.10", "3.43", "3%")
    p2 = await env.product(mode="manual", price=1990, floor=100)  # 59.7 -> 60
    o2 = await buy(buyer, p2)
    assert (o2["subtotal"], o2["fee"], o2["amount"]) == ("19.90", "0.60", "20.50")
    row = await env.scalar(
        "SELECT fee_permille * 1000000 + fee_minor FROM orders WHERE order_no = :n",
        n=o2["order_no"],
    )
    assert row == 30_000_060
    ch = (await buyer.get("/api/v1/site/payment-channels")).json()
    assert ch == [{"id": "zpay_alipay", "label": "Alipay", "fee_rate": "3%", "permille": 30}]
    zh = (await buyer.get("/api/v1/site/payment-channels?lang=zh-CN")).json()
    assert zh[0]["label"] == "支付宝"


async def test_pay_url_charges_total_and_reapplies_fee_when_config_changes(env):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    r = await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and "money=10.30" in r.json()["pay_url"]
    await set_fee(env, 50)  # config changed after the order was created
    r = await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and "money=10.50" in r.json()["pay_url"]  # re-applied (unpaid)


async def test_admin_price_sets_subtotal_and_recomputes_fee(env):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=20000, floor=100)
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    o = await buy(buyer, p)
    r = await agent.patch(
        f"/api/v1/admin/orders/{o['id']}/price", json={"amount": "100.00", "reason": "deal"}
    )
    assert r.status_code == 200
    v = r.json()
    assert (v["subtotal"], v["fee"], v["amount"]) == ("100.00", "3.00", "103.00")


async def test_fee_ledger_and_refund_reversal_balance(env):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    o = await buy(buyer, p)
    await pay_order(env, buyer, o)
    r = await staff.post(f"/api/v1/admin/orders/{o['id']}/deliver", json={"text": "done"})
    assert r.status_code == 200
    sums = await env.scalar(
        "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
        "HAVING sum(amount_minor) <> 0) x"
    )
    assert sums == 0
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='surcharge_revenue'"
        )
        == -60
    )
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='sales_revenue'"
        )
        == -1990
    )
    t = await buyer.post(
        f"/api/v1/orders/{o['id']}/after-sales",
        json={"reason": "invalid_code", "description": "x", "attachment_ids": []},
    )
    assert t.status_code == 200, t.text
    r = await admin.post(
        f"/api/v1/admin/after-sales/{t.json()['id']}/action", json={"action": "refund"}
    )
    assert r.status_code == 200
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='surcharge_revenue'"
        )
        == 0
    )
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='sales_revenue'"
        )
        == 0
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
            "HAVING sum(amount_minor) <> 0) x"
        )
        == 0
    )


# ------------------------------------------------------------------ §2 cart / checkout
async def test_cart_quote_flags_unavailable_and_totals(env):
    await set_fee(env, 30)
    manual, card, _buyer, _ = await three_way(env)
    draft = await env.product(mode="manual", status="draft")
    r = await env.client().post(  # no login needed
        "/api/v1/cart/quote",
        json={"items": [line(manual, 2), line(card, 5), line(draft), line(card, 1)]},
    )
    assert r.status_code == 200, r.text
    q = r.json()
    by = [(ln["available"], ln["max_qty"], ln["stock"]) for ln in q["lines"]]
    assert by == [(True, 10, None), (False, 3, 3), (False, 0, None), (True, 3, 3)]
    # available lines only: 2*19.90 + 9.90 = 49.70; fee = ceil(39.80*3%) + ceil(9.90*3%)
    assert q["subtotal"] == "49.70" and q["fee"] == "1.50" and q["total"] == "51.20"
    assert q["fee_rate"] == "3%" and q["lines"][0]["line_subtotal"] == "39.80"


async def test_checkout_happy_path_via_mock_gateway(env):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    r = await checkout(buyer, [line(manual, 1), line(card, 2)])
    assert r.status_code == 200, r.text
    body = r.json()
    co, orders = body["checkout"], body["orders"]
    assert co["checkout_no"].startswith("C") and len(co["checkout_no"]) == 13
    # (19.90 + 0.60) + (19.80 + 0.60 [ceil 0.594]) = 40.90
    assert co["amount"] == "40.90" and co["subtotal"] == "39.70" and co["fee"] == "1.20"
    assert sorted(co["order_nos"]) == sorted(o["order_no"] for o in orders)
    assert set(body["access_codes"]) == set(co["order_nos"])
    assert all(o["checkout_no"] == co["checkout_no"] for o in orders)
    # a member order is never paid on its own
    r = await buyer.post(f"/api/v1/orders/{orders[0]['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "pay_via_checkout"
    assert err["checkout_no"] == co["checkout_no"] == err["data"]["checkout_no"]
    # pay the checkout through the mock gateway (notify -> re-query -> settle)
    r = await buyer.post(
        f"/api/v1/checkouts/{co['checkout_no']}/pay", json={"channel": "zpay_alipay"}
    )
    assert r.status_code == 200 and "money=40.90" in r.json()["pay_url"]
    assert f"out_trade_no={co['checkout_no']}" in r.json()["pay_url"]
    resp = await simulate_gateway_payment(env, r.json()["pay_url"])
    assert resp.status_code == 302
    got = (await buyer.get(f"/api/v1/checkouts/{co['checkout_no']}")).json()
    assert got["status"] == "paid"
    for o in orders:
        v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
        assert v["status"] == "paid" and v["checkout_no"] == co["checkout_no"]
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM payments WHERE checkout_id IS NOT NULL") == 1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 2
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='Card'") == 2
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='surcharge_revenue'"
        )
        == -120
    )
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='sales_revenue'"
        )
        == -3970
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY "
            "txn_id HAVING sum(amount_minor) <> 0) x"
        )
        == 0
    )
    assert await env.drain() >= 1  # the auto_card member is delivered by the worker
    v = (await buyer.get(f"/api/v1/orders/{orders[1]['id']}")).json()
    assert v["status"] == "delivered"
    # paying again is refused
    r = await buyer.post(
        f"/api/v1/checkouts/{co['checkout_no']}/pay", json={"channel": "zpay_alipay"}
    )
    assert r.status_code == 409


async def test_checkout_wrong_amount_forged_and_requery_mismatch_never_fulfil(env, gw):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual), line(card)])).json()["checkout"]["checkout_no"]
    c = env.client(csrf=False)
    # tampered after signing -> bad signature
    forged = notify_params(no, "30.70", trade_no="TCO1")
    forged["money"] = "0.01"
    assert (await c.get(NOTIFY, params=forged)).text == "fail"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "pending_payment"
    # validly signed but the gateway re-query disagrees (other trade_no) -> manual review
    gw.paid[no] = ("30.70", "OTHER")
    good = notify_params(no, "30.70", trade_no="TCO1")
    assert (await c.get(NOTIFY, params=good)).text == "success"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox") == 0
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='manual_review'") == 2


async def test_checkout_signed_but_wrong_amount_goes_to_manual_review(env, gw):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual), line(card)])).json()["checkout"]["checkout_no"]
    gw.paid[no] = ("30.69", "TCO2")
    c = env.client(csrf=False)
    p = notify_params(no, "30.69", trade_no="TCO2")  # exact Decimal compare vs 30.70
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox") == 0
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='Card'") == 0


async def test_checkout_duplicate_notify_is_idempotent(env, gw):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual), line(card)])).json()["checkout"]["checkout_no"]
    gw.paid[no] = ("30.70", "TCO9")
    c = env.client(csrf=False)
    p = notify_params(no, "30.70", trade_no="TCO9")
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert (await c.get(NOTIFY, params=p)).text == "success"  # identical replay
    p2 = notify_params(no, "30.70", trade_no="TCO9", name="other")  # new signature, same trade
    assert (await c.get(NOTIFY, params=p2)).text == "success"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "paid"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox") == 2
    assert (
        await env.scalar("SELECT count(*) FROM ledger_entries WHERE memo LIKE 'zpay payment%'") == 6
    )
    # a different trade_no for the same paid checkout is flagged, not applied
    gw.paid[no] = ("30.70", "TCO10")
    assert (
        await c.get(NOTIFY, params=notify_params(no, "30.70", trade_no="TCO10"))
    ).text == "success"
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 1
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_notify_for_expired_checkout_is_late_manual_review_with_money_recorded(env, gw):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual), line(card)])).json()["checkout"]["checkout_no"]
    async with env.owner_session() as db:
        await db.execute(text("UPDATE checkouts SET expires_at = now() - interval '1 hour'"))
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '1 hour'"))
        await db.commit()
    assert await expire_pending(env.res.maker, env.res) == 2
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "expired"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='expired'") == 2
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409  # expired
    gw.paid[no] = ("30.70", "TLATE")
    c = env.client(csrf=False)
    assert (
        await c.get(NOTIFY, params=notify_params(no, "30.70", trade_no="TLATE"))
    ).text == "success"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='manual_review'") == 2
    assert await env.scalar("SELECT count(*) FROM payments") == 1  # money recorded
    assert await env.scalar("SELECT count(*) FROM outbox") == 0  # nothing fulfilled
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='Card'") == 0


async def test_checkout_validation_limits_and_stock(env):
    manual, card, buyer, _ = await three_way(env)
    assert (await checkout(buyer, [])).status_code == 422
    assert (await checkout(buyer, [line(manual)] * 21)).status_code == 422
    r = await checkout(buyer, [line(card, 4)])
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    assert r.json()["error"]["product_id"] == str(card.id)
    r = await checkout(buyer, [line(card, 2), line(card, 2)])  # duplicates are summed
    assert r.status_code == 409
    r = await checkout(buyer, [{"product_id": "00000000-0000-0000-0000-000000000000"}])
    assert r.status_code == 404
    assert await env.scalar("SELECT count(*) FROM checkouts") == 0


async def test_open_payables_cap_counts_checkouts_not_orders(env):
    manual, _card, buyer, _ = await three_way(env)  # regular buyer: cap 5
    for _i in range(5):
        r = await checkout(buyer, [line(manual), line(manual), line(manual)])
        assert r.status_code == 200, r.text  # 3 orders but 1 payable each
    r = await checkout(buyer, [line(manual)])
    assert r.status_code == 409
    r = (
        await buy(buyer, manual)
        if False
        else await buyer.post("/api/v1/orders", json={"product_id": str(manual.id), "qty": 1})
    )
    assert r.status_code == 409


async def test_cross_tenant_and_cross_buyer_checkout_access(env):
    manual, _card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual)])).json()["checkout"]["checkout_no"]
    other, _ = await env.login("buyer")
    assert (await other.get(f"/api/v1/checkouts/{no}")).status_code == 404
    assert (await other.post(f"/api/v1/checkouts/{no}/cancel", json={})).status_code == 404
    assert (
        await other.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    ).status_code == 404
    # another tenant's session (RLS-pinned) cannot see the row at all
    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT count(*) FROM checkouts")) == 0
        assert await db.scalar(text("SELECT count(*) FROM orders")) == 0
    async with env.api_session() as db:
        assert await db.scalar(text("SELECT count(*) FROM checkouts")) == 1
    # a buyer logged in on the other tenant cannot read it either
    ob, _u = await env.login("buyer", tenant_id=env.other_tenant_id)
    assert (await ob.get(f"/api/v1/checkouts/{no}")).status_code in (401, 404)


# ------------------------------------------------------------------ §3 cancel
async def test_buyer_cancel_member_order_cancels_whole_checkout(env):
    manual, card, buyer, _ = await three_way(env)
    body = (await checkout(buyer, [line(manual), line(card)])).json()
    o0 = body["orders"][0]
    r = await buyer.post(f"/api/v1/orders/{o0['id']}/cancel", json={"reason": "changed my mind"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["cancel_reason"] == "changed my mind"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='cancelled'") == 2
    assert (await buyer.get(f"/api/v1/checkouts/{body['checkout']['checkout_no']}")).json()[
        "status"
    ] == "cancelled"
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.cancelled'") == 2
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='checkout.cancelled'") == 1
    r = await buyer.post(f"/api/v1/orders/{o0['id']}/cancel", json={})
    assert r.status_code == 409  # only pending_payment


async def test_cancel_checkout_endpoint_and_system_message(env):
    manual, _card, buyer, _ = await three_way(env)
    await open_conversation(buyer)
    body = (await checkout(buyer, [line(manual)])).json()
    no = body["checkout"]["checkout_no"]
    r = await buyer.post(f"/api/v1/checkouts/{no}/cancel", json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    texts = await env.scalar(
        "SELECT string_agg(text, '|') FROM messages WHERE payload->>'event'='order_cancelled'"
    )
    assert texts == "Order cancelled by buyer"


async def test_cancel_after_paid_requery_settles_and_returns_already_paid(env, gw):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    body = (await checkout(buyer, [line(manual), line(card)])).json()
    no = body["checkout"]["checkout_no"]
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200  # a pay URL was issued
    gw.paid[no] = ("30.70", "TPAID1")  # ...and the buyer paid, but the notify never arrived
    r = await buyer.post(f"/api/v1/checkouts/{no}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "paid"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='paid'") == 2
    assert await env.scalar("SELECT count(*) FROM outbox") == 2


async def test_cancel_with_issued_pay_url_requeries_first(env, gw):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    gw.down = True
    r = await buyer.post(f"/api/v1/orders/{o['id']}/cancel", json={})
    assert r.status_code == 503  # query failed: retry later, nothing changed
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "pending_payment"
    gw.down = False
    calls = gw.calls
    r = await buyer.post(f"/api/v1/orders/{o['id']}/cancel", json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and gw.calls > calls
    # a later notify for the cancelled order is still recorded -> manual review
    gw.paid[o["order_no"]] = ("10.30", "TLATE2")
    c = env.client(csrf=False)
    assert (
        await c.get(NOTIFY, params=notify_params(o["order_no"], "10.30", trade_no="TLATE2"))
    ).text == "success"
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_staff_close_requires_reason_and_buyer_sees_it(env):
    manual, card, buyer, _ = await three_way(env)
    staff, _ = await env.login("agent")
    await open_conversation(buyer)
    body = (await checkout(buyer, [line(manual), line(card)])).json()
    oid = body["orders"][0]["id"]
    url = f"/api/v1/admin/orders/{oid}/cancel"
    assert (await staff.post(url, json={})).status_code == 422
    assert (await staff.post(url, json={"reason": ""})).status_code == 422
    r = await staff.post(url, json={"reason": "out of stock upstream"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='cancelled'") == 2
    assert (
        await env.scalar(
            "SELECT text FROM messages WHERE payload->>'event'='order_cancelled' LIMIT 1"
        )
        == "Order closed by support: out of stock upstream"
    )
    assert (await staff.post(url, json={"reason": "again"})).status_code == 409


async def test_admin_views_search_by_checkout_and_siblings(env):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    staff, _ = await env.login("agent")
    body = (await checkout(buyer, [line(manual), line(card)])).json()
    no = body["checkout"]["checkout_no"]
    r = await staff.get("/api/v1/admin/orders", params={"q": no[:8]})
    assert r.json()["total"] == 2
    row = r.json()["items"][0]
    assert row["checkout_no"] == no and row["fee_rate"] == "3%" and "subtotal" in row
    d = (await staff.get(f"/api/v1/admin/orders/{body['orders'][0]['id']}")).json()
    assert len(d["checkout_orders"]) == 2 and d["payment"] is None
    assert {"id", "order_no", "title", "amount", "status"} <= set(d["checkout_orders"][0])
    # the other buyer-facing view must not leak buyer_input to non-owners
    assert d["cancel_reason"] is None


async def test_expiry_expires_checkout_with_its_orders(env):
    manual, card, buyer, _ = await three_way(env)
    no = (await checkout(buyer, [line(manual), line(card)])).json()["checkout"]["checkout_no"]
    assert await expire_pending(env.res.maker) == 0
    async with env.owner_session() as db:
        await db.execute(text("UPDATE checkouts SET expires_at = now() - interval '1 minute'"))
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '1 minute'"))
        await db.commit()
    assert await expire_pending(env.res.maker) == 2
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "expired"
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='expired'") == 2


# ------------------------------------------------------------------ §4 buyer input
INPUT_CFG = {
    "enabled": True,
    "required": True,
    "multiline": True,
    "max_len": 50,
    "title_i18n": {"en": "", "zh-CN": ""},
    "prompt_i18n": {"en": "Open `https://chatgpt.com/api/auth/session`", "zh-CN": "打开…"},
    "placeholder_i18n": {"en": "Paste here", "zh-CN": "粘贴到这里"},
}


async def input_product(env, admin, **cfg):
    p = await env.product(mode="manual", price=1000, floor=100)
    r = await admin.patch(
        f"/api/v1/admin/products/{p.id}", json={"buyer_input": {**INPUT_CFG, **cfg}}
    )
    assert r.status_code == 200, r.text
    return p, r.json()


async def test_buyer_input_config_validation_and_public_resolution(env):
    admin, _ = await env.login("admin")
    p, view = await input_product(env, admin)
    assert view["buyer_input"]["max_len"] == 50 and view["buyer_input"]["title_i18n"]["en"] == ""
    for bad in ({"max_len": 0}, {"max_len": 4001}, {"prompt_i18n": {"en": "x" * 1001}}):
        r = await admin.patch(
            f"/api/v1/admin/products/{p.id}", json={"buyer_input": {**INPUT_CFG, **bad}}
        )
        assert r.status_code == 422, bad
    buyer, _ = await env.login("buyer")
    pub = (await buyer.get(f"/api/v1/products/{p.id}")).json()["buyer_input"]
    assert pub == {
        "enabled": True,
        "required": True,
        "multiline": True,
        "max_len": 50,
        "title": "Order information",  # fixed default when empty
        "prompt": "Open `https://chatgpt.com/api/auth/session`",
        "placeholder": "Paste here",
    }
    zh = (await buyer.get(f"/api/v1/products/{p.id}?lang=zh-CN")).json()["buyer_input"]
    assert zh["title"] == "下单信息" and zh["placeholder"] == "粘贴到这里"


async def test_product_lists_carry_buyer_input_for_quick_buy(env):
    """Issue 49 B: Home/Search quick buy opens BuySheet with a list summary; without the config
    it skipped the order-information form and the order failed with buyer_input_required."""
    admin, _ = await env.login("admin")
    p, _ = await input_product(env, admin, required=False)
    plain = await env.product(title="No input", mode="manual")
    buyer, _ = await env.login("buyer")
    listed = {i["id"]: i for i in (await buyer.get("/api/v1/products")).json()["items"]}
    assert listed[str(p.id)]["buyer_input"]["enabled"] is True
    assert listed[str(p.id)]["buyer_input"]["required"] is False
    assert listed[str(plain.id)]["buyer_input"] is None  # off is explicit, never missing
    by_ids = (await buyer.get(f"/api/v1/products?ids={p.id}")).json()["items"]
    assert by_ids[0]["buyer_input"]["placeholder"] == "Paste here"
    r = await admin.patch(f"/api/v1/admin/products/{p.id}", json={"buyer_input": None})
    assert r.json()["buyer_input"] is None
    assert (await buyer.get(f"/api/v1/products/{p.id}")).json()["buyer_input"] is None


async def test_buyer_input_required_max_len_and_never_audited(env, gw):
    await set_fee(env, 0)
    admin, _ = await env.login("admin")
    p, _v = await input_product(env, admin)
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(p.id)})
    assert r.status_code == 422 and r.json()["error"]["code"] == "buyer_input_required"
    r = await buyer.post("/api/v1/orders", json={"product_id": str(p.id), "buyer_input": "  \n "})
    assert r.json()["error"]["code"] == "buyer_input_required"
    r = await buyer.post("/api/v1/orders", json={"product_id": str(p.id), "buyer_input": "x" * 51})
    assert r.status_code == 422
    secret = "SESSION-TOKEN-abc123\x00\x07"
    o = await buy(buyer, p, buyer_input=f"  {secret}  ")
    assert o["buyer_input"] == "SESSION-TOKEN-abc123"  # trimmed, control chars stripped
    # checkout lines are validated the same way
    r = await checkout(buyer, [line(p)])
    assert r.json()["error"]["code"] == "buyer_input_required"
    # never in any audit row, event or log-like table
    for table in ("audit_log", "order_events"):
        n = await leaks(env, table, "abc123")
        assert n == 0, table
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE detail->>'buyer_input' = 'true'")
        == 1
    )
    # fix it while pending, then it is posted as a BUYER chat message after payment
    r = await buyer.patch(f"/api/v1/orders/{o['id']}/input", json={"buyer_input": "TOKEN-2"})
    assert r.status_code == 200 and r.json()["buyer_input"] == "TOKEN-2"
    r = await buyer.patch(f"/api/v1/orders/{o['id']}/input", json={"buyer_input": ""})
    assert r.status_code == 422
    assert await env.scalar("SELECT count(*) FROM messages WHERE sender_role='buyer'") == 0
    gw.paid[o["order_no"]] = ("10.00", "TIN1")
    c = env.client(csrf=False)
    assert (
        await c.get(NOTIFY, params=notify_params(o["order_no"], "10.00", trade_no="TIN1"))
    ).text == "success"
    assert (
        await env.scalar(
            "SELECT count(*) FROM messages WHERE sender_role='buyer' AND type='order_card'"
        )
        == 1
    )
    assert await leaks(env, "messages", "TOKEN-2") == 0
    # editable while paid (not yet delivered): CS gets an updated message
    r = await buyer.patch(f"/api/v1/orders/{o['id']}/input", json={"buyer_input": "TOKEN-3"})
    assert r.status_code == 200
    assert await env.scalar("SELECT count(*) FROM messages WHERE sender_role='buyer'") == 2
    for table in ("audit_log", "order_events"):
        n = await leaks(env, table, "TOKEN-")
        assert n == 0, table
    # owner and staff see it, other buyers do not
    staff, _ = await env.login("agent")
    d = (await staff.get(f"/api/v1/admin/orders/{o['id']}")).json()
    assert d["buyer_input"] == "TOKEN-3"
    other, _ = await env.login("buyer")
    assert (await other.get(f"/api/v1/orders/{o['id']}")).status_code == 404


async def test_buyer_input_message_for_checkout_orders_and_optional_field(env, gw):
    await set_fee(env, 0)
    admin, _ = await env.login("admin")
    p, _v = await input_product(env, admin)
    plain = await env.product(mode="manual", price=500, floor=100)
    buyer, _ = await env.login("buyer")
    body = (
        await checkout(buyer, [line(p, buyer_input="tok"), line(plain, buyer_input="ignored")])
    ).json()
    assert body["orders"][1]["buyer_input"] is None  # no popup on that product: dropped
    no = body["checkout"]["checkout_no"]
    gw.paid[no] = ("15.00", "TCI1")
    c = env.client(csrf=False)
    assert (
        await c.get(NOTIFY, params=notify_params(no, "15.00", trade_no="TCI1"))
    ).text == "success"
    assert await env.scalar("SELECT count(*) FROM messages WHERE sender_role='buyer'") == 1


# ------------------------------------------------------------------ §6 product cards
async def test_staff_product_card_payload_is_built_by_the_server(env):
    staff, _ = await env.login("agent")
    buyer, _ = await env.login("buyer", locale="zh-CN")
    conv = await open_conversation(buyer)
    p = await env.product(mode="manual", price=1990, title="Real title", zh="真标题")
    r = await staff.post(
        f"/api/v1/admin/conversations/{conv['id']}/messages",
        json={
            "client_msg_id": "pc-1",
            "type": "product_card",
            "product_id": str(p.id),
            "text": "FORGED",
            "payload": {"title": "FORGED"},
        },
    )
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["type"] == "product_card" and m["sender"]["role"] == "agent"
    assert m["payload"] == {
        "product_id": str(p.id),
        "title": "真标题",
        "cover": None,
        "price": "19.90",
        "currency": "CNY",
    }
    assert m["text"] is None
    draft = await env.product(mode="manual", status="draft")
    r = await staff.post(
        f"/api/v1/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "pc-2", "type": "product_card", "product_id": str(draft.id)},
    )
    assert r.status_code == 404
    other_t = await env.product(mode="manual", tenant_id=env.other_tenant_id)
    r = await staff.post(
        f"/api/v1/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "pc-3", "type": "product_card", "product_id": str(other_t.id)},
    )
    assert r.status_code == 404
    r = await staff.post(
        f"/api/v1/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "pc-4", "type": "product_card"},
    )
    assert r.status_code == 422
    # buyers cannot push product cards
    r = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "pc-5", "type": "product_card", "product_id": str(p.id)},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ §5 / §10 site
async def test_site_support_and_payment_settings_roundtrip(env):
    admin, _ = await env.login("admin")
    r = await admin.put(
        "/api/v1/admin/site",
        json={
            "payments": {"channels": {"zpay_alipay": {"enabled": True, "surcharge_permille": 25}}},
            "support": {
                "welcome_i18n": {"en": "Hi!", "zh-CN": "你好"},
                "quick_questions_i18n": {"en": ["Where is my order?", " "], "zh-CN": ["订单在哪"]},
            },
            "rate_limit": {"enabled": False, "limit": 200, "window_s": 30},
        },
    )
    assert r.status_code == 200, r.text
    got = (await admin.get("/api/v1/admin/site")).json()
    assert got["payments"]["channels"]["zpay_alipay"] == {"enabled": True, "surcharge_permille": 25}
    assert got["support"]["welcome_i18n"] == {"en": "Hi!", "zh-CN": "你好"}
    assert got["support"]["quick_questions_i18n"] == {
        "en": ["Where is my order?"],
        "zh-CN": ["订单在哪"],
    }
    assert got["rate_limit"] == {"enabled": False, "limit": 200, "window_s": 30}
    pub = (await admin.get("/api/v1/site")).json()
    assert pub["support"] == {"welcome": "Hi!", "quick_questions": ["Where is my order?"]}
    pz = (await admin.get("/api/v1/site?lang=zh-CN")).json()["support"]
    assert pz == {"welcome": "你好", "quick_questions": ["订单在哪"]}
    assert (await admin.get("/api/v1/site/payment-channels")).json()[0]["fee_rate"] == "2.5%"
    for bad in (
        {"payments": {"channels": {"zpay_alipay": {"surcharge_permille": 201}}}},
        {"payments": {"channels": {"stripe": {"enabled": True}}}},
        {"support": {"quick_questions_i18n": {"en": ["q"] * 7}}},
        {"support": {"quick_questions_i18n": {"en": ["x" * 61]}}},
        {"rate_limit": {"limit": 9}},
        {"rate_limit": {"window_s": 3601}},
    ):
        assert (await admin.put("/api/v1/admin/site", json=bad)).status_code == 422, bad
    # disabling the channel hides it and blocks new orders
    await admin.put(
        "/api/v1/admin/site",
        json={"payments": {"channels": {"zpay_alipay": {"enabled": False}}}},
    )
    assert (await admin.get("/api/v1/site/payment-channels")).json() == []
    p = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    assert (await buyer.post("/api/v1/orders", json={"product_id": str(p.id)})).status_code == 503


async def test_global_rate_limit_off_on_exempt_exact_body_and_fail_open(env, monkeypatch):
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    admin, _ = await env.login("admin")
    c = env.client()
    # default OFF: nothing is limited
    for _i in range(15):
        assert (await c.get("/api/v1/categories")).status_code == 200
    r = await admin.put(
        "/api/v1/admin/site", json={"rate_limit": {"enabled": True, "limit": 10, "window_s": 60}}
    )
    assert r.status_code == 200
    assert json.loads(await env.res.redis.get(f"cfg:ratelimit:{env.tenant_id}")) == {
        "enabled": True,
        "limit": 10,
        "window_s": 60,
        "staff_ips": [],  # v5.2: staff IP allowlist mirrored with the limiter config
    }
    codes = [(await c.get("/api/v1/categories")).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
    r = await c.get("/api/v1/categories")
    assert r.status_code == 429
    assert r.content == b'{"code":429,"msg":"Access is too frequent. Please try again later."}'
    assert r.headers["content-type"] == "application/json" and int(r.headers["retry-after"]) >= 1
    # exempt: health, notify, realtime, admin, non-/api
    for _i in range(15):
        assert (await c.get("/healthz")).status_code == 200
        assert (await admin.get("/api/v1/admin/stats")).status_code == 200
        assert (await c.get(NOTIFY, params={"x": "1"})).status_code == 200
    # a different client IP has its own window
    other = env.client()
    assert (
        await other.get("/api/v1/categories", headers={"x-forwarded-for": "9.9.9.9"})
    ).status_code == 200

    # fail open: Redis errors never block traffic
    async def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(env.res.redis, "get", boom)
    assert (await c.get("/api/v1/categories")).status_code == 200
    # switching it off again lifts the limit
    monkeypatch.undo()
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    await admin.put("/api/v1/admin/site", json={"rate_limit": {"enabled": False}})
    assert (await c.get("/api/v1/categories")).status_code == 200


# ------------------------------------------------------------------ §8 markdown
async def test_description_markdown_is_sanitised_and_backfilled(env):
    admin, _ = await env.login("admin")
    up = await admin.post(
        "/api/v1/admin/content/images", files={"file": ("a.png", png_bytes(), "image/png")}
    )
    assert up.status_code == 200, up.text
    img = up.json()
    assert img["url"] == f"/api/v1/files/{img['id']}"
    md = f"**bold**\n\n![ok]({img['url']})\n\n![evil](https://evil.example/x.png)\n\n<script>alert(1)</script>"
    r = await admin.post(
        "/api/v1/admin/products",
        json={
            "title_i18n": {"en": "P"},
            "description_i18n": {"en": md},
            "price": "1.00",
            "delivery_mode": "manual",
            "category": "gift_cards",
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    buyer, _ = await env.login("buyer")
    v = (await buyer.get(f"/api/v1/products/{pid}")).json()
    html = v["description_html"]
    assert "<strong>bold</strong>" in html and img["url"] in html
    assert "evil.example" not in html and "<script" not in html
    assert v["description"] == md
    # idempotent backfill renders rows written before v4
    async with env.owner_session() as db:
        await db.execute(text("UPDATE products SET description_html_i18n = '{}'::jsonb"))
        await db.commit()
    from app.devtools.seed import backfill_description_html

    async with env.api_session() as db:
        assert await backfill_description_html(db) == 1
    async with env.api_session() as db:
        assert await backfill_description_html(db) == 0
    assert "<strong>bold</strong>" in await env.scalar(
        "SELECT description_html_i18n->>'en' FROM products"
    )
    # staff-only; hostile bytes are rejected
    assert (
        await buyer.post(
            "/api/v1/admin/content/images", files={"file": ("a.png", png_bytes(), "image/png")}
        )
    ).status_code == 403
    bad = await admin.post(
        "/api/v1/admin/content/images", files={"file": ("a.png", b"<script>", "image/png")}
    )
    assert bad.status_code == 422
