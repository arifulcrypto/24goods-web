"""Regressions from the independent v6 source review; synthetic money and transports only."""

import secrets
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from app.models import PaymentAttempt
from app.modules.payments.notify import replay_key
from app.modules.payments.reconcile import requery_attempts
from app.payments import bepusdt, epay
from app.payments.bounded import response_bytes
from app.payments.zpay import QueryResult, ZPayClient, ZPayQueryError, parse_query_response
from tests.helpers import buy
from tests.test_epay_gateways_v6 import _gateway
from tests.test_usdt_v52 import _paid_order, _post, _status, mock, set_channels  # noqa: F401


@pytest.mark.parametrize(
    "number", ["1e1000", "1e-1000", "NaN", "12345678901234567890123456789012345678901234567890"]
)
def test_native_numbers_rejected_before_signature_formatting(number):
    with pytest.raises(ValueError):
        bepusdt.parse_json(('{"amount":' + number + ',"signature":"invalid"}').encode())
    assert not bepusdt.verify_signature(
        {"amount": Decimal("1e1000"), "signature": "0" * 32}, "test-only"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"code": -2, "msg": "merchant authentication failed"},
        {"code": -1, "msg": "upstream unavailable"},
        {"code": 1, "status": 9},
    ],
)
def test_query_error_is_never_negative_payment_evidence(payload):
    with pytest.raises(ZPayQueryError):
        parse_query_response(payload)


class Chunks(httpx.AsyncByteStream):
    def __init__(self):
        self.read = 0

    async def __aiter__(self):
        for _ in range(100):
            self.read += 1
            yield b"x" * 16


async def test_response_stream_stops_at_cap_and_rejects_compression():
    stream = Chunks()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        async with client.stream("GET", "https://fixture.example") as response:
            with pytest.raises(ValueError):
                await response_bytes(response, 32)
    assert stream.read < 100
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Chunks())
        )
    ) as client:
        async with client.stream("GET", "https://fixture.example") as response:
            with pytest.raises(ValueError):
                await response_bytes(response, 8192)


async def _prepare(buyer, product):
    order = await buy(buyer, product)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200, r.text
    return order


async def test_long_provider_trade_ids_remain_distinct_and_idempotent(env, monkeypatch):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    _, key = await _gateway(env, admin, monkeypatch)

    async def create(*args):
        return {"kind": "qr", "payload": "https://qr.alipay.com/fixture"}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    product = await env.product(mode="manual")
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE products SET manual_stock=3 WHERE id=:id"), {"id": product.id}
        )
        await db.commit()
    orders = [await _prepare(buyer, product) for _ in range(2)]
    refs = {}
    async with env.api_session() as db:
        for att in await db.scalars(
            select(PaymentAttempt).where(PaymentAttempt.tenant_id == env.tenant_id)
        ):
            refs[att.local_ref] = att.gateway_ref
    trades = {
        refs[o["order_no"]]: ("shared-prefix-" + "x" * 48 + str(i)) for i, o in enumerate(orders)
    }

    async def query(self, no):
        return QueryResult(True, True, Decimal(orders[0]["amount"]), trades[no], no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    for order in orders:
        no = refs[order["order_no"]]
        params = {
            "pid": "fixture1",
            "out_trade_no": no,
            "trade_no": trades[no],
            "money": order["amount"],
            "trade_status": "TRADE_SUCCESS",
            "type": "alipay",
            "sign_type": "MD5",
        }
        params["sign"] = epay.sign(params, key)
        for _ in range(2):
            result = await env.client(csrf=False).get("/api/v1/pay/notify/zpay", params=params)
            assert result.text == "success", result.text
            await env.res.redis.delete(replay_key(params))
    assert await env.scalar("SELECT count(*) FROM payments") == 2
    assert await env.scalar("SELECT count(*) FROM orders WHERE status='paid'") == 2
    assert await env.scalar("SELECT max(length(gateway_trade_no)) FROM payments") == 64


async def test_recovery_checks_second_paid_original_after_first_settles(env, monkeypatch):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _gateway(env, admin, monkeypatch)

    async def create(*args):
        return {"kind": "qr", "payload": "https://qr.alipay.com/fixture"}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    order = await _prepare(buyer, await env.product(mode="manual"))
    await _gateway(env, admin, monkeypatch, "fixture2")
    assert (
        await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    ).status_code == 200
    seen = []

    async def query(self, no):
        seen.append(no)
        return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        outcome, _ = await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
        assert outcome is not None
    assert len(set(seen)) == 2
    assert await _status(buyer, order) in {"paid", "manual_review"}
    assert outcome.dup_trade_no is not None
    assert await env.scalar("SELECT count(*) FROM payment_attempts WHERE status='settled'") == 2
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 1


async def test_cancel_requeries_old_usdt_after_switch_to_epay(env, mock):  # noqa: F811
    buyer, order, _page, trade = await _paid_order(env, mock)
    changed = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert changed.status_code == 200, changed.text
    await mock.patch_trade(trade, status=2)
    result = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert result.status_code == 409, result.text
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_unknown_native_receiver_is_recorded_without_fulfilment(env, mock):  # noqa: F811
    buyer, order, _page, trade = await _paid_order(env, mock)
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE payment_attempts SET data=data-'trade_id'-'address'-'actual_amount', "
                "status='unknown' WHERE tenant_id=:tid"
            ),
            {"tid": env.tenant_id},
        )
        await db.commit()
    await env.res.redis.delete(f"usdt:attempt:{order['order_no']}")
    await mock.patch_trade(trade, status=2)
    result = await _post(env, await mock.body(trade))
    assert result.status_code == 200, result.text
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='order.paid'") == 0


async def test_rejected_login_events_are_sampled_even_after_rate_limit(env):
    client = env.client()
    for _ in range(15):
        await client.post(
            "/api/v1/auth/login", json={"email": "missing@example.com", "password": "incorrect"}
        )
    assert (
        await env.scalar(
            "SELECT count(*) FROM operation_events WHERE family='login' AND outcome='denied'"
        )
        <= 2
    )
