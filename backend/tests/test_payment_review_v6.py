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
    """No legacy URL is RECORDED for this pure-USDT order (``_order`` never issues one), yet with a
    key present the best-effort legacy query still runs (v4 review W2/W3). The case this name
    used to claim -- a URL really issued before a switch -- is the next test down."""
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


async def test_epay_order_refuses_to_cancel_when_the_legacy_key_is_gone(env, monkeypatch):
    """Fail closed: a legacy EPay pay URL was issued (and, since #32, recorded); with the key
    cleared it cannot be verified, and cancelling could release an order already paid for.

    The reachable shape is the operator one: the legacy URL is issued, then a durable gateway is
    configured (so a gateway is still 'ready') and the old environment key is cleared.
    """
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    order = await _prepare(buyer, await env.product(mode="manual"))  # legacy URL, no attempt row
    assert await env.scalar("SELECT count(*) FROM payment_attempts") == 0
    await _gateway(env, admin, monkeypatch)  # a gateway stays configured for the tenant
    monkeypatch.setattr(env.res.settings, "zpay_pid", "")
    monkeypatch.setattr(env.res.settings, "zpay_key", "")

    result = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert result.status_code == 503, result.text
    assert await _status(buyer, order) == "pending_payment"


async def test_a_commit_survives_a_plain_unavailable_raised_afterwards(env, monkeypatch):
    """Inconsistent evidence on a later original must not discard an earlier settlement."""
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
        # paid, but the gateway contradicts itself: a plain Unavailable used to drop the commit
        return QueryResult(True, True, Decimal(order["amount"]), "", no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        with pytest.raises(UnresolvedEvidence) as raised:
            await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
        assert raised.value.settled is not None
        assert raised.value.settled.orders
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_a_settled_original_is_not_requeried_on_retry(env, monkeypatch):
    """Retry amplification: a decided original must not replay its audit trail every tick."""
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _gateway(env, admin, monkeypatch)

    async def create(*args):
        return {"kind": "qr", "payload": "https://qr.alipay.com/fixture"}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    order = await _prepare(buyer, await env.product(mode="manual"))
    seen: list[str] = []

    async def query(self, no):
        seen.append(no)
        return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        first, _ = await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
        assert first is not None
        after_first = len(seen)
        again, _ = await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
    assert len(seen) == after_first, "a settled original was queried again"
    assert again is None
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 0


# --------------------------------------------------------------- independent audit, 2026-11-08
# A cold-started payment-auditor pass (no context shared with the session that wrote #31) found
# the defects below. Each test fails against the code it replaces.


async def _alipay_then_usdt(env):
    """A real legacy EPay URL is issued, THEN the buyer switches the same order to USDT."""
    await set_channels(
        env,
        {
            "zpay_alipay": {"surcharge_permille": 0},
            "usdt_trc20": {"enabled": True, "surcharge_permille": 0},
        },
    )
    product = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 200, r.text
    order = r.json()
    for channel in ("zpay_alipay", "usdt_trc20"):
        p = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": channel})
        assert p.status_code == 200, (channel, p.text)
    return buyer, order


async def _qr(monkeypatch):
    async def create(*args):
        return {"kind": "qr", "payload": "https://qr.alipay.com/fixture"}

    monkeypatch.setattr(ZPayClient, "create_payment", create)


async def test_a_legacy_url_is_recorded_when_issued_and_only_then(env, monkeypatch):
    """#32: the durable record exists exactly when an env-key URL left the server."""
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    legacy = await _prepare(buyer, await env.product(mode="manual"))
    await _gateway(env, admin, monkeypatch)
    await _qr(monkeypatch)
    durable = await _prepare(buyer, await env.product(mode="manual"))
    sql = "SELECT count(*) FROM legacy_pay_urls WHERE local_ref = :n"
    assert await env.scalar(sql, n=legacy["order_no"]) == 1
    assert await env.scalar(sql, n=durable["order_no"]) == 0  # a durable provider records nothing


async def test_durable_only_tenant_with_no_env_key_can_cancel_an_unpaid_order(env, monkeypatch):
    """Audit finding 1: the key check ran even after requery_all had resolved every durable
    attempt as unpaid, so a tenant on durable providers only got a permanent 503."""
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _gateway(env, admin, monkeypatch)
    await _qr(monkeypatch)
    monkeypatch.setattr(env.res.settings, "zpay_pid", "")
    monkeypatch.setattr(env.res.settings, "zpay_key", "")
    order = await _prepare(buyer, await env.product(mode="manual"))

    async def query(self, no):
        return QueryResult(False, False, None, "", no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert await _status(buyer, order) == "cancelled"


async def test_a_recorded_legacy_url_is_drained_after_a_switch_to_usdt(env, mock, monkeypatch):  # noqa: F811
    buyer, order = await _alipay_then_usdt(env)
    queried: list[str] = []

    async def query(self, no):
        queried.append(no)
        return QueryResult(False, False, None, "", no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert queried == [order["order_no"]]


async def test_a_recorded_legacy_url_fails_closed_after_a_switch_to_usdt(env, mock, monkeypatch):  # noqa: F811
    """Audit finding 2(a): the gate read the CURRENT channel, so once the order had switched to
    USDT a cleared key let the cancel proceed over a legacy URL still in the wild."""
    buyer, order = await _alipay_then_usdt(env)
    monkeypatch.setattr(env.res.settings, "zpay_pid", "")
    monkeypatch.setattr(env.res.settings, "zpay_key", "")
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 503, r.text
    assert await _status(buyer, order) == "pending_payment"


async def test_a_recorded_legacy_url_is_queried_despite_a_later_durable_attempt(env, monkeypatch):
    """Audit finding 2(b): any durable provider attempt made legacy_allowed() False for the whole
    payable, so money paid on the earlier legacy URL was never looked for and the cancel ran."""
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    order = await _prepare(buyer, await env.product(mode="manual"))  # legacy URL, recorded
    await _gateway(env, admin, monkeypatch)
    await _qr(monkeypatch)
    again = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert again.status_code == 200, again.text  # now a durable attempt exists too

    async def query(self, no):
        if no == order["order_no"]:  # the legacy URL was paid
            return QueryResult(True, True, Decimal(order["amount"]), "LEGACY-TRADE-1", no)
        return QueryResult(False, False, None, "", no)  # the durable attempt was not

    monkeypatch.setattr(ZPayClient, "query_order", query)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid", r.text
    assert await _status(buyer, order) in {"paid", "manual_review"}


async def test_a_deconfigured_tenant_fails_closed_over_a_recorded_legacy_url(env, monkeypatch):
    """Audit finding 2(c) / #32: with no key, no provider and no attempt history gateway_ready was
    False, so _settle_if_paid never ran and the cancel proceeded unqueried."""
    buyer, _ = await env.login()
    order = await _prepare(buyer, await env.product(mode="manual"))  # legacy URL, recorded
    monkeypatch.setattr(env.res.settings, "zpay_pid", "")
    monkeypatch.setattr(env.res.settings, "zpay_key", "")
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 503, r.text
    assert await _status(buyer, order) == "pending_payment"


async def _two_durable_originals(env, monkeypatch):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _gateway(env, admin, monkeypatch)
    await _qr(monkeypatch)
    order = await _prepare(buyer, await env.product(mode="manual"))
    await _gateway(env, admin, monkeypatch, "fixture2")
    again = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert again.status_code == 200, again.text
    return buyer, order


async def test_an_unexpected_error_after_a_commit_still_carries_the_settlement(env, monkeypatch):
    """Audit finding 3: only Unavailable was carried, so any other exception after the first
    original settled lost its broadcast and duplicate alert for good."""
    _, order = await _two_durable_originals(env, monkeypatch)
    seen: list[str] = []

    async def query(self, no):
        seen.append(no)
        if len(seen) == 1:
            return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)
        raise RuntimeError("driver failure on the second original")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    async with env.api_session() as db:
        with pytest.raises(UnresolvedEvidence) as raised:
            await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
    assert raised.value.settled is not None and raised.value.settled.orders
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_the_settled_mark_rides_in_the_settlement_commit(env, monkeypatch):
    """Audit finding 4: the attempt was marked settled only AFTER apply_* had committed, so a
    crash in between left it 'ready' and the next tick re-applied it."""
    from app.modules.payments import reconcile

    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _gateway(env, admin, monkeypatch)
    await _qr(monkeypatch)
    order = await _prepare(buyer, await env.product(mode="manual"))

    async def query(self, no):
        return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    real, calls = reconcile._mark_settled, []

    async def crash_after_apply(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:  # the re-mark that follows apply's own commit
            raise RuntimeError("process died between the settlement and the mark")
        return await real(*args, **kwargs)

    monkeypatch.setattr(reconcile, "_mark_settled", crash_after_apply)
    async with env.api_session() as db:
        with pytest.raises(UnresolvedEvidence):
            await requery_attempts(db, env.res, env.tenant_id, order["order_no"])
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT status FROM payment_attempts") == "settled"


async def test_a_timeout_keeps_what_already_committed(env, monkeypatch):
    """#33: asyncio.wait_for cancels the coroutine, so an outcome it was about to return was
    lost for good. The caller's sink holds every outcome the moment it commits."""
    import asyncio

    _, order = await _two_durable_originals(env, monkeypatch)
    seen: list[str] = []

    async def query(self, no):
        seen.append(no)
        if len(seen) == 1:
            return QueryResult(True, True, Decimal(order["amount"]), secrets.token_hex(16), no)
        await asyncio.sleep(60)  # the second original hangs
        raise AssertionError("unreachable")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    sink: list = []
    async with env.api_session() as db:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                requery_all(db, env.res, env.tenant_id, order["order_no"], sink), timeout=3
            )
    assert len(sink) == 1 and sink[0].orders
    assert await env.scalar("SELECT count(*) FROM payments") == 1


async def test_every_distinct_duplicate_keeps_its_own_staff_alert(env, monkeypatch):
    """Audit finding 5: merge_outcomes kept only the LAST dup_trade_no, so with three paid
    originals one of the two refunds staff owed never raised an alert."""
    import uuid

    from app.core import notify_tg
    from app.models import Order
    from app.modules.payments.reconcile import merge_outcomes
    from app.modules.payments.settle import NotifyOutcome

    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))
    async with env.api_session() as db:
        o = await db.get(Order, uuid.UUID(order["id"]))
        merged = await merge_outcomes(
            db,
            env.tenant_id,
            [
                NotifyOutcome("success", o, [], [o], "TRADE-B"),
                NotifyOutcome("success", o, [], [o], "TRADE-C"),
                NotifyOutcome("success", o, [], [o], None),
            ],
        )
    assert merged.dup_trade_nos == ("TRADE-B", "TRADE-C")

    sent: list[str] = []
    monkeypatch.setattr(notify_tg, "_outcome_events", lambda res, orders: None)
    monkeypatch.setattr(
        notify_tg, "order_event", lambda res, o, kind, lines, **kw: sent.append(lines[0])
    )
    notify_tg.outcome_events(env.res, merged.orders, merged.dup_trade_nos)
    assert sorted(sent) == [
        "SECOND payment, refund it: trade TRADE-B",
        "SECOND payment, refund it: trade TRADE-C",
    ]


async def test_a_cancel_inside_the_pay_window_never_receives_a_url(env, monkeypatch):
    """Re-audit finding (PR #44): /pay commits and releases its row lock before prepare and the
    marker write, so a cancel can finish in between -- it sees no marker, queries "unpaid" and
    cancels. The URL must then never reach the buyer."""
    from app.modules.payments import gateways

    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))

    async def cancel_meanwhile(db, res, **kw):
        async with env.owner_session() as other:  # a separate connection: the racing cancel
            await other.execute(
                text("UPDATE orders SET status = 'cancelled' WHERE order_no = :n"), {"n": kw["no"]}
            )
            await other.commit()
        return None  # no durable provider: the legacy path builds a URL

    monkeypatch.setattr(gateways, "prepare", cancel_meanwhile)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state", r.text
    assert "pay_url" not in r.text
