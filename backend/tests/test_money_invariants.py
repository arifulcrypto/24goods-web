# ruff: noqa: F811
"""Issue #72 A8: ledger property test (seeded random sequences) and a settlement race
(notify + reconciler + buyer cancel on one payable)."""

from __future__ import annotations

import asyncio
import random
import uuid
from decimal import Decimal

from sqlalchemy import select, text

from app.core.errors import ApiError
from app.models import Order, User
from app.modules.aftersales.service import refund_order
from app.modules.orders.admin import lock_order
from app.modules.payments import reconcile
from app.modules.payments.settle import apply_checkout_payment, apply_order_payment
from tests.helpers import buy, notify_params
from tests.test_commerce_v4 import checkout, line
from tests.test_notify import NOTIFY, GatewayStub, gw  # noqa: F401


async def _assert_ledger_sane(env) -> None:
    unbalanced = await env.scalar(
        "SELECT count(*) FROM (SELECT txn_id FROM ledger_entries GROUP BY txn_id "
        "HAVING sum(amount_minor) <> 0) t"
    )
    assert unbalanced == 0
    negative = await env.scalar(
        "SELECT count(*) FROM (SELECT order_id FROM ledger_entries "
        "WHERE account LIKE 'gateway_clearing:%' AND order_id IS NOT NULL "
        # the gateway fee posting is deliberately not reversed by a refund (the gateway keeps it)
        "AND memo NOT LIKE '% fee %' "
        "GROUP BY order_id HAVING sum(amount_minor) < 0) t"
    )
    assert negative == 0


async def test_ledger_property_random_pay_duplicate_late_refund_sequences(env):
    rng = random.Random(7261)
    buyer, _ = await env.login("buyer")
    async with env.owner_session() as db:
        staff = await db.scalar(select(User).where(User.id == (await env.user("admin")).id))
    product = await env.product(mode="manual", price=1990, floor=100)
    other = await env.product(mode="manual", price=990, floor=100)

    orders: list[dict] = []  # {"id","order_no","amount","trade","kind","co"}
    refs = iter(range(10_000))

    async def new_order() -> None:
        o = await buy(buyer, product, qty=rng.choice([1, 2]))
        orders.append({"id": uuid.UUID(o["id"]), "no": o["order_no"], "trades": [], "co": None})

    async def new_checkout() -> None:
        r = await checkout(buyer, [line(product), line(other)])
        assert r.status_code == 200, r.text
        body = r.json()
        async with env.owner_session() as db:
            cid = await db.scalar(
                text("SELECT id FROM checkouts WHERE checkout_no=:n"),
                {"n": body["checkout"]["checkout_no"]},
            )
            members = list(
                await db.scalars(text("SELECT id FROM orders WHERE checkout_id=:c"), {"c": cid})
            )
        orders.append(
            {
                "co": cid,
                "amount": Decimal(body["checkout"]["amount"]),
                "members": members,
                "trades": [],
            }
        )

    async def pay(t: dict, trade: str, amount: Decimal | None = None) -> None:
        async with env.api_session() as db:
            if t["co"] is not None:
                await apply_checkout_payment(
                    db, env.res.settings, t["co"], trade, amount or t["amount"], {}, None
                )
            else:
                o = await db.scalar(select(Order).where(Order.id == t["id"]))
                amt = amount or Decimal(o.amount_minor) / 100
                await apply_order_payment(db, env.res.settings, t["id"], trade, amt, {}, None)
        t["trades"].append(trade)

    async def refund(order_id: uuid.UUID) -> None:
        async with env.api_session() as db:
            o = await lock_order(db, order_id, env.tenant_id)
            try:
                await refund_order(
                    db, o, staff, None, f"REF-{next(refs)}", None, source="after_sales"
                )
                await db.commit()
            except ApiError:
                await db.rollback()  # a legal refusal (wrong state): ledger untouched

    for step in range(50):
        if not orders or rng.random() < 0.3:
            await (new_checkout() if rng.random() < 0.3 else new_order())
        t = rng.choice(orders)
        action = rng.choice(["pay", "pay", "dup", "second_trade", "late", "refund", "cancel"])
        if action == "pay" and not t["trades"]:
            await pay(t, f"T-{step}")
        elif action == "dup" and t["trades"]:
            await pay(t, t["trades"][0])  # replay of the same trade
        elif action == "second_trade":
            await pay(t, f"T-{step}-b")  # a different trade on a payable
        elif action == "late":
            if t["co"] is None:  # cancel then pay: lands in manual review, never booked twice
                await buyer.post(f"/api/v1/orders/{t['id']}/cancel", json={})
            await pay(t, f"T-{step}-late")
        elif action == "refund":
            target = t["id"] if t["co"] is None else rng.choice(t["members"])
            await refund(target)
        await _assert_ledger_sane(env)
    assert await env.scalar("SELECT count(*) FROM ledger_entries") > 0


async def test_notify_reconciler_and_cancel_race_settle_exactly_once(env, gw, monkeypatch):
    for round_no in range(3):
        product = await env.product(mode="manual", price=1990, floor=100)
        buyer, _ = await env.login("buyer")
        order = await buy(buyer, product)
        r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
        assert r.status_code == 200, r.text
        gw.trade_no = f"T-RACE-{round_no}"
        async with env.owner_session() as db:  # make the attempt due for the reconciler
            await db.execute(
                text(
                    "UPDATE payment_attempts SET checked_at = NULL, "
                    "created_at = now() - interval '5 min'"
                )
            )
            await db.commit()
        p = notify_params(order["order_no"], "19.90", trade_no=gw.trade_no)
        c = env.client(csrf=False)

        async def notify(c=c, p=p):
            return await c.get(NOTIFY, params=p)

        async def reconcile_run():
            return await reconcile.run(env.res)

        async def cancel(buyer=buyer, order=order):
            return await buyer.post(f"/api/v1/orders/{order['id']}/cancel", json={})

        await asyncio.gather(notify(), reconcile_run(), cancel(), return_exceptions=True)
        oid = uuid.UUID(order["id"])
        assert await env.scalar("SELECT count(*) FROM payments WHERE order_id=:o", o=oid) == 1, (
            "exactly one Payment"
        )
        status = await env.scalar("SELECT status FROM orders WHERE id=:o", o=oid)
        # paid beats cancel; a cancel that won the race routes the payment to manual review
        assert status in ("paid", "manual_review")
        paid_events = await env.scalar(
            "SELECT count(*) FROM order_events WHERE order_id=:o AND to_status='paid'", o=oid
        )
        outbox = await env.scalar(
            "SELECT count(*) FROM outbox WHERE payload->>'order_id'=:o", o=str(oid)
        )
        assert paid_events == (1 if status == "paid" else 0)
        assert outbox == paid_events, "one fulfilment iff the order was paid"
        assert (
            await env.scalar("SELECT count(*) FROM audit_log WHERE action='payment.duplicate'") == 0
        )
        await _assert_ledger_sane(env)
        async with env.owner_session() as db:  # next round starts clean
            await db.execute(
                text("TRUNCATE outbox, ledger_entries, payments, payment_attempts CASCADE")
            )
            await db.commit()
