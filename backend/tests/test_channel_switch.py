"""Issue 23 A/B/C/H: one fee rule, per-channel totals from the server, switching channel while
unpaid (old-amount notify -> manual review, no double fee)."""

from __future__ import annotations

from app.modules.payments.channels import lines_totals
from tests.helpers import buy, notify_params
from tests.test_commerce_v4 import NOTIFY, checkout
from tests.test_usdt_v52 import mock, set_channels  # noqa: F401  (fixture re-export)

PAY = "/api/v1/orders/{}/pay"


def test_per_line_ceiling_5_x_1_01_at_3_percent():
    # 5 lines of 1.01 @3%: 5 * ceil(0.0303) = 0.20 (NOT ceil(5.05*3%) = 0.16)
    assert lines_totals(30, [(101, 0)] * 5) == (20, 525)
    assert lines_totals(0, [(101, 0)] * 5) == (0, 505)


async def _two_channels(env, alipay=30, usdt=0):
    await set_channels(
        env,
        {
            "zpay_alipay": {"surcharge_permille": alipay},
            "usdt_trc20": {"enabled": True, "surcharge_permille": usdt},
        },
    )


async def test_cart_quote_and_checkout_agree_per_channel(env):
    await _two_channels(env)
    p = await env.product(mode="manual", price=101, floor=10)
    buyer, _ = await env.login("buyer")
    items = [{"product_id": str(p.id), "qty": 1}] * 5
    q = (await buyer.post("/api/v1/cart/quote", json={"items": items})).json()
    assert q["fee"] == "0.20" and q["total"] == "5.25"
    assert q["channel_totals"]["zpay_alipay"]["amount"] == "5.25"
    assert q["channel_totals"]["usdt_trc20"] == {"fee": "0.00", "fee_rate": "0%", "amount": "5.05"}
    q2 = (
        await buyer.post("/api/v1/cart/quote", json={"items": items, "channel": "usdt_trc20"})
    ).json()
    assert q2["total"] == "5.05" and q2["channel"] == "usdt_trc20"
    # H: checkout created with the selected channel's fee
    r = await checkout(buyer, items, channel="usdt_trc20")
    assert r.status_code == 200, r.text
    assert r.json()["checkout"]["amount"] == "5.05"
    r = await checkout(buyer, items)
    assert r.json()["checkout"]["amount"] == q["total"]


async def test_order_view_exposes_server_channel_totals(env):
    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    ct = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["channel_totals"]
    assert ct["zpay_alipay"]["amount"] == "10.30" and ct["usdt_trc20"]["amount"] == "10.00"
    assert ct["usdt_trc20"]["fee"] == "0.00"


async def test_switch_alipay_usdt_alipay_no_double_fee_and_old_notify_to_review(env, gw, mock):  # noqa: F811
    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    assert o["amount"] == "10.30"
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})
    assert "money=10.30" in r.json()["pay_url"]
    # -> USDT (0%): fee removed server side, version bumped, buyer told, audited
    v0 = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["version"]
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})
    assert r.status_code == 200, r.text
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert (v["fee"], v["amount"], v["fee_rate"], v["channel"]) == (
        "0.00",
        "10.00",
        "0%",
        "usdt_trc20",
    )
    assert v["version"] == v0 + 1
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.fee_recomputed'") == 1
    )
    # -> back to Alipay: 3% applied once, not stacked
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})
    assert "money=10.30" in r.json()["pay_url"]
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert (v["fee"], v["amount"]) == ("0.30", "10.30")
    # -> USDT again, then a late Alipay payment of the OLD 10.30 is held for review
    await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})
    gw.paid[o["order_no"]] = ("10.30", "TLATE")
    await env.client(csrf=False).get(
        NOTIFY, params=notify_params(o["order_no"], "10.30", trade_no="TLATE")
    )
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert v["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0


async def test_switch_checkout_channel_and_refuse_after_payment(env, gw):
    await _two_channels(env)
    p = await env.product(mode="manual", price=101, floor=10)
    buyer, _ = await env.login("buyer")
    r = await checkout(buyer, [{"product_id": str(p.id), "qty": 1}] * 2)
    co = r.json()["checkout"]
    no = co["checkout_no"]
    assert co["amount"] == "2.10" and co["channel_totals"]["usdt_trc20"]["amount"] == "2.02"
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert "money=2.10" in r.json()["pay_url"]
    # money arrives for the old amount -> manual review; the checkout is no longer payable
    gw.paid[no] = ("2.10", "TCO")
    await env.client(csrf=False).get(NOTIFY, params=notify_params(no, "2.10", trade_no="TCO"))
    r = await buyer.post(f"/api/v1/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409


async def test_specific_codes_for_unpaid_cap_and_long_buyer_input(env):
    from app.modules.common.buyerinput import clean_value

    assert clean_value("​ abc﻿　 ") == "abc"  # zero-width + full-width space
    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    for _i in range(5):
        await buy(buyer, p)
    r = await buyer.post("/api/v1/orders", json={"product_id": str(p.id), "qty": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_many_unpaid"
    r = await buyer.post(
        "/api/v1/orders", json={"product_id": str(p.id), "qty": 1, "buyer_input": "x" * 4001}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "buyer_input_too_long"


# ------------------------------------------------------------------ payment-auditor round 2
BALANCED = (
    "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
    "HAVING sum(amount_minor) <> 0) x"
)


async def _zpay_notify(env, gw, no, money, trade):
    gw.paid[no] = (money, trade)
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(no, money, trade_no=trade))
    return r.text


async def test_old_zpay_url_paid_after_switch_is_recorded_and_held(env, gw):
    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})  # URL for 10.30
    await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})  # now 10.00
    assert await _zpay_notify(env, gw, o["order_no"], "10.30", "TOLD") == "success"
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert v["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT amount_minor FROM payments") == 1030  # what was paid
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
    assert await env.scalar(BALANCED) == 0
    # duplicate / retried notify is idempotent
    assert await _zpay_notify(env, gw, o["order_no"], "10.30", "TOLD") == "success"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    # a forged amount the gateway does not confirm is NOT recorded
    o2 = await buy(buyer, p)
    gw.paid[o2["order_no"]] = ("10.30", "OTHER")
    await env.client(csrf=False).get(
        NOTIFY, params=notify_params(o2["order_no"], "9.99", trade_no="TFORGED")
    )
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_old_usdt_trade_paid_after_switch_is_recorded_and_held(env, mock):  # noqa: F811
    from tests.test_usdt_v52 import _post

    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})  # trade for 10.00
    assert r.status_code == 200, r.text
    keys = await env.s.redis.keys("mockpay:usdt:*-*")
    trade_id = next(k.decode().split(":")[-1] for k in keys if b":o:" not in k)
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})  # now 10.30
    assert r.status_code == 200
    await mock.patch_trade(trade_id, status=2)  # the buyer paid the OLD trade on chain
    body = await mock.body(trade_id)
    await _post(env, body)
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert v["status"] == "manual_review" and v["amount"] == "10.30"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT amount_minor FROM payments") == 1000
    assert await env.scalar("SELECT channel FROM payments") == "usdt_trc20"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
    assert await env.scalar(BALANCED) == 0
    await _post(env, body)  # duplicate
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_concurrent_switch_vs_inflight_notify_never_double_records(env, gw, mock):  # noqa: F811
    import asyncio

    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})
    gw.paid[o["order_no"]] = ("10.30", "TRACE")
    sw, nt = await asyncio.gather(
        buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"}),
        env.client(csrf=False).get(
            NOTIFY, params=notify_params(o["order_no"], "10.30", trade_no="TRACE")
        ),
    )
    assert sw.status_code in (200, 409) and nt.text in ("success", "fail")
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
    assert v["status"] in ("paid", "manual_review")
    if v["status"] == "paid":  # the notify won the lock while the amount still matched
        assert await env.scalar("SELECT amount_minor FROM payments") == 1030
        assert v["amount"] == "10.30"
    else:  # the switch won: the 10.30 payment is recorded but held
        assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
    assert await env.scalar(BALANCED) == 0


