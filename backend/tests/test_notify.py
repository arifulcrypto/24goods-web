"""Payment notify pipeline: forged/tampered/replayed/concurrent notifies."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.payments import epay
from tests.conftest import ZPAY_KEY
from tests.helpers import notify_params

NOTIFY = "/api/v1/pay/notify/zpay"


class GatewayStub:
    """Server-to-server re-query responses (``api.php?act=order``)."""

    def __init__(self) -> None:
        self.calls = 0
        self.mode = "paid"
        self.money = "19.90"
        self.trade_no = "T202611050001"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert request.url.path == "/api.php"
        assert request.url.params["act"] == "order"
        assert request.url.params["key"] == ZPAY_KEY
        if self.mode == "down":
            raise httpx.ConnectError("boom", request=request)
        status = 1 if self.mode == "paid" else 0
        body = {
            "code": "1",
            "msg": "succ",
            "trade_no": self.trade_no,
            "out_trade_no": request.url.params["out_trade_no"],
            "money": self.money,
            "status": str(status),
        }
        return httpx.Response(200, text=json.dumps(body))


@pytest.fixture
async def gw(env):
    stub = GatewayStub()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handler))
    env.res.zpay._http = client
    yield stub
    await client.aclose()


async def _order(env, price=1990, mode="manual"):
    product = await env.product(mode=mode, price=price, floor=100)
    buyer, _user = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 200, r.text
    return buyer, r.json()


async def _status(buyer, order) -> str:
    return (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"]


async def _counts(env) -> tuple[int, int, int]:
    return (
        await env.scalar("SELECT count(*) FROM payments"),
        await env.scalar("SELECT count(DISTINCT txn_id) FROM ledger_entries"),
        await env.scalar("SELECT count(*) FROM outbox"),
    )


async def test_valid_notify_get_and_ledger(env, gw):
    buyer, order = await _order(env)
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.status_code == 200 and r.text == "success"
    assert r.headers["content-type"].startswith("text/plain")
    assert await _status(buyer, order) == "paid"
    assert await _counts(env) == (1, 2, 1)
    assert gw.calls == 1
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='gateway_clearing:zpay'"
        )
        == 1990 - 32
    )


async def test_valid_notify_post_form_without_csrf_header(env, gw):
    buyer, order = await _order(env)
    r = await env.client(csrf=False).post(NOTIFY, data=notify_params(order["order_no"], "19.90"))
    assert r.status_code == 200 and r.text == "success"
    assert await _status(buyer, order) == "paid"


async def test_forged_signature(env, gw):
    buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    p["sign"] = "0" * 32
    r = await env.client(csrf=False).get(NOTIFY, params=p)
    assert r.text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    assert gw.calls == 0
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.notify_bad_signature' "
            "AND severity='critical'"
        )
        == 1
    )


async def test_tampered_amount_after_signing(env, gw):
    buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    p["money"] = "0.01"
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "fail"
    assert await _status(buyer, order) == "pending_payment"


async def test_signed_wrong_amount_goes_to_manual_review(env, gw):
    buyer, order = await _order(env)
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "0.01"))
    assert r.text == "success"  # stop gateway retries
    assert await _status(buyer, order) == "manual_review"
    assert await _counts(env) == (0, 0, 0)
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.manual_review'") == 1
    )


async def test_wrong_pid(env, gw):
    buyer, order = await _order(env)
    r = await env.client(csrf=False).get(
        NOTIFY, params=notify_params(order["order_no"], "19.90", pid="9999")
    )
    assert r.text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.notify_rejected'")
        == 1
    )


async def test_sign_type_downgrade_rejected(env, gw):
    _buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    p["sign_type"] = "RSA"
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "fail"


async def test_unknown_order(env, gw):
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params("GAAAAAAAAAAAA", "1.00"))
    assert r.text == "fail"


async def test_not_paid_status_is_acknowledged_without_change(env, gw):
    buyer, order = await _order(env)
    r = await env.client(csrf=False).get(
        NOTIFY, params=notify_params(order["order_no"], "19.90", trade_status="WAIT_BUYER_PAY")
    )
    assert r.text == "success"
    assert await _status(buyer, order) == "pending_payment"


async def test_replay_is_ignored(env, gw):
    _buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    c = env.client(csrf=False)
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert gw.calls == 1  # second one stopped by the Redis replay guard
    assert await _counts(env) == (1, 2, 1)


async def test_requery_disagrees_goes_to_manual_review(env, gw):
    gw.mode = "unpaid"
    buyer, order = await _order(env)
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"
    assert await _counts(env) == (0, 0, 0)


async def test_requery_amount_mismatch(env, gw):
    gw.money = "1.99"
    buyer, order = await _order(env)
    await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert await _status(buyer, order) == "manual_review"


async def test_requery_outage_fails_and_allows_retry(env, gw):
    gw.mode = "down"
    buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    c = env.client(csrf=False)
    assert (await c.get(NOTIFY, params=p)).text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    gw.mode = "paid"
    assert (await c.get(NOTIFY, params=p)).text == "success"  # replay key was released
    assert await _status(buyer, order) == "paid"


async def test_concurrent_identical_notifies(env, gw):
    _buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    c1, c2 = env.client(csrf=False), env.client(csrf=False)
    replies = await asyncio.gather(c1.get(NOTIFY, params=p), c2.get(NOTIFY, params=p))
    assert {r.status_code for r in replies} == {200}
    assert "success" in {r.text for r in replies}
    assert await _counts(env) == (1, 2, 1)


async def test_concurrent_distinct_notifies_hit_the_row_lock(env, gw):
    """Different param sets bypass the replay guard: the DB lock + UNIQUE must hold."""
    _buyer, order = await _order(env)
    variants = [
        notify_params(order["order_no"], "19.90"),
        notify_params(order["order_no"], "19.90", param="retry-1"),
        notify_params(order["order_no"], "19.90", param="retry-2"),
        notify_params(order["order_no"], "19.90", name="other-name"),
    ]
    clients = [env.client(csrf=False) for _ in variants]
    pairs = zip(clients, variants, strict=True)
    replies = await asyncio.gather(*(c.get(NOTIFY, params=p) for c, p in pairs))
    assert [r.text for r in replies] == ["success"] * 4
    assert await _counts(env) == (1, 2, 1)
    assert await env.scalar("SELECT count(*) FROM order_events WHERE to_status='paid'") == 1


async def test_different_trade_no_after_paid_is_flagged(env, gw):
    _buyer, order = await _order(env)
    c = env.client(csrf=False)
    assert (await c.get(NOTIFY, params=notify_params(order["order_no"], "19.90"))).text == "success"
    gw.trade_no = "T-SECOND"
    p = notify_params(order["order_no"], "19.90", trade_no="T-SECOND")
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert await _counts(env) == (1, 2, 1)
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 1


async def test_late_payment_on_expired_order_goes_to_manual_review(env, gw):
    from app.modules.orders.service import expire_pending

    buyer, order = await _order(env)
    async with env.owner_session() as db:
        from sqlalchemy import text

        await db.execute(text("UPDATE orders SET expires_at = now() - interval '5 minutes'"))
        await db.commit()
    assert await expire_pending(env.res.maker) == 1
    assert await _status(buyer, order) == "expired"
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"
    # money was received: payment and ledger recorded, but nothing is fulfilled
    assert await _counts(env) == (1, 2, 0)


async def test_price_change_invalidates_old_payment_amount(env, gw):
    buyer, order = await _order(env)
    agent, _ = await env.login("agent")
    r = await agent.patch(
        f"/api/v1/admin/orders/{order['id']}/price", json={"amount": "12.00", "reason": "discount"}
    )
    assert r.status_code == 200
    # gateway reports the OLD amount the buyer was shown before the change
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"
    # M1: the confirmed old-amount payment is recorded (Payment row), held, never fulfilled
    assert await _counts(env) == (1, 0, 0)


def test_signature_helper_matches_epay():
    p = notify_params("GABCDEFGHIJKL", "1.00")
    assert epay.verify_signature(p, ZPAY_KEY)


# --- regressions from the payment-auditor review (docs/review-v2.md §5) ------------


async def test_replay_marker_is_promoted_to_done_after_settling(env, gw):
    from app.modules.payments.notify import INFLIGHT_TTL_S, REPLAY_TTL_S, replay_key

    _buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    assert (await env.client(csrf=False).get(NOTIFY, params=p)).text == "success"
    key = replay_key(p)
    assert await env.res.redis.get(key) == b"done"
    assert await env.res.redis.ttl(key) > INFLIGHT_TTL_S
    assert await env.res.redis.ttl(key) <= REPLAY_TTL_S


async def test_crash_mid_settle_does_not_lose_the_payment(env, gw):
    """A process killed mid-settle leaves only a short in-flight lock behind."""
    from app.modules.payments.notify import INFLIGHT_TTL_S, replay_key

    buyer, order = await _order(env)
    p = notify_params(order["order_no"], "19.90")
    key = replay_key(p)
    # what a SIGKILLed worker leaves behind
    await env.res.redis.set(key, b"inflight", ex=INFLIGHT_TTL_S)
    c = env.client(csrf=False)
    assert (await c.get(NOTIFY, params=p)).text == "fail"  # unsettled -> ask ZPay to retry
    assert await env.res.redis.ttl(key) <= INFLIGHT_TTL_S  # lapses quickly, not after 24 h
    await env.res.redis.delete(key)  # = the lock expired before ZPay's next retry
    assert (await c.get(NOTIFY, params=p)).text == "success"
    assert await _status(buyer, order) == "paid"


async def test_requery_trade_no_mismatch_goes_to_manual_review(env, gw):
    buyer, order = await _order(env)
    gw.trade_no = "T-SOMETHING-ELSE"
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"
    assert (await _counts(env))[2] == 0  # nothing fulfilled


async def test_requery_missing_trade_no_is_not_accepted(env, gw):
    buyer, order = await _order(env)
    gw.trade_no = ""
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"


async def test_requery_timeout_is_capped_below_zpay_budget(env, gw, monkeypatch):
    import time

    from app.payments import zpay

    monkeypatch.setattr(zpay, "QUERY_TIMEOUT_S", 0.3)

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text="{}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    env.res.zpay._http = client
    buyer, order = await _order(env)
    t0 = time.monotonic()
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    elapsed = time.monotonic() - t0
    await client.aclose()
    assert r.text == "fail"  # ZPay will retry
    assert elapsed < 2.0
    assert await _status(buyer, order) == "pending_payment"


async def test_very_late_payment_is_still_recorded(env, gw):
    """Paid > 24 h after expiry: money must still land in payments + ledger (manual_review)."""
    from sqlalchemy import text

    from app.modules.orders.service import expire_pending

    buyer, order = await _order(env)
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '5 minutes'"))
        await db.commit()
    assert await expire_pending(env.res.maker) == 1
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET expires_at = now() - interval '3 days'"))
        await db.commit()
    r = await env.client(csrf=False).get(NOTIFY, params=notify_params(order["order_no"], "19.90"))
    assert r.text == "success"
    assert await _status(buyer, order) == "manual_review"
    assert await _counts(env) == (1, 2, 0)


async def test_mock_gateway_refuses_outside_dev_and_test(monkeypatch):
    from app.core.config import Settings
    from app.devtools import mock_zpay

    for env_name in ("staging", "prod"):
        s = Settings(env=env_name, zpay_gateway="https://zpayz.cn", cookie_secure=True)
        monkeypatch.setattr(mock_zpay, "get_settings", lambda s=s: s)
        with pytest.raises(SystemExit):
            await mock_zpay._main()


def test_prod_requires_https_query_base():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="APP_ZPAY_API_BASE"):
        Settings(
            env="prod",
            zpay_gateway="https://zpayz.cn",
            zpay_api_base="http://zpayz.cn",
            cookie_secure=True,
        )
