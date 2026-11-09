"""review-v9 payment-audit follow-ups: migration backfill under FORCE RLS (H1), release race (M2),
failed-attempt safety net (M1), notify for a failed attempt (M3), expedite cap."""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.modules.payments import reconcile
from app.payments import epay
from app.payments.zpay import QueryResult, ZPayClient, ZPayUnreachable
from tests.test_commerce_v4 import checkout
from tests.test_payments_v9 import (
    PAY,
    _age_expedites,
    _attempts,
    _paid_manual,
    _pay_via_db_gateway,
    _unknown_attempt,
)

BODY = {"outcome": "not_paid", "note": "Checked the gateway console: no charge exists"}


async def test_member_channel_backfill_runs_under_force_rls(env):
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0017_payments_v9.py"
    spec = importlib.util.spec_from_file_location("mig0017", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    p = await env.product(mode="manual", price=500, floor=100)
    buyer, _ = await env.login()
    r = await checkout(buyer, [{"product_id": str(p.id), "qty": 1}] * 2)
    co_no = r.json()["checkout"]["checkout_no"]
    async with env.owner_session() as db:  # a pending checkout from before v9
        await db.execute(
            text("UPDATE checkouts SET channel='zpay_alipay' WHERE checkout_no=:n"), {"n": co_no}
        )
        await db.execute(text("UPDATE orders SET channel=NULL WHERE checkout_id IS NOT NULL"))
        await db.commit()
    stmts: list[str] = []
    mig.backfill(stmts.append)
    # the migration role runs WITHOUT a tenant GUC, under FORCE RLS
    async with env.s.owner_engine.connect() as conn:
        for sql in stmts:
            await conn.execute(text(sql))
        await conn.commit()
    assert (
        await env.scalar(
            "SELECT count(*) FROM orders WHERE checkout_id IS NOT NULL AND channel='zpay_alipay'"
        )
        == 2
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM pg_class WHERE relname IN ('orders','checkouts')"
            " AND relforcerowsecurity"
        )
        == 2
    )


async def test_prepare_race_with_staff_release_keeps_failed(env, monkeypatch):
    async def create(self, *a):
        async with env.owner_session() as db:  # staff release it while the call is in flight
            await db.execute(
                text(
                    "UPDATE payment_attempts SET status='failed',"
                    ' data=\'{"reason":"resolved_not_paid"}\'::jsonb'
                )
            )
            await db.commit()
        return {"kind": "qr", "payload": "https://qr.alipay.com/example"}

    _admin, buyer, order, _ref, _key = await _pay_via_db_gateway(env, monkeypatch, create)
    r = await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "channel_unreachable", r.text
    assert "pay_url" not in r.text
    (att,) = await _attempts(env)
    assert att.status == "failed" and att.data == {"reason": "resolved_not_paid"}


async def test_resolve_preparing_only_after_two_minutes(env):
    admin, _ = await env.login("admin")
    await _unknown_attempt(
        env, created_ago=timedelta(seconds=20), checked_ago=None, status="preparing"
    )
    await _unknown_attempt(
        env, created_ago=timedelta(minutes=5), checked_ago=None, status="preparing"
    )
    rows = sorted(await _attempts(env), key=lambda a: a.created_at, reverse=True)
    r = await admin.post(f"/api/v1/admin/payment-attempts/{rows[0].id}/resolve", json=BODY)
    assert r.status_code == 409
    r = await admin.post(f"/api/v1/admin/payment-attempts/{rows[1].id}/resolve", json=BODY)
    assert r.status_code == 200


async def _failed_setup(env, monkeypatch):
    async def create(self, *a):
        raise ZPayUnreachable("gateway unreachable")

    _admin, buyer, order, _ref, key = await _pay_via_db_gateway(env, monkeypatch, create)
    assert (
        await buyer.post(PAY.format(order["id"]), json={"channel": "zpay_alipay"})
    ).status_code == 503
    (att,) = await _attempts(env)
    return buyer, order, att, key


def _paid_query(order, att, calls):
    async def query(self, no):
        calls.append(no)
        return QueryResult(True, True, Decimal(order["amount"]), "late-trade-1", no)

    return query


async def test_reconciler_rechecks_failed_attempt_pending_becomes_paid(env, monkeypatch):
    buyer, order, att, _key = await _failed_setup(env, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(ZPayClient, "query_order", _paid_query(order, att, calls))
    await reconcile.run(env.res)
    assert calls == [att.gateway_ref]
    assert (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"] == "paid"
    await reconcile.run(env.res)  # settled: not queried again
    assert calls == [att.gateway_ref]


async def test_recheck_window_and_cancelled_goes_to_manual_review(env, monkeypatch):
    buyer, order, att, _key = await _failed_setup(env, monkeypatch)
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})).status_code == 200
    calls: list[str] = []
    monkeypatch.setattr(ZPayClient, "query_order", _paid_query(order, att, calls))

    async def sql(q: str) -> None:
        async with env.owner_session() as db:
            await db.execute(text(q))
            await db.commit()

    await sql("UPDATE payment_attempts SET checked_at = now() - interval '2 minutes'")
    await reconcile.run(env.res)
    assert calls == []  # looked at 2 minutes ago: not due
    await sql(
        "UPDATE payment_attempts SET checked_at = NULL, created_at = now() - interval '25 hours'"
    )
    await reconcile.run(env.res)
    assert calls == []  # older than 24 h: given up
    await sql("UPDATE payment_attempts SET created_at = now()")
    await reconcile.run(env.res)
    assert calls == [att.gateway_ref]
    assert (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"] == "manual_review"


async def _notify(env, monkeypatch, cancel_first: bool) -> str:
    buyer, order, att, key = await _failed_setup(env, monkeypatch)
    if cancel_first:
        r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
        assert r.status_code == 200
    monkeypatch.setattr(ZPayClient, "query_order", _paid_query(order, att, []))
    params = {
        "pid": "fixture1",
        "out_trade_no": att.gateway_ref,
        "trade_no": "late-trade-1",
        "money": order["amount"],
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = epay.sign(params, key)
    resp = await env.client(csrf=False).get("/api/v1/pay/notify/zpay", params=params)
    assert resp.text == "success", resp.text
    return (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["status"]


async def test_notify_for_failed_attempt_pending_order_is_paid(env, monkeypatch):
    assert await _notify(env, monkeypatch, cancel_first=False) == "paid"


async def test_notify_for_failed_attempt_cancelled_order_goes_to_manual_review(env, monkeypatch):
    assert await _notify(env, monkeypatch, cancel_first=True) == "manual_review"


async def test_expedite_cap_is_a_clear_409(env):
    buyer, order = await _paid_manual(env)
    url = f"/api/v1/orders/{order['id']}/expedite"
    for _ in range(3):
        assert (await buyer.post(url)).status_code == 200
        await _age_expedites(env, 31)
    r = await buyer.post(url)
    assert r.status_code == 409 and r.json()["error"]["code"] == "expedite_limit"
