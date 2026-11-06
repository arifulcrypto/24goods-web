"""Review findings on commerce v4 (payment audit F1/F2/W*, security M1/M2/L3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import text

from app.core import globallimit
from app.modules import jobs
from app.modules.payments.settle import exact_minor
from tests.helpers import buy, notify_params
from tests.test_commerce_v4 import (
    INPUT_CFG,
    NOTIFY,
    checkout,
    line,
    set_fee,
    set_settings,
    three_way,
)

BALANCED = (
    "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
    "HAVING sum(amount_minor) <> 0) x"
)


async def new_checkout(env, price_a=1990, price_b=990):
    await set_fee(env, 30)
    manual, card, buyer, _ = await three_way(env)
    body = (await checkout(buyer, [line(manual), line(card)])).json()
    return manual, card, buyer, body, body["checkout"]["checkout_no"]


# ------------------------------------------------------------------ F1
async def test_checkout_notify_with_wrong_pid_is_rejected(env, gw):
    _m, _c, buyer, _b, no = await new_checkout(env)
    gw.paid[no] = ("30.70", "TPID")
    p = notify_params(no, "30.70", trade_no="TPID", pid="9999")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "fail"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "pending_payment"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE severity='critical'") == 1


async def test_concurrent_duplicate_checkout_notify_settles_once(env, gw):
    _m, _c, buyer, _b, no = await new_checkout(env)
    gw.paid[no] = ("30.70", "TCONC")
    p = notify_params(no, "30.70", trade_no="TCONC")
    clients = [env.client(csrf=False) for _ in range(5)]
    rs = await asyncio.gather(*(c.get(NOTIFY, params=p) for c in clients))
    assert {r.text for r in rs} <= {"success", "fail"} and any(r.text == "success" for r in rs)
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "paid"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 2
    assert await env.scalar("SELECT count(DISTINCT key) FROM outbox") == 2
    assert await env.scalar(BALANCED) == 0


async def test_notify_for_a_member_order_no_is_recorded_and_held(env, gw):
    _m, _c, buyer, body, _no = await new_checkout(env)
    member = body["orders"][0]  # 19.90 + 0.60 = 20.50
    gw.paid[member["order_no"]] = ("20.50", "TMEM")
    p = notify_params(member["order_no"], "20.50", trade_no="TMEM")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "success"
    v = (await buyer.get(f"/api/v1/orders/{member['id']}")).json()
    assert v["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments WHERE order_id IS NOT NULL") == 1
    assert await env.scalar("SELECT count(*) FROM outbox") == 0  # nothing fulfilled
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE severity='critical' "
            "AND detail->>'reason' = 'member_order_paid_directly'"
        )
        >= 1
    )
    assert await env.scalar(BALANCED) == 0
    # the same notify again is a benign duplicate: still one Payment
    p2 = notify_params(member["order_no"], "20.50", trade_no="TMEM", name="x")
    assert (await env.client(csrf=False).get(NOTIFY, params=p2)).text == "success"
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_cancel_racing_a_notify_never_loses_the_payment(env, gw):
    _m, _c, buyer, _b, no = await new_checkout(env)
    await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    gw.paid[no] = ("30.70", "TRACE")
    p = notify_params(no, "30.70", trade_no="TRACE")
    cancel, notify = await asyncio.gather(
        buyer.post(f"/api/v1/checkouts/{no}/cancel", json={}),
        env.client(csrf=False).get(NOTIFY, params=p),
    )
    assert cancel.status_code == 409  # already_paid (re-query) or invalid_state (notify won)
    assert notify.text == "success"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "paid"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 2
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='cancelled'") == 0
    assert await env.scalar(BALANCED) == 0


async def test_refund_of_one_member_reverses_only_its_lines(env, gw):
    await set_fee(env, 30)
    a = await env.product(mode="manual", price=1990, floor=100, title="A")
    b = await env.product(mode="manual", price=990, floor=100, title="B")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    body = (await checkout(buyer, [line(a), line(b)])).json()
    no = body["checkout"]["checkout_no"]
    gw.paid[no] = ("30.70", "TREF")
    p = notify_params(no, "30.70", trade_no="TREF")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "success"
    oa, ob = body["orders"]
    r = await staff.post(f"/api/v1/admin/orders/{oa['id']}/deliver", json={"text": "done"})
    assert r.status_code == 200
    t = await buyer.post(
        f"/api/v1/orders/{oa['id']}/after-sales",
        json={"reason": "invalid_code", "description": "x", "attachment_ids": []},
    )
    assert t.status_code == 200, t.text
    r = await admin.post(
        f"/api/v1/admin/after-sales/{t.json()['id']}/action", json={"action": "refund"}
    )
    assert r.status_code == 200
    sums = {
        (oid, acct): amt
        for oid, acct, amt in [
            tuple(x)
            for x in await _rows(
                env,
                "SELECT order_id::text, account, sum(amount_minor) FROM ledger_entries "
                "WHERE account IN ('sales_revenue','surcharge_revenue') GROUP BY 1,2",
            )
        ]
    }
    assert sums[(oa["id"], "sales_revenue")] == 0 and sums[(oa["id"], "surcharge_revenue")] == 0
    assert sums[(ob["id"], "sales_revenue")] == -990
    assert sums[(ob["id"], "surcharge_revenue")] == -30  # ceil(0.297) = 0.30
    assert await env.scalar(BALANCED) == 0
    assert (await buyer.get(f"/api/v1/orders/{ob['id']}")).json()["status"] == "paid"
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "paid"


async def _rows(env, sql: str):
    async with env.owner_session() as db:
        return (await db.execute(text(sql))).all()


async def test_checkout_with_one_member_already_expired(env, gw):
    _m, _c, buyer, body, no = await new_checkout(env)
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET status='expired' WHERE id = :i"), {"i": body["orders"][0]["id"]}
        )
        await db.commit()
    gw.paid[no] = ("30.70", "TMIX")
    p = notify_params(no, "30.70", trade_no="TMIX")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "success"
    st = {
        o["id"]: (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"]
        for o in body["orders"]
    }
    assert st[body["orders"][0]["id"]] == "manual_review"  # late member: held, money recorded
    assert st[body["orders"][1]["id"]] == "paid"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 1
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='Manual'") == 0
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='Card'") == 1
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar(BALANCED) == 0


async def test_sold_is_aggregated_per_product_for_duplicate_lines(env, gw):
    await set_fee(env, 0)
    a = await env.product(mode="manual", price=500, floor=100, title="A")
    buyer, _ = await env.login("buyer")
    no = (await checkout(buyer, [line(a, 2), line(a, 3)])).json()["checkout"]["checkout_no"]
    gw.paid[no] = ("25.00", "TSOLD")
    p = notify_params(no, "25.00", trade_no="TSOLD")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "success"
    assert await env.scalar("SELECT sold FROM products WHERE title_i18n->>'en'='A'") == 5


async def test_f2_review_without_payment_is_upgraded_by_a_later_verified_notify(env, gw):
    _m, _c, buyer, _b, no = await new_checkout(env)
    c = env.client(csrf=False)
    gw.paid[no] = ("30.70", "OTHER")  # re-query disagrees with the notify below
    assert (await c.get(NOTIFY, params=notify_params(no, "30.70", trade_no="TF2A"))).text == (
        "success"
    )
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    gw.paid[no] = ("30.70", "TF2B")  # now the gateway confirms a payment
    assert (await c.get(NOTIFY, params=notify_params(no, "30.70", trade_no="TF2B"))).text == (
        "success"
    )
    assert (await buyer.get(f"/api/v1/checkouts/{no}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments WHERE checkout_id IS NOT NULL") == 1
    assert await env.scalar("SELECT count(*) FROM outbox") == 0  # still not fulfilled
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 0
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE "
            "account='gateway_clearing:zpay' AND memo LIKE 'zpay payment%'"
        )
        == 3070
    )
    assert await env.scalar(BALANCED) == 0
    # a third notify is a plain duplicate now
    gw.paid[no] = ("30.70", "TF2B")
    await c.get(NOTIFY, params=notify_params(no, "30.70", trade_no="TF2B", name="y"))
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_f2_single_order_review_without_payment_is_upgraded(env, gw):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    c = env.client(csrf=False)
    gw.paid[o["order_no"]] = ("10.30", "OTHER")
    await c.get(NOTIFY, params=notify_params(o["order_no"], "10.30", trade_no="TS1"))
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    gw.paid[o["order_no"]] = ("10.30", "TS2")
    await c.get(NOTIFY, params=notify_params(o["order_no"], "10.30", trade_no="TS2"))
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "manual_review"
    assert await env.scalar(BALANCED) == 0


# ------------------------------------------------------------------ W2 / W3
async def test_cancel_requeries_even_when_no_pay_url_was_issued(env, gw):
    await set_fee(env, 0)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)  # no /pay call: channel is NULL
    gw.paid[o["order_no"]] = ("10.00", "TNOURL")
    r = await buyer.post(f"/api/v1/orders/{o['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid"
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "paid"
    o2 = await buy(buyer, p)  # not found at the gateway: the cancel proceeds
    r = await buyer.post(f"/api/v1/orders/{o2['id']}/cancel", json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert gw.calls >= 2


async def test_cancel_refuses_to_act_on_a_malformed_paid_answer(env, gw):
    await set_fee(env, 0)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    for money, trade in (("10.001", "T1"), ("10.00", "")):
        o = await buy(buyer, p)
        gw.paid[o["order_no"]] = (money, trade)
        r = await buyer.post(f"/api/v1/orders/{o['id']}/cancel", json={})
        assert r.status_code == 503, (money, trade)
        assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "pending_payment"


def test_exact_minor_rejects_sub_fen_amounts():
    assert exact_minor(Decimal("10.30")) == 1030 and exact_minor(Decimal("10")) == 1000
    assert exact_minor(Decimal("10.001")) is None and exact_minor(Decimal("NaN")) is None
    assert exact_minor(Decimal("1E+1")) == 1000


# ------------------------------------------------------------------ W5
async def test_fee_is_recomputed_at_pay_time_until_a_url_is_issued(env):
    await set_fee(env, 30)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await set_fee(env, 50)  # config changed before any pay URL
    r = await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and "money=10.50" in r.json()["pay_url"]
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert (v["fee"], v["amount"], v["fee_rate"]) == ("0.50", "10.50", "5%")
    # a URL is out now: a further change is a 409
    await set_fee(env, 20)
    r = await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "fee_channel_mismatch"
    # pre-v4 pending order (fee 0): picks up the current rate on its first pay
    o2 = await buy(buyer, p)
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE orders SET fee_minor=0, fee_permille=0, amount_minor=subtotal_minor "
                "WHERE id=:i"
            ),
            {"i": o2["id"]},
        )
        await db.commit()
    r = await buyer.post(f"/api/v1/orders/{o2['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and "money=10.20" in r.json()["pay_url"]


async def test_checkout_fee_is_recomputed_at_pay_time(env):
    _m, _c, buyer, _body, no = await new_checkout(env)  # 30.70 at 3%
    await set_fee(env, 50)  # 19.90*5% = 0.995 -> 1.00 ; 9.90*5% = 0.495 -> 0.50
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and "money=31.30" in r.json()["pay_url"]
    co = (await buyer.get(f"/api/v1/checkouts/{no}")).json()
    assert co["amount"] == "31.30" and co["fee"] == "1.50" and co["fee_rate"] == "5%"
    assert await env.scalar("SELECT sum(amount_minor) FROM orders") == 3130
    await set_fee(env, 10)
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "fee_channel_mismatch"


# ------------------------------------------------------------------ M1
async def test_buyer_input_is_dropped_with_the_account_and_after_30_days(env):
    await set_fee(env, 0)
    admin, _ = await env.login("admin")
    p = await env.product(mode="manual", price=1000, floor=100)
    r = await admin.patch(
        f"/api/v1/admin/products/{p.id}", json={"buyer_input": {**INPUT_CFG, "max_len": 100}}
    )
    assert r.status_code == 200
    buyer, _u = await env.login("buyer")
    keep = await buy(buyer, p, buyer_input="token-keep")
    gone = await buy(buyer, p, buyer_input="token-gone")
    old_done = await buy(buyer, p, buyer_input="token-old-done")
    old_paid = await buy(buyer, p, buyer_input="token-old-paid")
    old = datetime.now(timezone.utc) - timedelta(days=40)
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE orders SET status='completed', completed_at=:d, updated_at=:d WHERE id=:i"
            ),
            {"d": old, "i": old_done["id"]},
        )
        await db.execute(
            text("UPDATE orders SET status='paid', updated_at=:d WHERE id=:i"),
            {"d": old, "i": old_paid["id"]},  # open order: never purged by age
        )
        await db.execute(
            text("UPDATE orders SET status='cancelled' WHERE id IN (:a, :b)"),
            {"a": gone["id"], "b": keep["id"]},
        )
        await db.commit()
    assert await jobs.purge_buyer_input(env.res.maker) == 1  # only the 40-day-old finished one
    assert await jobs.purge_buyer_input(env.res.maker) == 0  # idempotent
    vals = dict(await _rows(env, "SELECT id::text, buyer_input FROM orders"))
    assert vals[old_done["id"]] is None and vals[old_paid["id"]] == "token-old-paid"
    assert vals[keep["id"]] == "token-keep"
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET status='refunded', updated_at = now() WHERE id=:i"),
            {"i": old_paid["id"]},
        )
        await db.commit()
    r = await buyer.post(
        "/api/v1/me/delete", json={"password": "correct horse battery", "confirm": "DELETE"}
    )
    assert r.status_code == 204, r.text
    assert await env.scalar("SELECT count(*) FROM orders WHERE buyer_input IS NOT NULL") == 0


# ------------------------------------------------------------------ M2 / L3
async def test_rate_limit_config_is_reloaded_from_the_db_on_a_redis_miss(env, monkeypatch):
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    await set_settings(env, {"rate_limit": {"enabled": True, "limit": 10, "window_s": 60}})
    assert await env.res.redis.get(f"cfg:ratelimit:{env.tenant_id}") is None  # nothing mirrored
    c = env.client()
    codes = [(await c.get("/api/v1/categories")).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert await env.res.redis.get(f"cfg:ratelimit:{env.tenant_id}") is not None  # re-mirrored


async def test_rate_limit_is_skipped_when_the_client_ip_is_unknown(env, monkeypatch):
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    await set_settings(env, {"rate_limit": {"enabled": True, "limit": 10, "window_s": 60}})
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, client=None),  # type: ignore[arg-type]
        base_url="http://testserver",
    )
    env.clients.append(c)
    codes = [(await c.get("/api/v1/categories")).status_code for _ in range(15)]
    assert codes == [200] * 15
