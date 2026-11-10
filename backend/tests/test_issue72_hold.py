"""#72 B2: every held (``manual_review``) order has an exit.

(a) held by a signed notify whose re-query disagreed, with NO payment recorded: staff can close it,
    but only after a fresh re-query says unpaid; a paid answer is settled and the close refused.
(b) a manual-delivery order paid after expiry (recorded and held): staff accept the payment, the
    order becomes ``paid`` with manual stock re-reserved, and can then be delivered.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.errors import NotFound
from app.modules.orders import admin as order_admin
from app.modules.orders.service import expire_pending
from tests.helpers import buy, notify_params
from tests.test_integrity_v8 import cross_tenant

API = "/api/v1"
NOTIFY = "/api/v1/pay/notify/zpay"


async def sql(env, stmt: str, **params) -> None:
    async with env.owner_session() as db:
        await db.execute(text(stmt), params)
        await db.commit()


async def held_without_payment(env, gw, *, stock: int = 3):
    """A pending manual order whose signed notify is contradicted by the re-query."""
    p = await env.product(mode="manual")
    await sql(env, "UPDATE products SET manual_stock=:s WHERE id=:p", s=stock, p=p.id)
    b, _ = await env.login("buyer")
    o = await buy(b, p)
    gw.paid[o["order_no"]] = ("19.90", "OTHER-TRADE")
    c = env.client(csrf=False)
    r = await c.get(NOTIFY, params=notify_params(o["order_no"], "19.90", trade_no="T-HOLD-1"))
    assert r.text == "success"
    assert (await b.get(f"{API}/orders/{o['id']}")).json()["status"] == "manual_review"
    assert await env.scalar("SELECT count(*) FROM payments") == 0
    return p, b, o


def release(admin, oid: str, outcome: str, reason: str = "checked with the gateway"):
    return admin.post(
        f"{API}/admin/orders/{oid}/release-hold", json={"outcome": outcome, "reason": reason}
    )


async def test_held_without_payment_can_be_closed_after_unpaid_requery(env, gw):
    p, b, o = await held_without_payment(env, gw)
    admin, _ = await env.login("admin")
    agent, _ = await env.login("agent")
    # before #72 every exit answered 409: buyer cancel, refund, deliver, requeue
    assert (await b.post(f"{API}/orders/{o['id']}/cancel", json={})).status_code == 409
    assert (await release(agent, o["id"], "close")).status_code == 403  # admin only
    assert (await release(admin, o["id"], "accept")).status_code == 409  # nothing was paid
    r = await admin.get(f"{API}/admin/orders/{o['id']}")
    assert r.json()["hold"] == {"reason": "requery_mismatch", "by": "gateway", "has_payment": False}
    # the gateway now says unpaid: the close goes through, stock comes back
    gw.paid.pop(o["order_no"])
    r = await release(admin, o["id"], "close")
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:p", p=p.id) == 3
    assert await env.scalar("SELECT stock_reserved FROM orders WHERE id=:o", o=o["id"]) == 0
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='order.hold_released' "
            "AND detail->>'outcome'='close' AND detail->>'hold_reason'='requery_mismatch'"
        )
        == 1
    )
    assert (await release(admin, o["id"], "close")).status_code == 409  # not held any more


async def test_close_is_refused_when_the_gateway_now_reports_paid(env, gw):
    _p, b, o = await held_without_payment(env, gw)
    admin, _ = await env.login("admin")
    gw.paid[o["order_no"]] = ("19.90", "T-REAL")  # the gateway's query caught up
    r = await release(admin, o["id"], "close")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid", r.text
    # the payment is now recorded (money never vanishes) and the order is still held
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert (await b.get(f"{API}/orders/{o['id']}")).json()["status"] == "manual_review"
    # a held order WITH a payment can never be closed without payment
    gw.paid.pop(o["order_no"])
    assert (await release(admin, o["id"], "close")).status_code == 409
    # ...but it can be accepted and delivered
    r = await release(admin, o["id"], "accept")
    assert r.status_code == 200 and r.json()["status"] == "paid", r.text


async def test_close_fails_closed_when_the_gateway_cannot_be_reached(env, gw):
    _p, b, o = await held_without_payment(env, gw)
    admin, _ = await env.login("admin")
    gw.down = True
    r = await release(admin, o["id"], "close")
    assert r.status_code == 503, r.text
    assert (await b.get(f"{API}/orders/{o['id']}")).json()["status"] == "manual_review"


async def test_late_manual_payment_is_accepted_restocked_and_delivered(env, gw):
    p = await env.product(mode="manual")
    await sql(env, "UPDATE products SET manual_stock=2 WHERE id=:p", p=p.id)
    b, _ = await env.login("buyer")
    o = await buy(b, p)
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:p", p=p.id) == 1
    await sql(env, "UPDATE orders SET expires_at = now() - interval '1 second'")
    assert await expire_pending(env.res.maker, env.res) >= 1
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:p", p=p.id) == 2
    # the buyer pays seconds after expiry: recorded and held
    gw.paid[o["order_no"]] = ("19.90", "T-LATE")
    c = env.client(csrf=False)
    r = await c.get(NOTIFY, params=notify_params(o["order_no"], "19.90", trade_no="T-LATE"))
    assert r.text == "success"
    assert (await b.get(f"{API}/orders/{o['id']}")).json()["status"] == "manual_review"
    admin, _ = await env.login("admin")
    staff, _ = await env.login("agent")
    deliver = f"{API}/admin/orders/{o['id']}/deliver"
    assert (await staff.post(deliver, json={"text": "code"})).status_code == 409  # still held
    assert (await release(admin, o["id"], "close")).status_code == 409  # a payment exists
    with pytest.raises(NotFound):  # tenant-pinned below the HTTP layer
        await cross_tenant(
            env,
            lambda db, u: order_admin.release_hold(
                db, env.res, uuid.UUID(o["id"]), "accept", "x", u, None
            ),
        )
    r = await release(admin, o["id"], "accept")
    assert r.status_code == 200 and r.json()["status"] == "paid", r.text
    assert await env.scalar("SELECT manual_stock FROM products WHERE id=:p", p=p.id) == 1
    assert await env.scalar("SELECT stock_reserved FROM orders WHERE id=:o", o=o["id"]) == 1
    assert await env.scalar("SELECT sold FROM products WHERE id=:p", p=p.id) == 1
    assert (await release(admin, o["id"], "accept")).status_code == 409  # once
    r = await staff.post(deliver, json={"text": "ACCOUNT: x / y"})
    assert r.status_code == 200 and r.json()["status"] == "delivered", r.text


async def test_accept_refuses_when_no_manual_stock_is_left(env, gw):
    p = await env.product(mode="manual")
    await sql(env, "UPDATE products SET manual_stock=1 WHERE id=:p", p=p.id)
    b, _ = await env.login("buyer")
    o = await buy(b, p)
    await sql(env, "UPDATE orders SET expires_at = now() - interval '1 second'")
    assert await expire_pending(env.res.maker, env.res) >= 1
    await sql(env, "UPDATE products SET manual_stock=0 WHERE id=:p", p=p.id)  # sold elsewhere
    gw.paid[o["order_no"]] = ("19.90", "T-LATE2")
    c = env.client(csrf=False)
    await c.get(NOTIFY, params=notify_params(o["order_no"], "19.90", trade_no="T-LATE2"))
    admin, _ = await env.login("admin")
    r = await release(admin, o["id"], "accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "out_of_stock", r.text
    assert (await b.get(f"{API}/orders/{o['id']}")).json()["status"] == "manual_review"


async def test_release_needs_a_reason(env, gw):
    _p, _b, o = await held_without_payment(env, gw)
    admin, _ = await env.login("admin")
    r = await admin.post(
        f"{API}/admin/orders/{o['id']}/release-hold", json={"outcome": "close", "reason": ""}
    )
    assert r.status_code == 422
    r = await release(admin, o["id"], "close", reason="   ")
    assert r.status_code == 422
