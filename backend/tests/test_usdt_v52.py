"""USDT via self-hosted BEpusdt (docs/review-v5.2.md s7): pay attempt, pay page, the notify
pipeline against the BEpusdt simulator (signature, amount, status, re-query, idempotency,
channel gating, tenant isolation)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from app.devtools.mock_bepusdt import create_mock_app, notify_body
from app.payments import bepusdt
from tests.conftest import BEPUSDT_TOKEN

NOTIFY = "/api/v1/pay/notify/bepusdt"


async def set_channels(env, channels: dict, tenant_id=None) -> None:
    patch = {"payments": {"channels": channels}}
    async with env.owner_session(tenant_id) as db:
        await db.execute(
            text("UPDATE tenants SET settings = settings || CAST(:p AS jsonb) WHERE id = :t"),
            {"p": json.dumps(patch), "t": str(tenant_id or env.tenant_id)},
        )
        await db.commit()


class Mock:
    def __init__(self, env, client: httpx.AsyncClient) -> None:
        self.env, self.client = env, client

    async def trade(self, trade_id: str) -> dict:
        raw = await self.env.s.redis.get(f"mockpay:usdt:{trade_id}")
        return json.loads(raw)

    async def patch_trade(self, trade_id: str, **changes) -> None:
        t = await self.trade(trade_id)
        t.update(changes)
        await self.env.s.redis.set(f"mockpay:usdt:{trade_id}", json.dumps(t))

    async def body(self, trade_id: str, status: int = 2, **over) -> dict:
        b = notify_body(await self.trade(trade_id), BEPUSDT_TOKEN, status)
        if over:
            b.update(over)
            b.pop("signature")
            b["signature"] = bepusdt.sign(b, BEPUSDT_TOKEN)
        return b


@pytest.fixture
async def mock(env):
    from tests.conftest import LazyASGI  # noqa: F401  (documented dependency)

    api_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app))
    app = create_mock_app(env.res.settings, env.s.redis, api_http)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bep")
    env.res.bepusdt._http = client
    yield Mock(env, client)
    await client.aclose()
    await api_http.aclose()


async def _order(env, channel="usdt_trc20", price=1990, enable=True):
    if enable:
        await set_channels(
            env,
            {
                "zpay_alipay": {"surcharge_permille": 0},
                channel: {"enabled": True, "surcharge_permille": 0},
            },
        )
    product = await env.product(mode="manual", price=price, floor=100)
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 200, r.text
    order = r.json()
    p = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": channel})
    return buyer, order, p


async def _paid_order(env, mock, **kw):
    buyer, order, p = await _order(env, **kw)
    assert p.status_code == 200, p.text
    page = (await buyer.get(p.json()["pay_url"].replace("/pay/usdt/", "/api/v1/pay/usdt/"))).json()
    keys = await env.s.redis.keys("mockpay:usdt:*-*")
    trade_id = next(k.decode().split(":")[-1] for k in keys if b":o:" not in k)
    return buyer, order, page, trade_id


async def _status(buyer, order) -> str:
    return (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"]


async def _post(env, body: dict, host: str = "testserver"):
    return await env.client(host, csrf=False).post(NOTIFY, content=json.dumps(body))


async def test_happy_path_pay_page_notify_requery_ledger(env, mock):
    buyer, order, page, trade_id = await _paid_order(env, mock)
    assert page["channel"] == "usdt_trc20" and page["network"].startswith("TRON")
    assert page["address"] == "TMockAddressForDemoOnly000000000000"
    assert page["usdt_amount"] == "2.76" and page["fiat_amount_minor"] == 1990
    assert page["qr_svg"].lstrip().startswith("<svg") and "<script" not in page["qr_svg"]
    assert page["status"] == "pending_payment" and 0 < page["seconds_left"] <= 1200

    # the simulator's "pay" button sends the signed JSON notify to our API
    r = await mock.client.post(f"/pay/{trade_id}")
    assert r.status_code == 302
    assert await _status(buyer, order) == "paid"
    assert (await buyer.get(f"/api/v1/pay/usdt/{order['order_no']}")).json()["status"] == "paid"

    assert await env.scalar("SELECT channel FROM payments") == "usdt_trc20"
    assert await env.scalar("SELECT gateway_trade_no FROM payments") == trade_id
    raw = await env.scalar("SELECT raw::text FROM payments")
    assert "actual_amount" in raw and "block_transaction_id" in raw and "signature" not in raw
    assert await env.scalar("SELECT channel FROM orders") == "usdt_trc20"
    assert await env.scalar("SELECT count(*) FROM outbox") == 1
    assert await env.scalar("SELECT sum(amount_minor) FROM ledger_entries") == 0
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='gateway_clearing:bepusdt'"
        )
        == 1990
    )
    assert await env.scalar("SELECT count(*) FROM ledger_entries WHERE account LIKE '%zpay%'") == 0


async def test_pay_attempt_is_reused_and_amount_is_server_side(env, mock):
    buyer, order, _p = await _order(env, channel="usdt_xlayer")
    again = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_xlayer"})
    assert again.status_code == 200
    assert len([k for k in await env.s.redis.keys("mockpay:usdt:*-*")]) == 1  # no 2nd trade
    # unknown / spoofed channel is refused by schema validation
    bad = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_evm"})
    assert bad.status_code == 422


async def test_bad_signature_rejected(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    body = await mock.body(trade_id)
    body["signature"] = "0" * 32
    r = await _post(env, body)
    assert r.status_code == 403 and r.text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    body = await mock.body(trade_id)
    body["amount"] = "0.01"  # tampered after signing
    r = await _post(env, body)
    assert r.status_code == 403 and r.text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    body.pop("signature")
    assert (await _post(env, body)).text == "fail"
    assert await env.scalar("SELECT count(*) FROM payments") == 0


async def test_amount_mismatch_goes_to_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    r = await _post(env, await mock.body(trade_id, amount="0.01"))
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox") == 0
    assert r.text in ("ok", "fail")


async def test_requery_disagrees_goes_to_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    body = await mock.body(trade_id)  # forged-but-signed: gateway state says 1.00 and unpaid
    await mock.patch_trade(trade_id, amount="1.00")
    await _post(env, body)
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    assert await env.scalar("SELECT count(*) FROM outbox") == 0


async def test_requery_says_unpaid_goes_to_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await _post(env, await mock.body(trade_id))  # gateway still reports status 1
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox") == 0


async def test_requery_wrong_network_goes_to_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2, trade_type="usdt.xlayer")
    await _post(env, await mock.body(trade_id))
    assert await _status(buyer, order) == "manual_review"


async def test_requery_transport_failure_is_retryable(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2)
    body = await mock.body(trade_id)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    good = env.res.bepusdt._http
    env.res.bepusdt._http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    r = await _post(env, body)
    assert r.status_code == 503 and r.text == "fail"
    assert await _status(buyer, order) == "pending_payment"
    env.res.bepusdt._http = good  # the gateway retries -> now processed (no stale replay lock)
    retried = await _post(env, body)
    assert retried.status_code == 200 and retried.text == "ok"
    assert await _status(buyer, order) == "paid"
    assert (await _post(env, body)).status_code == 200
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox") == 1


@pytest.mark.parametrize("status", [1, 3])
async def test_waiting_and_expired_are_acknowledged_without_change(env, mock, status):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    before = await env.scalar("SELECT count(*) FROM audit_log")
    r = await _post(env, await mock.body(trade_id, status=status))
    assert r.text == "ok" and await _status(buyer, order) == "pending_payment"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    assert await env.scalar("SELECT count(*) FROM audit_log") == before


async def test_duplicate_notify_is_idempotent(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2)
    body = await mock.body(trade_id)
    assert (await _post(env, body)).text == "ok"
    snap = (
        await env.scalar("SELECT count(*) FROM payments"),
        await env.scalar("SELECT count(DISTINCT txn_id) FROM ledger_entries"),
        await env.scalar("SELECT count(*) FROM outbox"),
    )
    assert (await _post(env, body)).text == "ok"
    # a retry with a different body (e.g. other block id) for the same trade is also a no-op
    again = await mock.body(trade_id, block_transaction_id="0xdeadbeef")
    assert (await _post(env, again)).text == "ok"
    assert snap == (
        await env.scalar("SELECT count(*) FROM payments"),
        await env.scalar("SELECT count(DISTINCT txn_id) FROM ledger_entries"),
        await env.scalar("SELECT count(*) FROM outbox"),
    )
    assert snap[0] == 1 and await _status(buyer, order) == "paid"


async def test_disabled_channel_refused_and_default_is_off(env, mock):
    product = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    order = r.json()
    p = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_arbitrum"})
    assert p.status_code == 503
    assert await env.s.redis.keys("mockpay:usdt:*") == []  # nothing reached the gateway
    chans = (await buyer.get("/api/v1/site/payment-channels")).json()
    assert [c["id"] for c in chans] == ["zpay_alipay"]
    await set_channels(env, {"usdt_arbitrum": {"enabled": True}})
    chans = (await buyer.get("/api/v1/site/payment-channels")).json()
    assert {c["id"] for c in chans} == {"zpay_alipay", "usdt_arbitrum"}
    assert next(c for c in chans if c["id"] == "usdt_arbitrum")["permille"] == 0


async def test_notify_for_other_tenants_order_is_refused(env, mock):
    from app.models import Order

    other_buyer = await env.user("buyer", tenant_id=env.other_tenant_id)
    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            Order(
                tenant_id=env.other_tenant_id,
                order_no="GOTHERTENANT1",
                buyer_id=other_buyer.id,
                title="x",
                amount_minor=1990,
                subtotal_minor=1990,
                delivery_mode="manual",
                channel="usdt_trc20",
            )
        )
        await db.commit()
    body = {
        "trade_id": "x-1",
        "order_id": "GOTHERTENANT1",
        "amount": "19.90",
        "actual_amount": "2.76",
        "status": 2,
    }
    body["signature"] = bepusdt.sign(body, BEPUSDT_TOKEN)
    res = await _post(env, body)  # delivered on the platform tenant's host
    assert res.text == "fail"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    async with env.owner_session(env.other_tenant_id) as db:
        status = await db.scalar(text("SELECT status FROM orders WHERE order_no='GOTHERTENANT1'"))
    assert status == "pending_payment"


async def test_pay_page_is_owner_only(env, mock):
    _buyer, order, _page, _trade = await _paid_order(env, mock)
    stranger, _ = await env.login("buyer")
    r = await stranger.get(f"/api/v1/pay/usdt/{order['order_no']}")
    assert r.status_code == 404
    anon = env.client()
    assert (await anon.get(f"/api/v1/pay/usdt/{order['order_no']}")).status_code in (401, 403)


async def test_cancel_requery_settles_a_paid_usdt_order(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2)  # paid on chain, notify not yet delivered
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid"
    assert await _status(buyer, order) == "paid"
    assert await env.scalar("SELECT channel FROM payments") == "usdt_trc20"


def test_signature_scheme_and_float_decimals():
    p = {"order_id": "A1", "amount": "42", "notify_url": "http://x/n", "redirect_url": "http://x/r"}
    s = bepusdt.sign(p, "tok")
    assert s == bepusdt.sign({**p, "signature": "ignored", "empty": ""}, "tok")
    assert bepusdt.verify_signature({**p, "signature": s.upper()}, "tok")
    assert not bepusdt.verify_signature({**p, "signature": s}, "")
    assert not bepusdt.verify_signature(p, "tok")
    parsed = bepusdt.parse_json(b'{"amount": 100.00, "status": 2}')
    assert str(parsed["amount"]) == "100.00" and parsed["status"] == 2
    assert bepusdt.amount_literal(bepusdt.decimal_of("28.80")) == "28.8"


async def test_durable_attempt_recovers_after_redis_loss(env, mock):
    buyer, order, page, trade_id = await _paid_order(env, mock)
    await env.s.redis.delete(f"usdt:attempt:{order['order_no']}")
    view = await buyer.get(f"/api/v1/pay/usdt/{order['order_no']}")
    assert view.status_code == 200 and view.json()["address"] == page["address"]
    await mock.patch_trade(trade_id, status=2)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid", r.text
    assert await _status(buyer, order) == "paid"


async def test_unknown_usdt_create_is_durable_and_never_blindly_retried(env, mock, monkeypatch):
    async def unknown(**kw):
        raise bepusdt.BepusdtError("uncertain fixture")

    monkeypatch.setattr(env.res.bepusdt, "create_transaction", unknown)
    buyer, order, response = await _order(env)
    assert response.status_code == 503
    assert await env.scalar("SELECT count(*) FROM payment_attempts WHERE status='unknown'") == 1
    response = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_trc20"})
    assert response.status_code == 503
    assert await env.scalar("SELECT count(*) FROM payment_attempts") == 1
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})).status_code == 503


async def test_original_usdt_credential_and_receiver_binding(env, mock):
    from dataclasses import replace

    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2)
    # Rotation does not reinterpret existing attempts under the new environment key.
    env.res.bepusdt.config = replace(env.res.bepusdt.config, token="new-test-token-only")
    result = await _post(env, await mock.body(trade_id))
    assert result.status_code == 200, result.text
    assert await _status(buyer, order) == "paid"


async def test_changed_upstream_receiver_is_held_for_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await mock.patch_trade(trade_id, status=2, address="TChangedReceiverFixtureOnly000000")
    result = await _post(env, await mock.body(trade_id))
    assert result.status_code == 200, result.text
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0
