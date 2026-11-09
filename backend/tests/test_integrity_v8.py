"""v8 integrity (#59 A-D, F, G): no card oversell, requeue/refund of held orders, refund
reference, subscription refunds per period, reseller dispute reserve, term snapshot."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.core.errors import NotFound
from app.modules.common.sitedefaults import add_months
from app.modules.operations import jobs, service
from app.modules.orders import admin as order_admin
from app.modules.orders.service import expire_pending
from tests.helpers import delivered_order, open_conversation, pay_order, send_quote
from tests.test_commerce_v5 import FEATURES, make_reseller, set_settings
from tests.test_subscriptions_v7 import entitlements, subscription_product

API = "/api/v1"


async def sql(env, stmt: str, **params) -> None:
    async with env.owner_session() as db:
        await db.execute(text(stmt), params)
        await db.commit()


def buy(client, p, qty=1):
    return client.post(f"{API}/orders", json={"product_id": str(p.id), "qty": qty})


async def cross_tenant(env, call):
    """Run a service call as an admin of the OTHER tenant (RLS-pinned session)."""
    u = await env.user("admin", tenant_id=env.other_tenant_id)
    async with env.api_session(env.other_tenant_id) as db:
        return await call(db, u)


async def paid_manual_review(env, product):
    """Buy + pay one card, then remove the stock before the worker runs: the order is held in
    manual_review exactly as production can leave it. Returns (buyer client, order view)."""
    b, _ = await env.login("buyer")
    o = (await buy(b, product)).json()
    await pay_order(env, b, o)
    await sql(env, "UPDATE card_stock SET status='void' WHERE product_id=:p", p=product.id)
    assert await env.drain() >= 1
    held = (await b.get(f"{API}/orders/{o['id']}")).json()
    assert held["status"] == "manual_review", held
    return b, held


# ------------------------------------------------------------------ A: no oversell


async def test_last_card_is_reserved_and_released_on_expiry(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["ONLY-ONE-CODE"])
    b1, _ = await env.login("buyer")
    b2, _ = await env.login("buyer")
    r1 = await buy(b1, p)
    assert r1.status_code == 200, r1.text
    r2 = await buy(b2, p)
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "out_of_stock"
    r = await b2.post(f"{API}/checkout", json={"items": [{"product_id": str(p.id), "qty": 1}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    # quote acceptance honours the reservation too
    conv = await open_conversation(b2, p.id)
    agent, _ = await env.login("agent")
    q = await send_quote(agent, conv["id"], amount="19.90", product_id=str(p.id))
    r = await b2.post(f"{API}/quotes/{q['quote_id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"
    # expiry releases the reservation (derived from status): b2 can now buy
    await sql(env, "UPDATE orders SET expires_at = now() - interval '1 second'")
    assert await expire_pending(env.res.maker, env.res) >= 1
    assert (await buy(b2, p)).status_code == 200


async def test_concurrent_buyers_of_the_last_card(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["ONLY-ONE-CODE"])
    cs = [(await env.login("buyer"))[0] for _ in range(4)]
    rs = await asyncio.gather(*[buy(c, p) for c in cs])
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409]


async def test_requeue_needs_stock_payment_admin_and_is_idempotent(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["HELD-CODE-0001"])
    b, held = await paid_manual_review(env, p)
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")
    oid = uuid.UUID(held["id"])
    url = f"{API}/admin/orders/{held['id']}/requeue-delivery"
    assert (await agent.post(url)).status_code == 403
    r = await admin.post(url)
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock"  # no cards yet
    await env.import_codes(p, ["HELD-CODE-0002"])
    # a payment that does not cover the order is never enough
    await sql(env, "UPDATE payments SET amount_minor = 1")
    assert (await admin.post(url)).status_code == 409
    await sql(env, "UPDATE payments SET amount_minor = 1990")
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await other_admin.post(url)).status_code == 401  # session of another tenant
    with pytest.raises(NotFound):  # and below the HTTP layer: the order is invisible to it
        await cross_tenant(env, lambda db, u: order_admin.requeue_delivery(db, oid, u, None))
    r = await admin.post(url)
    assert r.status_code == 200 and r.json()["status"] == "paid", r.text
    assert (await admin.post(url)).status_code == 409  # no second requeue while it is paid
    assert await env.drain() >= 1
    assert (await b.get(f"{API}/orders/{held['id']}")).json()["status"] == "delivered"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.delivery_requeued'")
        == 1
    )
    assert await env.scalar("SELECT count(*) FROM outbox WHERE key LIKE '%:requeue:%'") == 1


async def test_refund_before_delivery(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["HELD-CODE-0001"])
    b, held = await paid_manual_review(env, p)
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")
    oid = uuid.UUID(held["id"])
    url = f"{API}/admin/orders/{held['id']}/refund"
    assert (await agent.post(url, json={"reference": "REF-1234"})).status_code == 403
    assert (await admin.post(url, json={})).status_code == 422
    assert (await admin.post(url, json={"reference": "ab"})).status_code == 422
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    assert (await other_admin.post(url, json={"reference": "REF-1234"})).status_code == 401
    with pytest.raises(NotFound):
        await cross_tenant(
            env, lambda db, u: order_admin.refund_undelivered(db, oid, "REF-1234", None, u, None)
        )
    r = await admin.post(url, json={"reference": "alipay refund 2026110912345"})
    assert r.status_code == 200 and r.json()["status"] == "refunded", r.text
    assert (await admin.post(url, json={"reference": "REF-1234"})).status_code == 409
    assert await env.scalar("SELECT count(*) FROM ledger_entries WHERE memo LIKE 'refund %'") >= 2
    assert (
        await env.scalar(
            "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
            "HAVING sum(amount_minor) <> 0) x"
        )
        == 0
    )
    seen = (await b.get(f"{API}/orders/{held['id']}")).json()
    assert seen["refund_reference"] == "alipay refund 2026110912345"
    assert (
        await env.scalar("SELECT detail->>'reference' FROM audit_log WHERE action='order.refunded'")
        == "alipay refund 2026110912345"
    )


async def test_after_sales_refund_requires_reference(env):
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    p = await env.product(mode="manual", price=1000, floor=100)
    o = await delivered_order(env, buyer, p, staff=staff)
    t = (
        await buyer.post(
            f"{API}/orders/{o['id']}/after-sales",
            json={"reason": "other", "description": "please"},
        )
    ).json()
    url = f"{API}/admin/after-sales/{t['id']}/action"
    r = await admin.post(url, json={"action": "refund"})
    assert r.status_code == 422, r.text
    r = await admin.post(url, json={"action": "refund", "reference": "  ab  "})
    assert r.status_code == 422
    r = await admin.post(url, json={"action": "refund", "reference": "TXN-0xabc123"})
    assert r.status_code == 200, r.text
    assert (await buyer.get(f"{API}/orders/{o['id']}")).json()["refund_reference"] == "TXN-0xabc123"


async def test_buyer_not_received_escalation_timing(env):
    p = await env.product(mode="manual", price=1000, floor=100)
    b, _ = await env.login("buyer")
    o = (await buy(b, p)).json()
    await pay_order(env, b, o)
    url = f"{API}/orders/{o['id']}/after-sales"
    body = {"reason": "not_received", "description": "nothing arrived"}
    r = await b.post(url, json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "delivery_not_overdue"
    assert (await b.post(url, json={**body, "reason": "other"})).status_code == 409
    await sql(env, "UPDATE orders SET paid_at = now() - interval '25 hours'")
    r = await b.post(url, json=body)
    assert r.status_code == 200, r.text
    # auto-card: one hour
    p2 = await env.product(mode="auto_card")
    await env.import_codes(p2, ["NR-CODE-0001"])
    b2, held = await paid_manual_review(env, p2)
    r = await b2.post(f"{API}/orders/{held['id']}/after-sales", json=body)
    assert r.status_code == 409
    await sql(
        env,
        "UPDATE orders SET paid_at = now() - interval '2 hours' WHERE id=:i",
        i=uuid.UUID(held["id"]),
    )
    r = await b2.post(f"{API}/orders/{held['id']}/after-sales", json=body)
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ G: stock provenance


async def test_release_gives_back_only_what_was_reserved(env):
    p = await env.product(mode="manual", price=500, floor=100)
    b, _ = await env.login("buyer")
    await sql(env, "UPDATE products SET manual_stock = NULL WHERE id=:i", i=p.id)
    o = (await buy(b, p, 2)).json()
    q = "SELECT stock_reserved FROM orders WHERE id=:i"
    assert await env.scalar(q, i=uuid.UUID(o["id"])) == 0
    await sql(
        env, "UPDATE products SET manual_stock = 5 WHERE id=:i", i=p.id
    )  # unlimited -> finite
    assert (await b.post(f"{API}/orders/{o['id']}/cancel", json={})).status_code == 200
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:i", i=p.id) == 5
    # a reserved order releases exactly its quantity, once
    o2 = (await buy(b, p, 2)).json()
    assert await env.scalar(q, i=uuid.UUID(o2["id"])) == 2
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:i", i=p.id) == 3
    await b.post(f"{API}/orders/{o2['id']}/cancel", json={})
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:i", i=p.id) == 5
    assert await env.scalar(q, i=uuid.UUID(o2["id"])) == 0


# ------------------------------------------------------------------ F: terms snapshot


async def test_terms_snapshot_survives_product_edit(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await env.product(mode="manual", price=1000, floor=100)
    await admin.patch(f"{API}/admin/products/{p.id}", json={"after_sales_months": 2})
    o = (await buy(buyer, p)).json()
    months = await env.scalar(
        "SELECT terms->>'after_sales_months' FROM orders WHERE id=:i", i=uuid.UUID(o["id"])
    )
    assert months == "2"
    await admin.patch(f"{API}/admin/products/{p.id}", json={"after_sales_months": 6})
    await pay_order(env, buyer, o)
    r = await staff.post(f"{API}/admin/orders/{o['id']}/deliver", json={"text": "ok"})
    assert r.status_code == 200, r.text
    d = datetime.fromisoformat(r.json()["delivered_at"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(r.json()["after_sales_until"].replace("Z", "+00:00"))
    assert until == add_months(d, 2)


async def test_subscription_terms_come_from_the_snapshot(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await subscription_product(env, admin, period_policy="calendar_month")
    o = await delivered_order(env, buyer, p, staff=staff)
    # the product stops being a subscription before the sync job runs: the order still is one
    await sql(env, "UPDATE products SET subscription = NULL WHERE id=:i", i=p.id)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    assert str(row.order_id) == o["id"]


# ------------------------------------------------------------------ C: subscription refunds


async def two_period_service(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await subscription_product(env, admin)
    first = await delivered_order(env, buyer, p, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1
    second = await delivered_order(env, buyer, p, qty=2, staff=staff)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    return first, second, row


async def void_order(env, order) -> None:
    await sql(env, "UPDATE orders SET status='refunded' WHERE id=:i", i=uuid.UUID(order["id"]))


async def test_renewal_refund_takes_back_only_its_periods(env):
    first, second, row = await two_period_service(env)
    full = row.ends_at
    await void_order(env, second)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    assert row.ends_at == service.periods_end(full, row.period_policy, 30, "UTC", -2)
    assert row.status == "active" and row.version == 3
    n = await env.scalar("SELECT count(*) FROM service_renewals WHERE reversed_at IS NOT NULL")
    assert n == 1
    n = await env.scalar("SELECT count(*) FROM audit_log WHERE action='service.renewal_reversed'")
    assert n == 1
    assert await jobs.sync_subscriptions(env.res) == 0  # reversed once
    # the source is refunded as well and no renewal remains -> the service is cancelled
    await void_order(env, first)
    assert await jobs.sync_subscriptions(env.res) == 1
    (row,) = await entitlements(env)
    assert row.status == "cancelled"


async def test_ambiguous_reversal_is_left_for_staff(env):
    first, _second, row = await two_period_service(env)
    await sql(env, "UPDATE service_entitlements SET starts_at = ends_at - interval '1 day'")
    await void_order(env, first)
    assert await jobs.sync_subscriptions(env.res) == 0
    (row2,) = await entitlements(env)
    assert row2.ends_at == row.ends_at and row2.source_reversed_at is None
    assert await jobs.sync_subscriptions(env.res) == 0
    n = await env.scalar(
        "SELECT count(*) FROM operation_events WHERE action='service.reversal_ambiguous'"
    )
    assert n == 1


# ------------------------------------------------------------------ D: reseller dispute reserve


async def test_open_ticket_holds_reseller_markup(env):
    await set_settings(
        env, {**FEATURES, "resellers": {"settle_after_hours": 0, "min_payout_minor": 100}}
    )
    rb, ru = await env.login("buyer")
    await make_reseller(env, ru, "shopa", kind="fixed", value=400)
    p = await env.product(mode="manual", price=1000, floor=100)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    o = (
        await buyer.post(
            f"{API}/orders", json={"product_id": str(p.id)}, headers={"X-Storefront": "shopa"}
        )
    ).json()
    await pay_order(env, buyer, o)
    await staff.post(f"{API}/admin/orders/{o['id']}/deliver", json={"text": "ok"})
    await buyer.post(f"{API}/orders/{o['id']}/confirm")
    await rb.patch(f"{API}/reseller/me", json={"payout_account": {"name": "A", "account": "x"}})
    assert (await rb.get(f"{API}/reseller/me")).json()["balances"]["available_minor"] == 400
    t = (
        await buyer.post(
            f"{API}/orders/{o['id']}/after-sales", json={"reason": "other", "description": "hmm"}
        )
    ).json()
    bal = (await rb.get(f"{API}/reseller/me")).json()["balances"]
    assert bal["available_minor"] == 0 and bal["disputed_minor"] == 400
    r = await rb.post(f"{API}/reseller/payouts", json={"amount_minor": 400})
    assert r.json()["error"]["code"] == "insufficient_balance"
    await admin.post(
        f"{API}/admin/after-sales/{t['id']}/action", json={"action": "reject", "note": "no"}
    )
    bal = (await rb.get(f"{API}/reseller/me")).json()["balances"]
    assert bal["available_minor"] == 400 and bal["disputed_minor"] == 0
