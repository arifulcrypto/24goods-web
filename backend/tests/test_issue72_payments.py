# ruff: noqa: F811
"""Issue #72 payment-path batch: B1 (USDT refusal), B3 (closed attempts), S12 (refunded order
second payment), A3 (redirect gateway health), A4 (dead delivery job alert + overview)."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from sqlalchemy import select, text

from app.core import notify_tg, safefetch
from app.models import Order, PaymentAttempt, PaymentGateway
from app.modules.operations import overview as ops_overview
from app.modules.payments import reconcile
from app.modules.payments.settle import apply_order_payment
from app.payments import bepusdt
from tests.conftest import PASSWORD
from tests.helpers import buy
from tests.test_usdt_v52 import (
    _order,
    _paid_order,
    _status,
    mock,  # noqa: F401
)


async def _attempts(env):
    async with env.api_session() as db:
        return list(await db.scalars(select(PaymentAttempt)))


# ---------------------------------------------------------------------------------------- B1
async def test_usdt_refusal_fails_attempt_and_retry_and_cancel_work(env, mock, monkeypatch):
    async def refuse(**kw):
        raise bepusdt.BepusdtRefused("no")

    real = env.res.bepusdt.create_transaction
    monkeypatch.setattr(env.res.bepusdt, "create_transaction", refuse)
    buyer, order, response = await _order(env)
    assert response.status_code == 503
    assert "refused" in response.text
    (att,) = await _attempts(env)
    assert att.status == "failed" and att.data["reason"] == "gateway_refused"
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.usdt_refused'")
    # healthy gateway: immediate retry succeeds
    monkeypatch.setattr(env.res.bepusdt, "create_transaction", real)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_trc20"})
    assert r.status_code == 200, r.text
    # and cancel succeeds (the failed attempt has no trade_id and must not block)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text


async def test_usdt_refusal_then_cancel_directly(env, mock, monkeypatch):
    async def refuse(**kw):
        raise bepusdt.BepusdtRefused("no")

    monkeypatch.setattr(env.res.bepusdt, "create_transaction", refuse)
    buyer, order, response = await _order(env)
    assert response.status_code == 503
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text


async def test_usdt_transport_error_stays_unknown_then_resolve_unblocks(env, mock, monkeypatch):
    async def boom(**kw):
        raise bepusdt.BepusdtError("timeout fixture")

    real = env.res.bepusdt.create_transaction
    monkeypatch.setattr(env.res.bepusdt, "create_transaction", boom)
    buyer, order, response = await _order(env)
    assert response.status_code == 503
    (att,) = await _attempts(env)
    assert att.status == "unknown"
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})).status_code == 503
    admin, _ = await env.login("admin")
    r = await admin.post(
        f"/api/v1/admin/payment-attempts/{att.id}/resolve",
        json={"outcome": "not_paid", "note": "checked the gateway console"},
    )
    assert r.status_code == 200, r.text
    monkeypatch.setattr(env.res.bepusdt, "create_transaction", real)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "usdt_trc20"})
    assert r.status_code == 200, r.text
    assert (await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})).status_code == 200


async def test_usdt_resolved_then_cancel_succeeds(env, mock, monkeypatch):
    async def boom(**kw):
        raise bepusdt.BepusdtError("timeout fixture")

    monkeypatch.setattr(env.res.bepusdt, "create_transaction", boom)
    buyer, order, _ = await _order(env)
    (att,) = await _attempts(env)
    admin, _ = await env.login("admin")
    r = await admin.post(
        f"/api/v1/admin/payment-attempts/{att.id}/resolve",
        json={"outcome": "not_paid", "note": "checked the gateway console"},
    )
    assert r.status_code == 200
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 200, r.text


async def test_paid_older_usdt_attempt_still_blocks_cancel_and_settles(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    # a refused newer attempt must not hide the paid original
    async with env.owner_session() as db:
        await db.execute(
            text(
                "INSERT INTO payment_attempts (tenant_id, local_ref, gateway_ref, channel,"
                " amount_minor, currency, status, data) SELECT tenant_id, local_ref,"
                " local_ref||'-u-failed', channel, amount_minor, currency, 'failed',"
                ' \'{"reason":"gateway_refused"}\' FROM payment_attempts LIMIT 1'
            )
        )
        await db.commit()
    await mock.patch_trade(trade_id, status=2)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid", r.text
    assert await _status(buyer, order) == "paid"


# ---------------------------------------------------------------------------------------- B3
async def _abandon(env, order, hours=73, status="expired"):
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE orders SET status=:s WHERE id=:i"), {"s": status, "i": order["id"]}
        )
        await db.execute(
            text("UPDATE payment_attempts SET created_at = now() - make_interval(hours => :h)"),
            {"h": hours},
        )
        await db.commit()


async def _close(env):
    async with env.api_session() as db:
        return await reconcile.close_abandoned(
            env.res, db, env.tenant_id, datetime.now(timezone.utc)
        )


async def test_expired_unpaid_attempt_is_closed_after_window_and_stops_polling(env, mock):
    _buyer, order, _page, _tid = await _paid_order(env, mock)
    await _abandon(env, order)
    assert await _close(env) == 1
    (att,) = await _attempts(env)
    assert att.status == "closed"
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.attempt_closed' "
            "AND actor_role='system'"
        )
        == 1
    )
    # no longer selected by the reconciler
    async with env.api_session() as db:
        left = await db.scalar(
            select(PaymentAttempt.id).where(PaymentAttempt.status.in_(reconcile.OPEN_STATUSES))
        )
    assert left is None
    assert await _close(env) == 0


async def test_attempt_not_closed_inside_window_or_for_a_pending_order(env, mock):
    _buyer, order, _page, _tid = await _paid_order(env, mock)
    await _abandon(env, order, hours=10)
    assert await _close(env) == 0
    await _abandon(env, order, hours=100, status="pending_payment")
    assert await _close(env) == 0
    assert (await _attempts(env))[0].status == "ready"


async def test_attempt_stays_open_when_the_gateway_says_paid_or_fails(env, mock, monkeypatch):
    _buyer, order, _page, trade_id = await _paid_order(env, mock)
    await _abandon(env, order)
    await mock.patch_trade(trade_id, status=2)  # paid
    assert await _close(env) == 0
    await mock.patch_trade(trade_id, status=1)

    async def fail(self, trade):
        raise bepusdt.BepusdtError("down")

    monkeypatch.setattr(bepusdt.BepusdtClient, "query_info", fail)
    assert await _close(env) == 0
    assert (await _attempts(env))[0].status == "ready"


async def test_late_notify_for_closed_attempt_lands_in_manual_review(env, mock):
    buyer, order, _page, trade_id = await _paid_order(env, mock)
    await _abandon(env, order)
    assert await _close(env) == 1
    r = await mock.client.post(f"/pay/{trade_id}")  # paid on chain, signed notify arrives late
    assert r.status_code == 302
    assert await _status(buyer, order) == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    # a closed attempt can still be moved to settled
    async with env.api_session() as db:
        await reconcile._mark_settled(
            db, env.tenant_id, PaymentAttempt.local_ref == order["order_no"]
        )
        await db.commit()
    assert (await _attempts(env))[0].status == "settled"


# ---------------------------------------------------------------------------------------- S12
async def test_second_trade_on_refunded_order_raises_duplicate_alert(env):
    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))
    async with env.api_session() as db:
        o = await db.scalar(select(Order).where(Order.id == uuid.UUID(order["id"])))
        amount = Decimal(o.amount_minor) / 100
        first = await apply_order_payment(db, env.res.settings, o.id, "T-one", amount, {}, None)
        assert first.dup_trade_no is None
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET status='refunded'"))
        await db.commit()
    async with env.api_session() as db:
        again = await apply_order_payment(db, env.res.settings, o.id, "T-one", amount, {}, None)
        assert again.dup_trade_no is None  # same trade: benign replay
    async with env.api_session() as db:
        second = await apply_order_payment(db, env.res.settings, o.id, "T-two", amount, {}, None)
    assert second.dup_trade_no is not None
    assert await env.scalar("SELECT status FROM orders") == "refunded"  # never fulfilled
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 1
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.unexpected_state'")
        == 0
    )


# ---------------------------------------------------------------------------------------- A3
async def test_redirect_gateway_is_not_marked_ok_by_building_a_url(env, monkeypatch):
    async def vetted(url):
        return safefetch.Target(url, "https", "gateway.example", 443, "/", "1.1.1.1")

    monkeypatch.setattr(safefetch, "vet_url", vetted)
    admin, _ = await env.login("admin")
    r = await admin.post(
        "/api/v1/admin/payment-gateways",
        json={
            "label": "Redirect fixture",
            "gateway": "https://gateway.example/submit.php",
            "merchant_id": "redir1",
            "secret": secrets.token_hex(16),
            "mode": "redirect",
            "current_password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    ref = r.json()["id"]
    r = await admin.post(
        f"/api/v1/admin/payment-gateways/{ref}/activate",
        json={"current_password": PASSWORD, "ownership_confirmed": True},
    )
    assert r.status_code == 200, r.text
    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200, r.text
    async with env.api_session() as db:
        gw = await db.scalar(select(PaymentGateway).where(PaymentGateway.id == uuid.UUID(ref)))
        assert gw.last_ok_at is None


# ---------------------------------------------------------------------------------------- A4
async def test_dead_delivery_job_raises_a_staff_alert_once_and_overview_counts(env, monkeypatch):
    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE orders SET status='paid', delivery_mode='auto_card', "
                "paid_at = now() - interval '20 minutes' "
                "WHERE id=:i"
            ),
            {"i": order["id"]},
        )
        await db.execute(
            text(
                "INSERT INTO outbox (tenant_id, topic, key, payload, attempts, last_error,"
                " processed_at) VALUES (:t,'order.paid',:k, CAST(:p AS jsonb), 8,'RuntimeError',"
                " now())"
            ),
            {"t": str(env.tenant_id), "k": order["id"], "p": '{"order_id": "' + order["id"] + '"}'},
        )
        await db.commit()
    sent = MagicMock()
    monkeypatch.setattr(notify_tg, "notify_bg", sent)
    async with env.api_session() as db:
        n = await reconcile.alert_dead_jobs(env.res, db, env.tenant_id, datetime.now(timezone.utc))
    assert n == 1 and sent.call_count == 1
    assert sent.call_args.args[2] == "payment_review"
    assert sent.call_args.kwargs["once"][0].startswith("dead_job:")
    async with env.api_session() as db:
        data = await ops_overview.overview(db, env.res, env.tenant_id)
    assert data["paid_undelivered_auto_card"] == 1
    assert "open_payment_attempts" in data["payment_review"]