async def test_disabled_channel_pay_checkout_and_quote(env):
    await set_channels(env, {"zpay_alipay": {"surcharge_permille": 30}})  # usdt stays off
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    r = await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})
    assert r.status_code == 503
    items = [{"product_id": str(p.id), "qty": 1}]
    r = await checkout(buyer, items, channel="usdt_trc20")
    assert r.status_code == 503 and r.json()["error"]["code"] == "channel_unavailable"
    q = (
        await buyer.post("/api/v1/cart/quote", json={"items": items, "channel": "usdt_trc20"})
    ).json()
    assert q["channel"] == "zpay_alipay" and q["channel_fallback"] is True


async def test_channel_change_with_same_fee_is_audited(env):
    await _two_channels(env, alipay=0, usdt=0)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})
    await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.channel_changed'") == 1
    )


async def test_promo_and_markup_orders_switched_then_paid_balance(env, gw, mock):  # noqa: F811
    from tests.test_commerce_v4 import set_settings
    from tests.test_commerce_v5 import FEATURES, make_promo, make_reseller

    await _two_channels(env)
    await set_settings(env, FEATURES)
    admin, _ = await env.login("admin")
    await make_promo(admin)  # 10%
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    _rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=300)
    promo = await buy(buyer, p, promo_code="SAVE10")
    store = (
        await buyer.post(
            "/api/v1/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "shopa"}
        )
    ).json()
    for o in (promo, store):
        for ch in ("zpay_alipay", "usdt_trc20", "zpay_alipay"):
            assert (await buyer.post(PAY.format(o["id"]), json={"channel": ch})).status_code == 200
        v = (await buyer.get(f"/api/v1/orders/{o['id']}")).json()
        assert v["amount"] == o["amount"] and v["fee"] == o["fee"]  # no double fee
        assert (
            await _zpay_notify(env, gw, o["order_no"], v["amount"], "T" + o["order_no"][:8])
            == "success"
        )
        assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "paid"
    assert await env.scalar("SELECT count(*) FROM payments") == 2
    assert await env.scalar(BALANCED) == 0


async def test_second_paid_trade_is_critical_held_and_alerted(env, gw, monkeypatch):
    from app.core import notify_tg

    calls: list[tuple] = []
    monkeypatch.setattr(
        notify_tg,
        "order_event",
        lambda res, o, ev, extra=None, once_suffix="": calls.append((ev, extra)),
    )
    await _two_channels(env)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    o = await buy(buyer, p)
    await buyer.post(PAY.format(o["id"]), json={"channel": "zpay_alipay"})  # old URL: 10.30
    await buyer.post(PAY.format(o["id"]), json={"channel": "usdt_trc20"})  # new amount 10.00
    assert await _zpay_notify(env, gw, o["order_no"], "10.30", "TFIRST") == "success"  # held
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await _zpay_notify(env, gw, o["order_no"], "10.30", "TSECOND") == "success"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    d = await env.scalar(
        "SELECT detail::text FROM audit_log "
        "WHERE action='payment.duplicate' AND severity='critical'"
    )
    assert "TSECOND" in d and "1030" in d and "TFIRST" in d
    assert (await buyer.get(f"/api/v1/orders/{o['id']}")).json()["status"] == "manual_review"
    assert any(ev == "payment_review" and "TSECOND" in str(x) for ev, x in calls)
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
