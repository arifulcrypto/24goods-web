"""review-v10: several EPay gateways: priority + sticky failover, admin API, test payments."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.core import safefetch
from app.models import PaymentAttempt, PaymentGateway, PaymentTest
from app.modules.payments.gateways import order_candidates
from app.payments import epay
from app.payments.zpay import (
    QueryResult,
    ZPayClient,
    ZPayConfig,
    ZPayQueryError,
    ZPayRefused,
    ZPayUnreachable,
)
from tests.conftest import PASSWORD
from tests.helpers import buy

GW = "/api/v1/admin/payment-gateways"
PAY = "/api/v1/orders/{}/pay"
NOTIFY = "/api/v1/pay/notify/zpay"
NOW = datetime.now(timezone.utc)


def _row(name, priority=100, enabled=True, ok=None, fail=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        label=name,
        priority=priority,
        enabled=enabled,
        last_ok_at=ok,
        last_fail_at=fail,
        created_at=NOW,
    )


# ---------------------------------------------------------------------------- ordering (unit)
def test_priority_mode_orders_by_priority_and_skips_disabled():
    a, b, c = _row("a", 30), _row("b", 10, ok=NOW), _row("c", 20, enabled=False)
    assert [r.label for r in order_candidates([a, b, c], "priority")] == ["b", "a"]


def test_sticky_mode_prefers_latest_ok_then_priority():
    a = _row("a", 10, ok=NOW - timedelta(hours=2))
    b = _row("b", 20, ok=NOW - timedelta(minutes=1))
    c = _row("c", 5)
    assert [r.label for r in order_candidates([a, b, c], "sticky")] == ["b", "c", "a"]
    assert [r.label for r in order_candidates([a, b, c], "priority")] == ["c", "a", "b"]


def test_sticky_ignores_a_gateway_that_failed_after_its_last_success():
    b = _row("b", 20, ok=NOW - timedelta(minutes=5), fail=NOW - timedelta(minutes=1))
    a = _row("a", 10)
    assert [r.label for r in order_candidates([a, b], "sticky")] == ["a", "b"]


def test_sticky_ignores_successes_older_than_the_primary_marker():
    a, b = _row("a", 5), _row("b", 20, ok=NOW - timedelta(hours=1))
    assert [r.label for r in order_candidates([a, b], "sticky", NOW)] == ["a", "b"]


# ------------------------------------------------------------------------ client classification
def _client(handler, key="k" * 16):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ZPayConfig(
        "pid", key, "https://p.example", "https://p.example", "https://s/n", "https://s/r"
    )
    return ZPayClient(cfg, http)


async def test_create_payment_refusal_is_explicit_and_key_is_redacted():
    key = "secretsecretsecret"
    c = _client(lambda _: httpx.Response(200, json={"code": -1, "msg": f"bad {key} sign"}), key)
    with pytest.raises(ZPayRefused) as caught:
        await c.create_payment("GX1", "i", Decimal("1.00"), "1.1.1.1")
    assert key not in caught.value.gateway_message and "***" in caught.value.gateway_message


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"code": -1, "msg": "boom"}),
        httpx.Response(200, text="<html>nope</html>"),
        httpx.Response(200, json=["code", 1]),
        httpx.Response(200, json={"msg": "no code"}),
        httpx.Response(200, json={"code": "abc", "msg": "x"}),
        httpx.Response(200, json={"code": True, "msg": "x"}),
        httpx.Response(200, json={"code": 1.5, "msg": "x"}),
    ],
)
async def test_create_payment_ambiguous_answers_are_not_refusals(response):
    c = _client(lambda _: response)
    with pytest.raises(ZPayQueryError) as caught:
        await c.create_payment("GX1", "i", Decimal("1.00"), "1.1.1.1")
    assert not isinstance(caught.value, (ZPayRefused, ZPayUnreachable))


# ------------------------------------------------------------------------------ helpers
async def _add_gateway(env, admin, monkeypatch, pid, *, priority=None, enable=True):
    async def vetted(url):
        return safefetch.Target(url, "https", "gateway.example", 443, "/", "1.1.1.1")

    monkeypatch.setattr(safefetch, "vet_url", vetted)
    key = secrets.token_hex(16)
    r = await admin.post(
        GW,
        json={
            "label": "gw " + pid,
            "gateway": f"https://{pid}.example",
            "merchant_id": pid,
            "secret": key,
            "current_password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    ref = r.json()["id"]
    body = {"enabled": enable}
    if priority is not None:
        body["priority"] = priority
    r = await admin.patch(f"{GW}/{ref}", json=body)
    assert r.status_code == 200, r.text
    return ref, key


def _stub_create(monkeypatch, behaviour):
    calls: list[str] = []

    async def create(self, order_no, *a):
        calls.append(self.config.pid)
        outcome = behaviour[self.config.pid]
        if isinstance(outcome, Exception):
            raise outcome
        return {"kind": "qr", "payload": "https://qr.alipay.com/" + self.config.pid}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    return calls


async def _setup(env, monkeypatch, behaviour, *, mode="priority", pa=10, pb=20):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    a = await _add_gateway(env, admin, monkeypatch, "gwa", priority=pa)
    b = await _add_gateway(env, admin, monkeypatch, "gwb", priority=pb)
    r = await admin.put(f"{GW}/routing", json={"mode": mode})
    assert r.status_code == 200, r.text
    calls = _stub_create(monkeypatch, behaviour)
    return admin, buyer, a, b, calls


async def _order(env, buyer):
    return await buy(buyer, await env.product(mode="manual"))


async def _attempts(env):
    async with env.api_session() as db:
        return list(
            await db.scalars(
                select(PaymentAttempt)
                .where(PaymentAttempt.tenant_id == env.tenant_id)
                .order_by(PaymentAttempt.created_at)
            )
        )


async def _gw_row(env, ref):
    async with env.api_session() as db:
        return await db.scalar(select(PaymentGateway).where(PaymentGateway.id == uuid.UUID(ref)))


# ------------------------------------------------------------------------------- failover
@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (ZPayUnreachable("gateway unreachable"), "gateway_unreachable"),
        (ZPayRefused("merchant disabled"), "gateway_refused"),
    ],
)
async def test_failover_to_next_gateway_on_clean_failure(env, monkeypatch, exc, reason):
    _admin, buyer, a, b, calls = await _setup(env, monkeypatch, {"gwa": exc, "gwb": None})
    order = await _order(env, buyer)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and r.json()["pay_url"].startswith("/pay/epay/"), r.text
    assert calls == ["gwa", "gwb"]
    first, second = await _attempts(env)
    assert (first.status, first.data["reason"], str(first.provider_id)) == ("failed", reason, a[0])
    assert (second.status, str(second.provider_id)) == ("ready", b[0])
    assert first.local_ref == second.local_ref and first.gateway_ref != second.gateway_ref
    ra, rb = await _gw_row(env, a[0]), await _gw_row(env, b[0])
    assert ra.last_fail_reason == reason and ra.last_ok_at is None
    assert rb.last_ok_at is not None
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.attempt_failed' AND tenant_id=:t",
            t=str(env.tenant_id),
        )
        == 1
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM operation_events WHERE action='payment.gateway_failed'"
            " AND tenant_id=:t",
            t=str(env.tenant_id),
        )
        == 1
    )


@pytest.mark.parametrize(
    "exc", [ZPayQueryError("query failed: ReadTimeout"), TimeoutError(), ZPayQueryError("x")]
)
async def test_no_failover_on_ambiguous_outcome(env, monkeypatch, exc):
    if isinstance(exc, TimeoutError):
        import asyncio

        exc = asyncio.TimeoutError()
    _admin, buyer, a, _b, calls = await _setup(env, monkeypatch, {"gwa": exc, "gwb": None})
    order = await _order(env, buyer)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable", r.text
    assert calls == ["gwa"]
    (att,) = await _attempts(env)
    assert att.status == "unknown" and str(att.provider_id) == a[0]


async def test_every_gateway_failing_is_channel_unreachable(env, monkeypatch):
    _admin, buyer, _a, _b, calls = await _setup(
        env, monkeypatch, {"gwa": ZPayUnreachable("x"), "gwb": ZPayRefused("no")}
    )
    order = await _order(env, buyer)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "channel_unreachable", r.text
    assert calls == ["gwa", "gwb"]
    assert [x.status for x in await _attempts(env)] == ["failed", "failed"]
    view = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert view["status"] == "pending_payment"


async def test_sticky_remembers_the_gateway_that_worked_and_priority_does_not(env, monkeypatch):
    behaviour = {"gwa": ZPayUnreachable("x"), "gwb": None}
    admin, buyer, a, b, calls = await _setup(env, monkeypatch, behaviour, mode="sticky")
    first = await _order(env, buyer)
    assert (
        await buyer.post(PAY.format(first["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 200
    assert calls == ["gwa", "gwb"]
    # a (priority 10) is healthy again, but b worked last time: sticky tries b first
    behaviour["gwa"] = None
    second = await _order(env, buyer)
    assert (
        await buyer.post(PAY.format(second["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 200
    assert calls[2:] == ["gwb"]
    listing = (await admin.get(GW)).json()
    assert listing["candidates"][0] == b[0]
    assert {i["id"]: i["in_use"] for i in listing["items"]} == {a[0]: False, b[0]: True}
    # fixed priority mode goes back to a
    assert (await admin.put(f"{GW}/routing", json={"mode": "priority"})).status_code == 200
    third = await _order(env, buyer)
    assert (
        await buyer.post(PAY.format(third["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 200
    assert calls[3:] == ["gwa"]
    assert (await admin.get(GW)).json()["candidates"][0] == a[0]


async def test_disabled_gateway_is_skipped_but_still_settles_its_attempt(env, monkeypatch):
    admin, buyer, a, b, _calls = await _setup(env, monkeypatch, {"gwa": None, "gwb": None})
    order = await _order(env, buyer)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 200
    (att,) = await _attempts(env)
    assert str(att.provider_id) == a[0]
    remote, key_a = att.gateway_ref, a[1]
    # the admin disables a and moves b first, after the buyer already holds a's QR
    assert (await admin.patch(f"{GW}/{a[0]}", json={"enabled": False})).status_code == 200
    assert (await admin.patch(f"{GW}/{b[0]}", json={"priority": 1})).status_code == 200

    seen = []

    async def query(self, no):
        seen.append((self.config.pid, no))
        return QueryResult(True, True, Decimal(order["amount"]), "trade-a-1", remote)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    params = {
        "pid": "gwa",
        "out_trade_no": remote,
        "trade_no": "trade-a-1",
        "money": order["amount"],
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = epay.sign(params, key_a)
    assert (await env.client(csrf=False).get(NOTIFY, params=params)).text == "success"
    assert seen == [("gwa", remote)]
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    # b's key cannot validate a's notify (original provider wins)
    params2 = {**params, "trade_no": "trade-a-2"}
    params2["sign"] = epay.sign({k: v for k, v in params2.items() if k != "sign"}, b[1])
    assert (await env.client(csrf=False).get(NOTIFY, params=params2)).text != "success"


async def test_cancel_or_resolve_use_the_attempts_own_gateway(env, monkeypatch):
    admin, buyer, a, _b, _calls = await _setup(env, monkeypatch, {"gwa": None, "gwb": None})
    order = await _order(env, buyer)
    assert (
        await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 200
    assert (await admin.patch(f"{GW}/{a[0]}", json={"enabled": False})).status_code == 200
    pids = []

    async def query(self, no):
        pids.append(self.config.pid)
        return QueryResult(False, False, None, "", "")

    monkeypatch.setattr(ZPayClient, "query_order", query)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert pids == ["gwa"]


# ---------------------------------------------------------------------------- admin API
async def test_new_gateway_is_disabled_until_enabled(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwn", enable=False)
    (item,) = (await admin.get(GW)).json()["items"]
    assert item["enabled"] is False and item["priority"] == 100 and item["in_use"] is False
    assert (await admin.get(GW)).json()["candidates"] == []
    assert (await admin.patch(f"{GW}/{ref}", json={"enabled": True})).status_code == 200
    assert (await admin.get(GW)).json()["candidates"] == [ref]


async def test_routing_endpoints_are_admin_only_audited_and_tenant_scoped(env, monkeypatch):
    admin, _ = await env.login("admin")
    a, _ = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)
    b, _ = await _add_gateway(env, admin, monkeypatch, "gwb", priority=20)
    assert (await admin.put(f"{GW}/order", json={"ids": [b, a]})).status_code == 200
    items = (await admin.get(GW)).json()["items"]
    assert [i["id"] for i in items] == [b, a] and [i["priority"] for i in items] == [10, 20]
    # the list must name every gateway exactly once
    assert (await admin.put(f"{GW}/order", json={"ids": [a]})).status_code in (400, 422)
    assert (await admin.put(f"{GW}/order", json={"ids": [a, a]})).status_code in (400, 422)
    assert (await admin.put(f"{GW}/routing", json={"mode": "bogus"})).status_code == 422
    assert (await admin.patch(f"{GW}/{a}", json={})).status_code in (400, 422)
    assert (await admin.put(f"{GW}/routing", json={"mode": "priority"})).status_code == 200
    for action in (
        "payment.gateway_updated",
        "payment.gateway_reordered",
        "payment.routing_changed",
    ):
        assert (
            await env.scalar(
                "SELECT count(*) FROM audit_log WHERE action=:a AND tenant_id=:t",
                a=action,
                t=str(env.tenant_id),
            )
            >= 1
        ), action
    buyer, _ = await env.login()
    agent, _ = await env.login("agent")
    for who in (buyer, agent):
        assert (await who.patch(f"{GW}/{a}", json={"enabled": False})).status_code in (401, 403)
        assert (await who.put(f"{GW}/routing", json={"mode": "sticky"})).status_code in (401, 403)
        assert (await who.put(f"{GW}/order", json={"ids": [a, b]})).status_code in (401, 403)
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.patch(f"{GW}/{a}", json={"priority": 1})).status_code in (401, 404)
    assert (await foreign.delete(f"{GW}/{a}")).status_code in (401, 404)
    assert (await foreign.post(f"{GW}/{a}/test-payment", json={})).status_code in (401, 404)
    assert (await foreign.put(f"{GW}/order", json={"ids": [a, b]})).status_code in (401, 404, 422)
    assert (await _gw_row(env, a)).priority == 20  # untouched by the foreign admin


async def test_delete_refused_after_use_but_allowed_when_untouched(env, monkeypatch):
    admin, buyer, a, b, _calls = await _setup(env, monkeypatch, {"gwa": None, "gwb": None})
    order = await _order(env, buyer)
    assert (
        await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 200
    assert (await admin.delete(f"{GW}/{a[0]}")).status_code == 409
    assert (await admin.delete(f"{GW}/{b[0]}")).status_code == 200
    assert [i["id"] for i in (await admin.get(GW)).json()["items"]] == [a[0]]


# --------------------------------------------------------------------------- test payment
def _stub_test_gateway(monkeypatch, *, query=None):
    async def create(self, order_no, name, amount, ip):
        assert order_no.startswith("T") and len(order_no) == 17
        return {"kind": "qr", "payload": "https://qr.alipay.com/" + order_no}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    if query:
        monkeypatch.setattr(ZPayClient, "query_order", query)


def _notify_params(test, key, *, money=None, trade_no="TT-1", pid="gwa"):
    params = {
        "pid": pid,
        "out_trade_no": test["out_trade_no"],
        "trade_no": trade_no,
        "money": money or test["amount"],
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = epay.sign(params, key)
    return params


async def test_test_payment_create_notify_paid_idempotent_and_isolated(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        return QueryResult(True, True, Decimal("0.01"), "TT-1", no)

    _stub_test_gateway(monkeypatch, query=query)
    r = await admin.post(f"{GW}/{ref}/test-payment", json={})
    assert r.status_code == 200, r.text
    test = r.json()
    assert test["status"] == "pending" and test["amount"] == "0.01"
    assert test["qr_content"].startswith("https://qr.alipay.com/T") and test["seconds_left"] > 0
    assert test["qr_svg"].lstrip().startswith("<svg") and "<script" not in test["qr_svg"]
    assert test["out_trade_no"].startswith("T") and test["gateway_label"] == "gw gwa"
    params = _notify_params(test, key)
    for _ in range(2):  # the second notify is idempotent
        assert (await env.client(csrf=False).get(NOTIFY, params=params)).text == "success"
    got = (await admin.get(f"{GW}/test-payments/{test['id']}")).json()
    assert got["status"] == "paid" and got["trade_no"] == "TT-1"
    # test money never touches orders, payments, outbox, stock or the sales ledger
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic = 'order.paid'") == 0
    rows = await env.scalar(
        "SELECT count(*) FROM ledger_entries WHERE tenant_id=:t", t=str(env.tenant_id)
    )
    assert rows == 2
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='test_receipts'"
            " AND order_id IS NULL"
        )
        == -1
    )
    assert (
        await env.scalar(
            "SELECT sum(amount_minor) FROM ledger_entries WHERE account='gateway_clearing:zpay'"
        )
        == 1
    )
    assert (
        await env.scalar("SELECT count(*) FROM ledger_entries WHERE account='sales_revenue'") == 0
    )
    assert (await _gw_row(env, ref)).last_ok_at is not None  # a paid test sets last_ok
    listed = (await admin.get(f"{GW}/test-payments")).json()["items"]
    assert [(i["id"], i["status"]) for i in listed] == [(test["id"], "paid")]


async def test_test_payment_rejects_forged_or_wrong_amount_notifies(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        return QueryResult(True, False, Decimal("0.01"), "", no)

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    client = env.client(csrf=False)
    bad = _notify_params(test, "x" * 32)
    assert (await client.get(NOTIFY, params=bad)).text != "success"
    wrong_amount = _notify_params(test, key, money="0.02")
    assert (await client.get(NOTIFY, params=wrong_amount)).text == "success"  # acknowledged...
    wrong_pid = _notify_params(test, key, pid="other")
    assert (await client.get(NOTIFY, params=wrong_pid)).text != "success"
    assert (await admin.get(f"{GW}/test-payments/{test['id']}")).json()["status"] == "pending"
    assert await env.scalar("SELECT count(*) FROM ledger_entries") == 0  # ...but never settled


async def test_test_payment_requires_requery_confirmation(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        return QueryResult(True, False, Decimal("0.01"), "", no)  # gateway says unpaid

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    await env.client(csrf=False).get(NOTIFY, params=_notify_params(test, key))
    assert (await admin.get(f"{GW}/test-payments/{test['id']}")).json()["status"] == "pending"
    assert await env.scalar("SELECT count(*) FROM ledger_entries") == 0


async def test_test_payment_poll_requeries_at_most_every_3s(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, _key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)
    n = {"q": 0}

    async def query(self, no):
        n["q"] += 1
        return QueryResult(True, n["q"] >= 2, Decimal("0.01"), "TT-9", no)

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={"amount": "0.01"})).json()
    url = f"{GW}/test-payments/{test['id']}"
    assert (await admin.get(url)).json()["status"] == "pending"
    assert (await admin.get(url)).json()["status"] == "pending"  # throttled, no new query
    assert n["q"] == 1
    async with env.owner_session() as db:
        row = await db.scalar(select(PaymentTest).where(PaymentTest.id == uuid.UUID(test["id"])))
        row.checked_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await db.commit()
    assert (await admin.get(url)).json()["status"] == "paid" and n["q"] == 2


@pytest.mark.parametrize("amount", ["0", "0.00", "10.01", "-1", "0.001", "abc", "100"])
async def test_test_payment_amount_bounds(env, monkeypatch, amount):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")
    _stub_test_gateway(monkeypatch)
    r = await admin.post(f"{GW}/{ref}/test-payment", json={"amount": amount})
    assert r.status_code in (400, 422), r.text


async def test_test_payment_bounds_accept_edges_and_rate_limit(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")
    _stub_test_gateway(monkeypatch)
    assert (
        await admin.post(f"{GW}/{ref}/test-payment", json={"amount": "10.00"})
    ).status_code == 200
    for _ in range(9):
        assert (
            await admin.post(f"{GW}/{ref}/test-payment", json={"amount": "0.01"})
        ).status_code == 200
    assert (await admin.post(f"{GW}/{ref}/test-payment", json={})).status_code == 429


async def test_test_payment_is_admin_session_only_and_tenant_scoped(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")
    _stub_test_gateway(monkeypatch)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    buyer, _ = await env.login()
    assert (await buyer.post(f"{GW}/{ref}/test-payment", json={})).status_code in (401, 403)
    assert (await buyer.get(f"{GW}/test-payments/{test['id']}")).status_code in (401, 403)
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.get(f"{GW}/test-payments/{test['id']}")).status_code in (401, 404)
    # service level (the session/host check above already refuses): the tenant filter holds alone
    from app.core.errors import NotFound
    from app.modules.payments import gateway_tests

    async with env.api_session(env.other_tenant_id) as db:
        assert await gateway_tests.list_recent(db, env.other_tenant_id) == []
        with pytest.raises(NotFound):
            await gateway_tests.refresh(
                db, env.res, tid=env.other_tenant_id, test_id=uuid.UUID(test["id"])
            )
        with pytest.raises(NotFound):
            await gateway_tests.create(
                db,
                env.res,
                tid=env.other_tenant_id,
                admin_id=uuid.uuid4(),
                admin_role="admin",
                gateway_id=uuid.UUID(ref),
                amount_minor=1,
                ip=None,
            )
    # a foreign tenant's notify for this test reference is not accepted either
    other_host = env.client(csrf=False)
    assert (
        await other_host.get(NOTIFY, params={"out_trade_no": test["out_trade_no"]})
    ).text != "success"
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.test_created' AND tenant_id=:t",
            t=str(env.tenant_id),
        )
        == 1
    )


async def test_test_payment_gateway_failure_is_reported_not_raised(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")

    async def create(self, *a):
        raise ZPayRefused("merchant disabled")

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    r = await admin.post(f"{GW}/{ref}/test-payment", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed" and body["reason"] == "gateway_refused"
    assert body["qr_content"] is None and "merchant disabled" in body["message"]


# ----------------------------------------------------------------------- audit follow-ups
@pytest.mark.parametrize(
    "raw", ["1E+999990", "1E+999998", "sNaN", "NaN", "-1", "10.01", "0", "1e1"]
)
def test_parse_amount_rejects_hostile_forms_without_arithmetic(raw):
    from app.core.errors import Validation
    from app.modules.payments.gateway_tests import parse_amount

    with pytest.raises(Validation):
        parse_amount(raw)


def test_parse_amount_accepts_the_range():
    from app.modules.payments.gateway_tests import parse_amount

    got = [parse_amount(x) for x in ("0.01", "1", "9.5", "10", "10.00")]
    assert got == [1, 100, 950, 1000, 1000]


@pytest.mark.parametrize("raw", ["1E+999990", "sNaN", "NaN"])
async def test_test_payment_endpoint_answers_422_for_hostile_amounts(env, monkeypatch, raw):
    admin, _ = await env.login("admin")
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")
    _stub_test_gateway(monkeypatch)
    assert (await admin.post(f"{GW}/{ref}/test-payment", json={"amount": raw})).status_code == 422


def test_refused_attempts_are_rechecked_by_the_reconciler():
    from app.modules.payments.reconcile import RECHECK_REASONS

    assert "gateway_refused" in RECHECK_REASONS and "gateway_unreachable" in RECHECK_REASONS


async def test_concurrent_pays_open_at_most_one_live_gateway_order(env, monkeypatch):
    import asyncio

    a_failed = asyncio.Event()
    calls: list[str] = []

    async def create(self, order_no, *a):
        calls.append(self.config.pid)
        if self.config.pid == "gwa":
            a_failed.set()
            raise ZPayUnreachable("gateway unreachable")
        await asyncio.sleep(0.4)  # gateway b is slow: the window the second request tries to use
        return {"kind": "qr", "payload": "https://qr.alipay.com/b"}

    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)
    await _add_gateway(env, admin, monkeypatch, "gwb", priority=20)
    await admin.put(f"{GW}/routing", json={"mode": "priority"})
    monkeypatch.setattr(ZPayClient, "create_payment", create)
    order = await _order(env, buyer)
    url = PAY.format(order["id"])

    async def second():
        await a_failed.wait()
        await asyncio.sleep(0.05)
        return await buyer.post(url, json={"channel": "zpay_alipay"})

    r1, r2 = await asyncio.gather(buyer.post(url, json={"channel": "zpay_alipay"}), second())
    assert r1.status_code == 200, r1.text
    assert r2.status_code in (200, 503), r2.text
    assert calls.count("gwb") == 1
    live = [x for x in await _attempts(env) if x.status in ("preparing", "ready", "unknown")]
    assert len(live) == 1


async def test_sticky_flip_by_another_success_does_not_supersede_a_live_qr(env, monkeypatch):
    behaviour = {"gwa": None, "gwb": None}
    _admin, buyer, a, _b, calls = await _setup(env, monkeypatch, behaviour, mode="sticky")
    first = await _order(env, buyer)
    r = await buyer.post(PAY.format(first["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 200
    assert calls == ["gwa"]
    behaviour["gwa"] = ZPayUnreachable("x")  # another payment fails over to b: b is now sticky
    other = await _order(env, buyer)
    r = await buyer.post(PAY.format(other["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 200
    assert calls == ["gwa", "gwa", "gwb"]
    # the first buyer comes back: the live QR at a (still enabled) is kept, nothing new is created
    again = await buyer.post(PAY.format(first["id"]), json={"channel": "zpay_alipay"})
    assert again.status_code == 200
    assert calls == ["gwa", "gwa", "gwb"]
    mine = [x for x in await _attempts(env) if x.local_ref == first["order_no"]]
    assert [(x.status, str(x.provider_id)) for x in mine] == [("ready", a[0])]


async def test_no_enabled_gateway_never_falls_back_to_the_environment_key(env, monkeypatch):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    ref, _ = await _add_gateway(env, admin, monkeypatch, "gwa")
    await admin.patch(f"{GW}/{ref}", json={"enabled": False})
    _stub_create(monkeypatch, {"gwa": None})
    monkeypatch.setattr(env.res.settings, "zpay_pid", "envpid", raising=False)
    monkeypatch.setattr(env.res.settings, "zpay_key", "envkey" * 4, raising=False)
    order = await _order(env, buyer)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable", r.text
    assert await _attempts(env) == []


async def test_test_payment_ambiguous_error_stays_pending_and_a_notify_still_records_it(
    env, monkeypatch
):
    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def create(self, *a):
        raise ZPayQueryError("query failed: ReadTimeout")

    async def query(self, no):
        return QueryResult(True, True, Decimal("0.01"), "TT-1", no)

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    monkeypatch.setattr(ZPayClient, "query_order", query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    assert test["status"] == "pending" and test["reason"] == "gateway_unknown"
    assert test["qr_svg"] is None
    got = await env.client(csrf=False).get(NOTIFY, params=_notify_params(test, key))
    assert got.text == "success"
    assert (await admin.get(f"{GW}/test-payments/{test['id']}")).json()["status"] == "paid"


async def test_gateway_with_paid_test_payments_cannot_be_deleted(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        return QueryResult(True, True, Decimal("0.01"), "TT-1", no)

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    await env.client(csrf=False).get(NOTIFY, params=_notify_params(test, key))
    assert (await admin.delete(f"{GW}/{ref}")).status_code == 409


async def test_concurrent_duplicate_test_notify_posts_the_ledger_once(env, monkeypatch):
    import asyncio

    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        await asyncio.sleep(0.1)
        return QueryResult(True, True, Decimal("0.01"), "TT-1", no)

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    params = _notify_params(test, key)
    rs = await asyncio.gather(
        *(env.client(csrf=False).get(NOTIFY, params=params) for _ in range(4))
    )
    assert all(r.status_code == 200 for r in rs)
    assert (
        await env.scalar("SELECT count(*) FROM ledger_entries WHERE account='test_receipts'") == 1
    )
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.test_paid'") == 1


async def test_cross_tenant_notify_of_a_test_reference_is_rejected(env, monkeypatch):
    from app.modules.payments.notify import handle_notify

    admin, _ = await env.login("admin")
    ref, key = await _add_gateway(env, admin, monkeypatch, "gwa", priority=10)

    async def query(self, no):
        return QueryResult(True, True, Decimal("0.01"), "TT-1", no)

    _stub_test_gateway(monkeypatch, query=query)
    test = (await admin.post(f"{GW}/{ref}/test-payment", json={})).json()
    params = _notify_params(test, key)  # correctly signed with the owner tenant's gateway key
    async with env.api_session(env.other_tenant_id) as db:
        outcome = await handle_notify(db, env.res, env.other_tenant_id, params, None)
    assert outcome.reply != "success"
    assert await env.scalar("SELECT status FROM payment_tests") == "pending"
    assert await env.scalar("SELECT count(*) FROM ledger_entries") == 0
