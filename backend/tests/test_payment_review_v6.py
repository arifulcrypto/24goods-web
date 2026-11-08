"""Regressions from the independent v6 source review; synthetic money and transports only."""

import secrets
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from app.models import PaymentAttempt
from app.modules.payments.notify import replay_key
from app.modules.payments.reconcile import UnresolvedEvidence, requery_all, requery_attempts
from app.payments import bepusdt, epay
from app.payments.bounded import response_bytes
from app.payments.zpay import QueryResult, ZPayClient, ZPayQueryError, parse_query_response
from tests.helpers import buy
from tests.test_epay_gateways_v6 import _gateway
from tests.test_usdt_v52 import (  # noqa: F401
    _order,
    _paid_order,
    _post,
    _status,
    mock,
    set_channels,
)


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


async def test_usdt_only_cancel_never_queries_unconfigured_legacy_epay(env, mock, monkeypatch):  # noqa: F811
    """Second-round review blocker: an unpaid USDT order with no EPay key must cancel, not 503."""
    buyer, order, p = await _order(env)
    assert p.status_code == 200, p.text
    monkeypatch.setattr(env.res.settings, "zpay_pid", "")
    monkeypatch.setattr(env.res.settings, "zpay_key", "")
    queried: list[str] = []

    async def query(self, no):
        queried.append(no)
        raise ZPayQueryError("no legacy credentials are configured")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    result = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert result.status_code == 200, result.text
    assert queried == []
    assert await _status(buyer, order) == "cancelled"


async def test_usdt_order_drains_a_legacy_epay_url_while_the_key_exists(
    env,
    mock,  # noqa: F811
    monkeypatch,
):
    """A pay URL issued before a switch to USDT stays drainable: the key decides, not the
    channel."""
    buyer, order, p = await _order(env)
    assert p.status_code == 200, p.text
    queried: list[str] = []

    async def query(self, no):
        queried.append(no)
        return QueryResult(False, False, None, "", no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    result = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert result.status_code == 200, result.text
    assert queried == [order["order_no"]]


async def test_committed_settlement_survives_an_unresolved_original(env, monkeypatch):
    """Second-round review blocker: a durably settled original must not be lost when another
    original is unavailable; its outcome travels with the failure so alerts still fire."""
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
    seen: list[str] = []

    async def query(self, no):
        seen.append(no)
        if len(seen) == 1:
            return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)
        raise ZPayQueryError("upstream unavailable")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        with pytest.raises(UnresolvedEvidence) as raised:
            await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
        settled = raised.value.settled
        assert settled is not None and settled.orders, "the committed payment was dropped"
    assert len(seen) == 2
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await _status(buyer, order) in {"paid", "manual_review"}


async def test_requery_all_propagates_a_committed_outcome_through_mixed_evidence(env, monkeypatch):
    """requery_all must report what it committed even while the rest stays unresolved."""
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
    seen: list[str] = []

    async def query(self, no):
        seen.append(no)
        if len(seen) == 1:
            return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)
        raise ZPayQueryError("upstream unavailable")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        with pytest.raises(UnresolvedEvidence) as raised:
            await requery_all(db, env.res, env.tenant_id, order["order_no"])
        assert raised.value.settled is not None
        assert raised.value.settled.orders
    assert await env.scalar("SELECT count(*) FROM payments") == 1
