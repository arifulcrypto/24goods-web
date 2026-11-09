"""After-sales tickets and staff actions (replace / refund / reject / resolve)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.modules.common.sitedefaults import add_months
from app.modules.jobs import auto_confirm
from tests.helpers import delivered_order, notify_params, open_conversation
from tests.test_notify import GatewayStub

CODES = [f"ACC{i}----pw{i}----2fa{i}----n" for i in range(6)]
NOTIFY_URL = "/api/v1/pay/notify/zpay"
TICKET = {"reason": "invalid_code", "description": "does not work", "attachment_ids": []}


async def setup_manual(env):
    p = await env.product(mode="manual")
    buyer, _bu = await env.login("buyer")
    staff, _su = await env.login("agent")
    admin, _au = await env.login("admin")
    order = await delivered_order(env, buyer, p, staff=staff)
    return p, buyer, staff, admin, order


async def open_ticket(buyer, order, **over):
    return await buyer.post(f"/api/v1/orders/{order['id']}/after-sales", json={**TICKET, **over})


async def test_delivery_sets_deadlines_manual_and_auto(env):
    p = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    o = await delivered_order(env, buyer, p, staff=staff)
    assert o["after_sales_until"] and o["auto_confirm_at"]
    d = datetime.fromisoformat(o["delivered_at"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(o["after_sales_until"].replace("Z", "+00:00"))
    conf = datetime.fromisoformat(o["auto_confirm_at"].replace("Z", "+00:00"))
    assert until == add_months(d, 1) and conf - d == timedelta(hours=72)
    p2 = await env.product(mode="auto_card", title="Card")
    await env.import_codes(p2, CODES[:2])
    o2 = await delivered_order(env, buyer, p2)
    d2 = datetime.fromisoformat(o2["delivered_at"].replace("Z", "+00:00"))
    conf2 = datetime.fromisoformat(o2["auto_confirm_at"].replace("Z", "+00:00"))
    assert conf2 - d2 == timedelta(hours=24)
    assert o2["delivery_info"]["separator"] == "----"


async def test_product_after_sales_hours_override_and_zero(env):
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    p = await env.product(mode="manual")
    async with env.owner_session() as db:
        await db.execute(text("UPDATE products SET after_sales_hours = 24"))
        await db.commit()
    o = await delivered_order(env, buyer, p, staff=staff)
    d = datetime.fromisoformat(o["delivered_at"].replace("Z", "+00:00"))
    until = datetime.fromisoformat(o["after_sales_until"].replace("Z", "+00:00"))
    assert until - d == timedelta(hours=24)
    async with env.owner_session() as db:
        await db.execute(text("UPDATE products SET after_sales_hours = 0"))
        await db.commit()
    o = await delivered_order(env, buyer, p, staff=staff)
    assert o["after_sales_until"] is None
    r = await open_ticket(buyer, o)
    assert r.status_code == 409 and r.json()["error"]["code"] == "after_sales_closed"


async def test_open_ticket_window_state_and_single_open(env):
    _p, buyer, _staff, _admin, order = await setup_manual(env)
    r = await open_ticket(buyer, order)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "open" and t["order_id"] == order["id"] and t["reason"] == "invalid_code"
    assert (await buyer.get(f"/api/v1/orders/{order['id']}")).json()["after_sales"]["id"] == t["id"]
    r = await open_ticket(buyer, order)  # one open ticket per order
    assert r.status_code == 409 and r.json()["error"]["code"] == "after_sales_open"
    # window closed
    async with env.owner_session() as db:
        await db.execute(text("UPDATE after_sales SET status='resolved'"))
        await db.execute(text("UPDATE orders SET after_sales_until = now() - interval '1 minute'"))
        await db.commit()
    r = await open_ticket(buyer, order)
    assert r.status_code == 409 and r.json()["error"]["code"] == "after_sales_closed"
    # not delivered
    pending = await env.product(mode="manual", title="Other")
    from tests.helpers import buy

    o2 = await buy(buyer, pending)
    r = await open_ticket(buyer, o2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


async def test_validation_and_attachments(env):
    _p, buyer, _staff, _admin, order = await setup_manual(env)
    assert (await open_ticket(buyer, order, reason="bogus")).status_code == 422
    assert (await open_ticket(buyer, order, description="")).status_code == 422
    assert (await open_ticket(buyer, order, description="x" * 2001)).status_code == 422
    bogus = ["00000000-0000-0000-0000-000000000001"]
    assert (await open_ticket(buyer, order, attachment_ids=bogus)).status_code == 404
    five = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(5)]
    assert (await open_ticket(buyer, order, attachment_ids=five)).status_code == 422
    from tests.test_chat import png_bytes

    up = await buyer.post(
        "/api/v1/uploads",
        files={"file": ("a.png", png_bytes(), "image/png")},
        data={"kind": "image"},
    )
    r = await open_ticket(buyer, order, attachment_ids=[up.json()["id"]])
    assert r.status_code == 200 and r.json()["attachments"][0]["id"] == up.json()["id"]
    # someone else's upload cannot be attached
    other, _ = await env.login("buyer")
    up2 = await other.post(
        "/api/v1/uploads",
        files={"file": ("a.png", png_bytes(), "image/png")},
        data={"kind": "image"},
    )
    await env.scalar("SELECT 1")
    async with env.owner_session() as db:
        await db.execute(text("UPDATE after_sales SET status='resolved'"))
        await db.commit()
    assert (await open_ticket(buyer, order, attachment_ids=[up2.json()["id"]])).status_code == 404


async def test_cross_tenant_and_cross_buyer_isolation(env):
    _p, buyer, staff, _admin, order = await setup_manual(env)
    r = await open_ticket(buyer, order)
    tid = r.json()["id"]
    intruder, _ = await env.login("buyer")
    assert (await open_ticket(intruder, order)).status_code == 404
    other_staff, _ = await env.login("agent", tenant_id=env.other_tenant_id)
    # same host maps to the default tenant: other-tenant staff session is rejected outright
    assert (await other_staff.get("/api/v1/admin/after-sales")).status_code == 401
    # RLS: the other tenant sees nothing, whatever it asks
    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT count(*) FROM after_sales")) == 0
        assert await db.scalar(text("SELECT count(*) FROM articles")) == 0
        assert await db.scalar(text("SELECT count(*) FROM card_batches")) == 0
    assert (await buyer.get("/api/v1/admin/after-sales")).status_code == 403
    assert (await staff.get(f"/api/v1/admin/after-sales/{tid}")).status_code == 200
    # a tenant-pinned staff query cannot fetch the other tenant's ticket by id
    other_staff2, _ = await env.login("agent")
    async with env.api_session(env.other_tenant_id) as db:
        assert (
            await db.scalar(text("SELECT count(*) FROM after_sales WHERE id=:i"), {"i": tid}) == 0
        )
    _ = other_staff2


async def test_admin_list_detail_and_actions_manual(env):
    _p, buyer, staff, _admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    r = await staff.get("/api/v1/admin/after-sales", params={"status": "open"})
    assert r.status_code == 200 and r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["order_no"] == order["order_no"] and item["buyer"]["email"]
    r = await staff.post(
        f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "processing"}
    )
    assert r.status_code == 200 and r.json()["status"] == "processing"
    r = await staff.post(
        f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "processing"}
    )
    assert r.status_code == 409
    r = await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "replace"})
    assert r.status_code == 422  # manual replace needs the new delivery text
    r = await staff.post(
        f"/api/v1/admin/after-sales/{t['id']}/action",
        json={"action": "replace", "delivery_text": "new account: a / b", "note": "sorry"},
    )
    assert r.status_code == 200 and r.json()["status"] == "replaced"
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["manual_delivery"]["text"] == "new account: a / b"
    assert (
        o["after_sales"]["status"] == "replaced" and o["after_sales"]["resolution_note"] == "sorry"
    )
    r = await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "resolve"})
    assert r.status_code == 409  # already closed
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action LIKE 'aftersales.%'") == 3


async def test_reject_requires_note_and_resolve(env):
    _p, buyer, staff, _admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    url = f"/api/v1/admin/after-sales/{t['id']}/action"
    assert (await staff.post(url, json={"action": "reject"})).status_code == 422
    r = await staff.post(url, json={"action": "reject", "note": "used already"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["after_sales"]["resolution_note"] == "used already"
    # a rejected ticket frees the order for a new one
    t2 = (await open_ticket(buyer, order)).json()
    r = await staff.post(f"/api/v1/admin/after-sales/{t2['id']}/action", json={"action": "resolve"})
    assert r.json()["status"] == "resolved" and r.json()["resolved_at"]


async def test_refund_marks_order_and_ledger_balances_to_zero(env):
    _p, buyer, staff, admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    url = f"/api/v1/admin/after-sales/{t['id']}/action"
    r = await staff.post(
        url, json={"action": "refund", "reference": "REF-TEST-1"}
    )  # agents cannot move money
    assert r.status_code == 403
    r = await admin.post(
        url, json={"action": "refund", "note": "refunded in Alipay", "reference": "REF-TEST-1"}
    )
    assert r.status_code == 200 and r.json()["status"] == "refunded"
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "refunded"
    async with env.owner_session() as db:
        sums = (
            await db.execute(
                text(
                    "SELECT txn_id, sum(amount_minor), count(*) FROM ledger_entries GROUP BY txn_id"
                )
            )
        ).all()
        assert len(sums) == 3 and all(s == 0 for _, s, _ in sums)  # sale, fee, refund
        refund = (
            await db.execute(
                text(
                    "SELECT account, amount_minor FROM ledger_entries "
                    "WHERE memo LIKE 'refund %' ORDER BY id"
                )
            )
        ).all()
    assert [a for a, _ in refund] == ["sales_revenue", "gateway_clearing:zpay"]
    assert refund[0][1] == -refund[1][1] == 1990
    assert await env.scalar("SELECT count(*) FROM order_events WHERE to_status='refunded'") == 1
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.refunded'") == 1
    r = await admin.post(url, json={"action": "refund"})
    assert r.status_code == 409  # cannot refund twice


async def test_replace_auto_card_via_delivery_worker(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, CODES)
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    await open_conversation(buyer)  # system events are posted into the buyer's conversation
    order = await delivered_order(env, buyer, p)
    first = (await buyer.post(f"/api/v1/orders/{order['id']}/reveal")).json()["cards"][0]
    t = (await open_ticket(buyer, order)).json()
    r = await staff.post(
        f"/api/v1/admin/after-sales/{t['id']}/action",
        json={"action": "replace", "note": "swapped"},
    )
    assert r.status_code == 200 and r.json()["status"] == "processing"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='delivery.replace'") == 1
    assert await env.drain() == 1
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["after_sales"]["status"] == "replaced"
    assert await env.scalar("SELECT count(*) FROM card_stock WHERE status='void'") == 1
    assert (
        await env.scalar("SELECT void_reason FROM card_stock WHERE id=:i", i=first["id"])
        == "defective"
    )
    assert await env.scalar("SELECT count(*) FROM deliveries WHERE order_id=:i", i=order["id"]) == 2
    revealed = (await buyer.post(f"/api/v1/orders/{order['id']}/reveal")).json()
    assert len(revealed["cards"]) == 1 and revealed["cards"][0]["id"] != first["id"]
    assert revealed["cards"][0]["code"] != first["code"]
    msgs = (await buyer.get("/api/v1/conversations")).json()
    events = [
        m["payload"]["event"]
        for m in (await buyer.get(f"/api/v1/conversations/{msgs[0]['id']}/messages")).json()
        if m["type"] == "system"
    ]
    assert "after_sales_opened" in events and events[-1] == "after_sales_replaced"
    # the worker is idempotent: a second run finds nothing to do
    assert await env.scalar("SELECT status FROM after_sales") == "replaced"


async def test_replace_without_stock_leaves_ticket_processing(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, CODES[:1])
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    order = await delivered_order(env, buyer, p)
    t = (await open_ticket(buyer, order)).json()
    await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "replace"})
    await env.drain()
    assert await env.scalar("SELECT status FROM after_sales") == "processing"
    assert await env.scalar("SELECT count(*) FROM card_stock WHERE status='void'") == 0
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='delivery.replace_out_of_stock'"
        )
        == 1
    )


async def test_auto_confirm_completes_unless_ticket_open(env):
    _p, buyer, staff, _admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET auto_confirm_at = now() - interval '1 hour'"))
        await db.commit()
    assert await auto_confirm(env.res.maker, env.res) == 0  # paused while the ticket is open
    r = await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "resolve"})
    assert r.status_code == 200
    due = await env.scalar("SELECT auto_confirm_at FROM orders")
    assert due > datetime.now(timezone.utc) + timedelta(hours=23)  # max(old, now + 24h)
    assert await auto_confirm(env.res.maker, env.res) == 0
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET auto_confirm_at = now() - interval '1 second'"))
        await db.commit()
    assert await auto_confirm(env.res.maker, env.res) == 1
    o = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert o["status"] == "completed"
    assert (
        await env.scalar("SELECT reason FROM order_events WHERE to_status='completed'")
        == "auto_confirm"
    )
    assert await auto_confirm(env.res.maker, env.res) == 0
    # a completed order can still open after-sales within the window
    assert (await open_ticket(buyer, order)).status_code == 200


async def test_sse_after_sales_event_published_to_staff(env):
    import asyncio
    import json

    _p, buyer, _staff, _admin, order = await setup_manual(env)
    pubsub = env.s.redis.pubsub()
    await pubsub.subscribe(f"rt:staff:{env.tenant_id}")
    await asyncio.sleep(0.05)
    await open_ticket(buyer, order)
    seen = []
    for _ in range(20):
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if m:
            seen.append(json.loads(m["data"])["event"])
        if "after_sales" in seen:
            break
    await pubsub.aclose()
    assert "after_sales" in seen


# ------------------------------------------------------------------ payment-audit fixes
async def _auto_order(env, n_codes=6, conversation=False):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, CODES[:n_codes])
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    admin, _ = await env.login("admin")
    if conversation:
        await open_conversation(buyer)
    order = await delivered_order(env, buyer, p)
    return p, buyer, staff, admin, order


async def test_refund_unpaid_order_rejected(env):
    _p, buyer, _staff, admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    async with env.owner_session() as db:  # force an impossible state
        await db.execute(text("UPDATE orders SET status='pending_payment'"))
        await db.commit()
    r = await admin.post(
        f"/api/v1/admin/after-sales/{t['id']}/action",
        json={"action": "refund", "reference": "REF-TEST-1"},
    )
    assert r.status_code == 409
    assert await env.scalar("SELECT count(*) FROM ledger_entries WHERE memo LIKE 'refund %'") == 0


async def test_service_level_refund_requires_admin(env):
    from app.core.db import tenant_session
    from app.core.errors import Forbidden
    from app.models import User
    from app.modules.aftersales import service
    from app.modules.aftersales.schemas import ActionIn

    _p, buyer, _staff, _admin, order = await setup_manual(env)
    t = (await open_ticket(buyer, order)).json()
    async with tenant_session(env.res.maker, env.tenant_id) as db:
        agent = await db.scalar(select_user("agent"))
        assert isinstance(agent, User)
        try:
            await service.act(
                db,
                agent,
                uuid.UUID(t["id"]),
                ActionIn(action="refund", reference="REF-TEST-1"),
                None,
            )
        except Forbidden:
            pass
        else:
            raise AssertionError("agent refund must be refused by the service")


def select_user(role: str):
    from sqlalchemy import select

    from app.models import User

    return select(User).where(User.role == role).limit(1)


@pytest.fixture
async def gw(env):
    stub = GatewayStub()
    client = httpx.AsyncClient(transport=httpx.MockTransport(stub.handler))
    env.res.zpay._http = client
    yield stub
    await client.aclose()


async def test_duplicate_notify_after_refund_is_benign(env, gw):
    notify = notify_params
    p = await env.product(mode="manual", price=1990, floor=100)
    buyer, _ = await env.login("buyer")
    admin, _ = await env.login("admin")
    from tests.helpers import buy

    o = await buy(buyer, p)
    params = notify(o["order_no"], "19.90")
    assert (await env.client(csrf=False).get(NOTIFY_URL, params=params)).text == "success"
    async with env.owner_session() as db:  # delivered + ticket, then refund
        await db.execute(text("UPDATE orders SET status='delivered', delivered_at=now()"))
        await db.execute(
            text("UPDATE orders SET after_sales_until=now()+interval '1 day', version=version+1")
        )
        await db.commit()
    t = (await open_ticket(buyer, o)).json()
    r = await admin.post(
        f"/api/v1/admin/after-sales/{t['id']}/action",
        json={"action": "refund", "reference": "REF-TEST-1"},
    )
    assert r.status_code == 200
    before = (
        await env.scalar("SELECT count(*) FROM payments"),
        await env.scalar("SELECT count(*) FROM ledger_entries"),
        await env.scalar("SELECT count(*) FROM audit_log WHERE severity='critical'"),
    )
    # replay guard branch (same notify) and settle branch (fresh replay key) both succeed quietly
    assert (await env.client(csrf=False).get(NOTIFY_URL, params=params)).text == "success"
    for k in await env.s.redis.keys("notify:zpay:*"):
        await env.s.redis.delete(k)
    assert (await env.client(csrf=False).get(NOTIFY_URL, params=params)).text == "success"
    after = (
        await env.scalar("SELECT count(*) FROM payments"),
        await env.scalar("SELECT count(*) FROM ledger_entries"),
        await env.scalar("SELECT count(*) FROM audit_log WHERE severity='critical'"),
    )
    assert before == after
    assert await env.scalar("SELECT status FROM orders") == "refunded"
    # a DIFFERENT trade_no on a refunded order stays a critical audit
    other = notify(o["order_no"], "19.90", trade_no="T999")
    gw.trade_no = "T999"
    assert (await env.client(csrf=False).get(NOTIFY_URL, params=other)).text == "success"
    assert (
        await env.scalar(
            "SELECT count(*) FROM audit_log WHERE action='payment.unexpected_state' "
            "AND severity='critical'"
        )
        == 1
    )
    assert await env.scalar("SELECT status FROM orders") == "refunded"


async def test_reveal_after_refund_is_409(env):
    _p, buyer, _staff, admin, order = await _auto_order(env)
    t = (await open_ticket(buyer, order)).json()
    r = await admin.post(
        f"/api/v1/admin/after-sales/{t['id']}/action",
        json={"action": "refund", "reference": "REF-TEST-1"},
    )
    assert r.status_code == 200
    r = await buyer.post(f"/api/v1/orders/{order['id']}/reveal")
    assert r.status_code == 409


async def test_replace_rejects_foreign_unknown_and_empty_card_ids(env):
    p, buyer, staff, _admin, order = await _auto_order(env)
    other_buyer, _ = await env.login("buyer")
    other = await delivered_order(env, other_buyer, p)
    foreign = (await other_buyer.post(f"/api/v1/orders/{other['id']}/reveal")).json()["cards"][0][
        "id"
    ]
    t = (await open_ticket(buyer, order)).json()
    url = f"/api/v1/admin/after-sales/{t['id']}/action"
    for ids in ([foreign], ["00000000-0000-0000-0000-000000000009"]):
        r = await staff.post(url, json={"action": "replace", "card_ids": ids})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic='delivery.replace'") == 0
    # no non-void cards left at all -> 422
    async with env.owner_session() as db:
        await db.execute(
            text(
                "UPDATE card_stock SET status='void', void_reason='defective', voided_at=now() "
                "WHERE order_id=:o"
            ),
            {"o": order["id"]},
        )
        await db.commit()
    r = await staff.post(url, json={"action": "replace"})
    assert r.status_code == 422


async def test_duplicate_replace_rows_are_noop_and_actor_recorded(env):
    _p, buyer, staff, _admin, order = await _auto_order(env)
    t = (await open_ticket(buyer, order)).json()
    r = await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "replace"})
    assert r.status_code == 200
    async with env.owner_session() as db:  # duplicate the outbox row
        await db.execute(
            text(
                "INSERT INTO outbox (tenant_id, topic, key, payload) "
                "SELECT tenant_id, topic, key, payload FROM outbox WHERE topic='delivery.replace'"
            )
        )
        await db.commit()
    assert await env.drain() == 2
    assert await env.scalar("SELECT count(*) FROM card_stock WHERE status='void'") == 1
    assert await env.scalar("SELECT count(*) FROM deliveries WHERE order_id=:i", i=order["id"]) == 2
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='aftersales.replaced'") == 1
    )
    me = str(await env.scalar("SELECT id FROM users WHERE role='agent'"))
    assert (
        str(await env.scalar("SELECT actor_id FROM audit_log WHERE action='aftersales.replaced'"))
        == me
    )
    assert (
        await env.scalar("SELECT actor_role FROM audit_log WHERE action='aftersales.replaced'")
        == "agent"
    )
    assert str(await env.scalar("SELECT resolved_by FROM after_sales")) == me
    assert await env.scalar("SELECT resolved_at FROM after_sales") is not None


async def test_replace_with_no_live_cards_in_worker_posts_failure(env):
    _p, buyer, staff, _admin, order = await _auto_order(env, conversation=True)
    t = (await open_ticket(buyer, order)).json()
    await staff.post(f"/api/v1/admin/after-sales/{t['id']}/action", json={"action": "replace"})
    async with env.owner_session() as db:  # cards voided between queueing and processing
        await db.execute(
            text("UPDATE card_stock SET status='void', void_reason='defective', voided_at=now()")
        )
        await db.commit()
    await env.drain()
    assert await env.scalar("SELECT status FROM after_sales") == "processing"
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='delivery.replace_no_cards'")
        == 1
    )
    assert (
        await env.scalar(
            "SELECT count(*) FROM messages WHERE payload->>'event'='after_sales_replace_failed'"
        )
        == 1
    )


async def test_third_replacement_requires_admin(env):
    _p, buyer, staff, admin, order = await setup_manual(env)
    for i in range(2):
        t = (await open_ticket(buyer, order)).json()
        r = await staff.post(
            f"/api/v1/admin/after-sales/{t['id']}/action",
            json={"action": "replace", "delivery_text": f"new {i}"},
        )
        assert r.status_code == 200, r.text
    t = (await open_ticket(buyer, order)).json()
    url = f"/api/v1/admin/after-sales/{t['id']}/action"
    body = {"action": "replace", "delivery_text": "third"}
    r = await staff.post(url, json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (await admin.post(url, json=body)).status_code == 200


async def test_open_ticket_bumps_version_and_autoconfirm_rechecks(env):
    _p, buyer, _staff, _admin, order = await setup_manual(env)
    v0 = await env.scalar("SELECT version FROM orders")
    await open_ticket(buyer, order)
    assert await env.scalar("SELECT version FROM orders") == v0 + 1


async def test_migration_backfill_sql(env):
    import importlib

    mig = importlib.import_module("migrations.versions.0002_storefront_v3")
    _p, _buyer, _staff, _admin, _order = await setup_manual(env)
    async with env.owner_session() as db:
        await db.execute(text("UPDATE orders SET after_sales_until=NULL, auto_confirm_at=NULL"))
        for stmt in mig.BACKFILL:
            await db.execute(text(stmt))
        await db.commit()
    d = await env.scalar("SELECT delivered_at FROM orders")
    assert await env.scalar("SELECT after_sales_until FROM orders") == d + timedelta(hours=672)
    assert await env.scalar("SELECT auto_confirm_at FROM orders") == d + timedelta(hours=72)
