"""review-v9 (#63): gateway-unreachable is a clean failure, admin resolve, self-test/health,
reconciler backoff, checkout member channel, expedite, announcement acknowledgement."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text

from app.models import PaymentAttempt
from app.modules.admin import payment_ops
from app.modules.payments import reconcile
from app.payments.zpay import QueryResult, ZPayClient, ZPayConfig, ZPayQueryError, ZPayUnreachable
from tests.helpers import buy, pay_order
from tests.test_commerce_v4 import checkout
from tests.test_epay_gateways_v6 import _gateway

PAY = "/api/v1/orders/{}/pay"


async def _pay_via_db_gateway(env, monkeypatch, create):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    ref, key = await _gateway(env, admin, monkeypatch)
    monkeypatch.setattr(ZPayClient, "create_payment", create)
    product = await env.product(mode="manual")
    order = await buy(buyer, product)
    return admin, buyer, order, ref, key


async def _attempts(env):
    async with env.api_session() as db:
        return list(
            await db.scalars(
                select(PaymentAttempt).where(PaymentAttempt.tenant_id == env.tenant_id)
            )
        )


# ------------------------------------------------------------------------------ §1 classification
async def test_transport_errors_classified():
    def cfg():
        return ZPayConfig("p", "k" * 16, "https://g.example", "https://g.example", "n", "r")

    def make(exc):
        def handler(request):
            raise exc(request=request, message="x") if False else exc("x", request=request)

        return handler

    for exc, unreachable in (
        (httpx.ConnectError, True),
        (httpx.ConnectTimeout, True),
        (httpx.ReadTimeout, False),
        (httpx.RemoteProtocolError, False),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(make(exc))) as http:
            client = ZPayClient(cfg(), http)
            with pytest.raises(ZPayQueryError) as caught:
                await client.create_payment("GEXAMPLE000001", "i", Decimal("1.00"), "1.1.1.1")
            assert isinstance(caught.value, ZPayUnreachable) is unreachable, exc


async def test_connect_failure_fails_attempt_and_payable_stays_payable(env, monkeypatch):
    calls = {"n": 0}

    async def create(self, *a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ZPayUnreachable("gateway unreachable")
        return {"kind": "qr", "payload": "https://qr.alipay.com/example"}

    _admin, buyer, order, _ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "channel_unreachable", r.text
    (att,) = await _attempts(env)
    assert att.status == "failed" and att.data == {"reason": "gateway_unreachable"}
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.attempt_failed' AND tenant_id=:t",
            t=str(env.tenant_id),
        )
        == 1
    )
    view = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert view["status"] == "pending_payment"
    # immediately payable again (the same method works once the gateway is back)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and r.json()["pay_url"].startswith("/pay/epay/"), r.text


async def test_connect_failure_then_cancel_succeeds(env, monkeypatch):
    async def create(self, *a):
        raise ZPayUnreachable("gateway unreachable")

    _admin, buyer, order, _ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


async def test_read_timeout_stays_unknown(env, monkeypatch):
    async def create(self, *a):
        raise ZPayQueryError("query failed: ReadTimeout")

    _admin, buyer, order, _ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
    (att,) = await _attempts(env)
    assert att.status == "unknown"
    # unchanged behaviour: pay and cancel stay blocked until the outcome is resolved
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503


# --------------------------------------------------------------------------- §1.3 admin resolve
async def test_admin_resolve_unknown_attempt(env, monkeypatch):
    async def create(self, *a):
        raise ZPayQueryError("query failed: ReadTimeout")

    async def query(self, no):
        raise ZPayQueryError("down")

    admin, buyer, order, _ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    monkeypatch.setattr(ZPayClient, "query_order", query)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 503  # unknown evidence blocks the cancel
    (att,) = await _attempts(env)
    url = f"/api/v1/admin/payment-attempts/{att.id}/resolve"
    body = {"outcome": "not_paid", "note": "Checked the gateway console: no charge exists"}

    # admin only, tenant scoped
    assert (await buyer.post(url, json=body)).status_code == 403
    agent, _ = await env.login("agent")
    assert (await agent.post(url, json=body)).status_code == 403
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.post(url, json=body)).status_code in (401, 404)
    assert (await admin.post(url, json={**body, "note": "short"})).status_code == 422
    assert (await admin.post(url, json={**body, "outcome": "paid"})).status_code == 422
    assert (await _attempts(env))[0].status == "unknown"

    r = await admin.post(url, json=body)
    assert r.status_code == 200 and r.json()["status"] == "failed", r.text
    (att,) = await _attempts(env)
    assert att.data["resolved_note"] == body["note"] and att.data["resolved_by"]
    assert (
        await env.scalar("SELECT severity FROM audit_log WHERE action='payment.attempt_resolved'")
        == "warning"
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM operation_events WHERE action='payment.attempt_resolved'"
        )
        == 1
    )
    # only unknown/preparing attempts can be resolved
    assert (await admin.post(url, json=body)).status_code == 409
    # the cancel now succeeds without any gateway evidence
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text


# -------------------------------------------------------------------------------- §1.4 reconcile
async def _unknown_attempt(env, *, created_ago: timedelta, checked_ago: timedelta | None):
    async with env.owner_session() as db:
        now = datetime.now(timezone.utc)
        await db.execute(
            text(
                "INSERT INTO payment_attempts (id, tenant_id, local_ref, gateway_ref, provider_id,"
                " channel, amount_minor, currency, status, data, attempts, created_at, updated_at,"
                " checked_at) VALUES (:id, :t, :ref, :gref, :pid, 'zpay_alipay', 100, 'CNY',"
                " 'unknown', '{}'::jsonb, 0, :c, :c, :k)"
            ),
            {
                "id": uuid.uuid4(),
                "t": env.tenant_id,
                "ref": "G" + uuid.uuid4().hex[:12].upper(),
                "gref": uuid.uuid4().hex,
                "pid": await _any_gateway(db, env),
                "c": now - created_ago,
                "k": now - checked_ago if checked_ago is not None else None,
            },
        )
        await db.commit()


async def _admin_id(db, env):
    from app.models import User

    return await db.scalar(select(User.id).where(User.tenant_id == env.tenant_id).limit(1))


async def _any_gateway(db, env):
    from app.models import PaymentGateway

    gid = await db.scalar(
        select(PaymentGateway.id).where(PaymentGateway.tenant_id == env.tenant_id)
    )
    if gid is None:
        gid = uuid.uuid4()
        db.add(
            PaymentGateway(
                id=gid,
                tenant_id=env.tenant_id,
                label="x",
                gateway="https://gateway.example",
                merchant_id="m1",
                mode="qr",
                key_ciphertext=b"x",
                created_by=(await _admin_id(db, env)),
            )
        )
        await db.flush()
    return gid


async def test_reconciler_backs_off_and_alerts_after_24h(env, monkeypatch):
    await env.user("admin")
    seen: list[str] = []

    async def fake_all(db, res, tid, no, sink=None):
        seen.append(no)

    monkeypatch.setattr(reconcile, "requery_all", fake_all)
    await _unknown_attempt(env, created_ago=timedelta(minutes=20), checked_ago=timedelta(minutes=5))
    await _unknown_attempt(
        env, created_ago=timedelta(minutes=20), checked_ago=timedelta(seconds=30)
    )
    await _unknown_attempt(env, created_ago=timedelta(hours=5), checked_ago=timedelta(minutes=10))
    await _unknown_attempt(env, created_ago=timedelta(hours=5), checked_ago=timedelta(hours=2))
    await reconcile.run(env.res)
    # first hour: every 2 minutes; afterwards hourly
    assert len(seen) == 2

    # 24 h unresolved: one high-severity event + no further polling
    seen.clear()
    await _unknown_attempt(env, created_ago=timedelta(hours=25), checked_ago=timedelta(hours=3))
    for _ in range(2):
        await reconcile.run(env.res)
    count = await env.scalar(
        "SELECT count(*) FROM operation_events WHERE action='payment.attempt_stale_unknown'"
        " AND severity='critical'"
    )
    assert count == 1
    assert len(seen) == 0 or all(n for n in seen)  # the alerted one is not re-queried
    async with env.api_session() as db:
        stale = await db.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.tenant_id == env.tenant_id,
                PaymentAttempt.data["stale_alerted"].as_string().is_not(None),
            )
        )
        assert stale is not None and stale.local_ref not in seen


# ---------------------------------------------------------------------------- §2 self-test
async def test_gateway_self_test_and_health(env, monkeypatch):
    admin, _ = await env.login("admin")
    ref, key = await _gateway(env, admin, monkeypatch)
    url = f"/api/v1/admin/payment-gateways/{ref}/test"

    async def connect(host, port):
        return 12

    monkeypatch.setattr(payment_ops, "tcp_connect_ms", connect)

    async def not_found(self, endpoint, params, *, post=False):
        return {"code": -1, "msg": "order not found " + params["key"]}

    monkeypatch.setattr(ZPayClient, "_json", not_found)
    r = await admin.post(url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["reachable"] and body["latency_ms"] == 12
    assert "order not found" in body["message"]
    assert key not in r.text  # never echo keys
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.gateway_tested'")
        == 1
    )
    h = (await admin.get("/api/v1/admin/payment-gateways-health")).json()
    assert h["gateways"][ref]["status"] == "healthy"

    async def down(self, endpoint, params, *, post=False):
        raise ZPayUnreachable("gateway unreachable")

    monkeypatch.setattr(ZPayClient, "_json", down)
    r = await admin.post(url)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["reachable"] is False
    assert key not in r.text
    h = (await admin.get("/api/v1/admin/payment-gateways-health")).json()
    assert h["gateways"][ref]["status"] == "unreachable"

    async def refused(host, port):
        raise OSError("refused")

    monkeypatch.setattr(payment_ops, "tcp_connect_ms", refused)
    r = await admin.post(url)
    assert r.json()["ok"] is False and r.json()["stage"] == "connect"

    # admin only, tenant isolated
    buyer, _ = await env.login()
    assert (await buyer.post(url)).status_code == 403
    foreign, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await foreign.post(url)).status_code in (401, 404)
    assert (await foreign.get("/api/v1/admin/payment-gateways-health")).status_code == 401


async def test_health_degrades_on_unknown_attempts(env, monkeypatch):
    async def create(self, *a):
        raise ZPayQueryError("query failed: ReadTimeout")

    admin, buyer, order, ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    h = (await admin.get("/api/v1/admin/payment-gateways-health")).json()
    assert h["gateways"][ref]["status"] == "degraded"


async def test_unreachable_attempt_marks_channel_unreachable(env, monkeypatch):
    async def create(self, *a):
        raise ZPayUnreachable("gateway unreachable")

    admin, buyer, order, ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    h = (await admin.get("/api/v1/admin/payment-gateways-health")).json()
    assert h["gateways"][ref]["status"] == "unreachable"


# -------------------------------------------------------------------------- §3 channel sync
async def test_checkout_members_carry_channel_while_pending(env, gw):
    p = await env.product(mode="manual", price=500, floor=100)
    buyer, _ = await env.login()
    admin, _ = await env.login("admin")
    r = await checkout(buyer, [{"product_id": str(p.id), "qty": 1}] * 2)
    co = r.json()["checkout"]
    r = await buyer.post(
        f"/api/v1/checkouts/{co['checkout_no']}/pay", json={"channel": "zpay_alipay"}
    )
    assert r.status_code == 200, r.text
    orders = (await buyer.get("/api/v1/orders")).json()["items"]
    assert len(orders) == 2 and all(o["channel"] == "zpay_alipay" for o in orders)
    detail = await admin.get(f"/api/v1/admin/orders/{orders[0]['id']}")
    assert detail.json()["channel"] == "zpay_alipay"
    assert (
        await env.scalar(
            "SELECT count(*) FROM orders WHERE checkout_id IS NOT NULL AND channel IS NULL"
        )
        == 0
    )


# ----------------------------------------------------------------------------------- §6 expedite
async def _paid_manual(env):
    buyer, _ = await env.login()
    p = await env.product(mode="manual", price=500, floor=100)
    order = await buy(buyer, p)
    await pay_order(env, buyer, order)
    return buyer, order


async def _age_expedites(env, minutes: int):
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE order_events SET created_at = created_at - make_interval(mins => :m)"
                " WHERE reason='expedite'"
            ),
            {"m": minutes},
        )
        await db.commit()


async def test_expedite_limits_and_isolation(env):
    buyer, order = await _paid_manual(env)
    url = f"/api/v1/orders/{order['id']}/expedite"
    r = await buyer.post(url)
    assert r.status_code == 200, r.text
    # a system message lands in the order conversation for staff
    assert (
        await env.scalar(
            "SELECT count(*) FROM messages WHERE payload->>'event'='delivery_expedite'"
        )
        == 1
    )
    r = await buyer.post(url)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    for _ in range(2):
        await _age_expedites(env, 31)
        r = await buyer.post(url)
        assert r.status_code in (200, 429)
    await _age_expedites(env, 31)
    # three reminders is the cap per order, whatever the cooldown
    assert await env.scalar("SELECT count(*) FROM order_events WHERE reason='expedite'") == 3
    assert (await buyer.post(url)).status_code == 429
    other, _ = await env.login()
    assert (await other.post(url)).status_code == 404
    foreign, _ = await env.login(tenant_id=env.other_tenant_id)
    assert (await foreign.post(url)).status_code in (401, 404)


async def test_expedite_only_for_paid_manual(env):
    buyer, _ = await env.login()
    p = await env.product(mode="manual", price=500, floor=100)
    order = await buy(buyer, p)  # unpaid
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/expedite")).status_code == 409
    card = await env.product(mode="auto_card", price=500, floor=100)
    await env.import_codes(card, ["CODE-1"])
    o2 = await buy(buyer, card)
    await pay_order(env, buyer, o2)
    assert (await buyer.post(f"/api/v1/orders/{o2['id']}/expedite")).status_code == 409


# ------------------------------------------------------------------------------- §8 announcement
async def test_announcement_ack_follows_user_and_is_tenant_scoped(env):
    admin, _ = await env.login("admin")
    r = await admin.put(
        "/api/v1/admin/site",
        json={
            "announcement": {
                "enabled": True,
                "display": "both",
                "require_ack": True,
                "title_i18n": {"en": "Notice", "zh-CN": "公告"},
                "body_i18n": {"en": "Read me", "zh-CN": "请阅读"},
            },
            "after_sales_default_hours": 48,
            "auto_confirm_hours": {"manual": 10, "auto_card": 5},
            "support_hours": {"en": "9-18", "zh-CN": "9-18"},
        },
    )
    assert r.status_code == 200, r.text
    a = r.json()["announcement"]
    assert a["display"] == "both" and a["require_ack"] is True and a["version"] >= 1
    buyer, _ = await env.login()
    site = (await buyer.get("/api/v1/site")).json()["announcement"]
    assert site["require_ack"] is True and site["display"] == "both"
    assert (await buyer.get("/api/v1/me/announcement-ack")).json() == {"version": 0}
    r = await buyer.post("/api/v1/me/announcement-ack", json={"version": site["version"]})
    assert r.status_code == 200 and r.json() == {"version": site["version"]}
    assert (
        await buyer.post("/api/v1/me/announcement-ack", json={"version": 999})
    ).status_code == 422
    # another user (and another tenant's user) is unaffected
    other, _ = await env.login()
    assert (await other.get("/api/v1/me/announcement-ack")).json() == {"version": 0}
    foreign, _ = await env.login(tenant_id=env.other_tenant_id)
    assert (await foreign.get("/api/v1/me/announcement-ack")).status_code in (200, 401)
    assert (
        await foreign.post("/api/v1/me/announcement-ack", json={"version": 1})
    ).status_code == 401


_ = (QueryResult,)
