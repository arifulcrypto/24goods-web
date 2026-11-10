"""review-v11 W2: replies, edit, pins list, typing, notify defaults, quotes for variants."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.modules.realtime.hub import RealtimeHub
from app.modules.realtime.publish import staff_channel, user_channel
from tests.helpers import open_conversation
from tests.test_variants import add_variant

API = "/api/v1"


async def send(client, conv, key, body="hello", **extra):
    r = await client.post(
        f"{API}/conversations/{conv['id']}/messages",
        json={"client_msg_id": key, "type": "text", "text": body, **extra},
    )
    return r


async def staff_send(client, conv, key, body="hi", **extra):
    return await client.post(
        f"{API}/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": key, "type": "text", "text": body, **extra},
    )


async def test_reply_snapshot_and_validation(env):
    buyer, _ = await env.login("buyer", name="Bea")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    orig = (await send(buyer, conv, "o1", "x" * 200)).json()
    r = await staff_send(agent, conv, "r1", "answer", reply_to=orig["id"])
    assert r.status_code == 200, r.text
    rt = r.json()["payload"]["reply_to"]
    assert rt["id"] == orig["id"] and rt["sender_name"] == "Bea" and rt["sender_role"] == "buyer"
    assert rt["preview"] == "x" * 120 and rt["type"] == "text"
    # other conversation -> refused
    buyer2, _ = await env.login("buyer", email="b2@example.com")
    conv2 = await open_conversation(buyer2)
    r = await send(buyer2, conv2, "c2", "y", reply_to=orig["id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "reply_invalid"
    # unknown id and system lines refused
    assert (await send(buyer, conv, "c3", "y", reply_to=999999)).status_code == 422
    # recalled (redacted) refused, and the quoted preview of earlier replies is scrubbed
    assert (
        await buyer.post(f"{API}/chat/messages/{orig['id']}/recall", json={})
    ).status_code == 200
    assert (await send(buyer, conv, "c4", "y", reply_to=orig["id"])).status_code == 422
    msgs = (await buyer.get(f"{API}/conversations/{conv['id']}/messages")).json()
    reply = next(m for m in msgs if m["client_msg_id"] == "r1")
    assert reply["payload"]["reply_to"]["preview"] == ""


async def test_edit_rules(env):
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    m = (await send(buyer, conv, "e1", "first")).json()
    path = f"{API}/chat/messages/{m['id']}"
    assert (await agent.patch(path, json={"text": "hack"})).status_code == 403  # not the sender
    stranger, _ = await env.login(email="stranger@example.com")
    assert (await stranger.patch(path, json={"text": "hack"})).status_code == 404
    assert (await buyer.patch(path, json={"text": "   "})).status_code == 422
    r = await buyer.patch(path, json={"text": "second"})
    assert r.status_code == 200 and r.json()["text"] == "second"
    assert r.json()["payload"]["edited_at"]
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='chat.message_edited'") == 1
    )
    # too old
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE messages SET created_at=:at WHERE id=:id"),
            {"at": datetime.now(timezone.utc) - timedelta(hours=49), "id": m["id"]},
        )
        await db.commit()
    assert (await buyer.patch(path, json={"text": "late"})).status_code == 409
    # non-text and recalled
    s = (await send(buyer, conv, "e2", "gone")).json()
    await buyer.post(f"{API}/chat/messages/{s['id']}/recall", json={})
    assert (
        await buyer.patch(f"{API}/chat/messages/{s['id']}", json={"text": "x"})
    ).status_code == 403
    card = await env.scalar("SELECT id FROM messages WHERE type='system' LIMIT 1")
    if card:
        assert (
            await buyer.patch(f"{API}/chat/messages/{card}", json={"text": "x"})
        ).status_code in (
            403,
            404,
        )


async def test_typing_authz_rate_limit_and_event(env):
    buyer, user = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    hub = RealtimeHub(env.s.redis)
    sub_buyer = hub.subscribe([user_channel(user.id)])
    sub_staff = hub.subscribe([staff_channel(env.tenant_id)])
    await hub.wait_ready()
    assert (await buyer.post(f"{API}/conversations/{conv['id']}/typing")).status_code == 204
    ev = await asyncio.wait_for(sub_staff.queue.get(), 2)
    assert ev["event"] == "typing" and ev["data"]["role"] == "buyer"
    assert ev["data"]["conversation_id"] == conv["id"] and ev["data"]["until"]
    # 1 per 3 s per user
    assert (await buyer.post(f"{API}/conversations/{conv['id']}/typing")).status_code == 429
    assert (await agent.post(f"{API}/admin/conversations/{conv['id']}/typing")).status_code == 204
    ev = await asyncio.wait_for(sub_buyer.queue.get(), 2)
    assert ev["event"] == "typing" and ev["data"]["role"] == "agent"
    # not a participant / cross tenant
    stranger, _ = await env.login(email="stranger2@example.com")
    assert (await stranger.post(f"{API}/conversations/{conv['id']}/typing")).status_code == 404
    other_admin, _ = await env.login("agent", tenant_id=env.other_tenant_id)
    r = await other_admin.post(f"{API}/admin/conversations/{conv['id']}/typing")
    assert r.status_code in (401, 403, 404)
    # buyers cannot use the staff endpoint
    assert (await buyer.post(f"{API}/admin/conversations/{conv['id']}/typing")).status_code == 403
    await hub.stop()


async def test_pins_list_scoping_and_system_line(env):
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    ids = []
    for i in range(4):
        ids.append((await send(buyer, conv, f"p{i}", f"msg {i}")).json()["id"])
    for mid in ids[:3]:
        assert (
            await buyer.post(f"{API}/chat/messages/{mid}/pin", json={"pinned": True})
        ).status_code == 200
    assert (
        await agent.post(f"{API}/chat/messages/{ids[3]}/pin", json={"pinned": True})
    ).status_code == 409
    pins = (await buyer.get(f"{API}/conversations/{conv['id']}/pins")).json()
    assert [p["id"] for p in pins] == [ids[2], ids[1], ids[0]]  # newest first, max 3
    staff_pins = (await agent.get(f"{API}/admin/conversations/{conv['id']}/pins")).json()
    assert [p["id"] for p in staff_pins] == [p["id"] for p in pins]
    msgs = (await buyer.get(f"{API}/conversations/{conv['id']}/messages")).json()
    assert [m["payload"]["event"] for m in msgs if m["type"] == "system"].count(
        "message_pinned"
    ) == 3
    # own pin lines do not create an unread badge for the buyer
    (view,) = (await buyer.get(f"{API}/conversations")).json()
    assert view["unread"] == 0
    # authz
    stranger, _ = await env.login(email="stranger3@example.com")
    assert (await stranger.get(f"{API}/conversations/{conv['id']}/pins")).status_code == 404
    other, _ = await env.login("agent", tenant_id=env.other_tenant_id)
    assert (await other.get(f"{API}/admin/conversations/{conv['id']}/pins")).status_code in (
        401,
        404,
    )
    assert (
        await other.post(f"{API}/chat/messages/{ids[0]}/pin", json={"pinned": False})
    ).status_code in (401, 404)
    # unpin posts a line and removes it from the list
    await buyer.post(f"{API}/chat/messages/{ids[2]}/pin", json={"pinned": False})
    pins = (await buyer.get(f"{API}/conversations/{conv['id']}/pins")).json()
    assert [p["id"] for p in pins] == [ids[1], ids[0]]


async def test_notify_defaults_in_site(env):
    anon = env.client()
    d = (await anon.get(f"{API}/site")).json()["notify_defaults"]["buyer"]
    assert d == {"sound": True, "desktop": False}
    admin, _ = await env.login("admin")
    r = await admin.put(
        f"{API}/admin/site",
        json={"notifications": {"buyer_sound": False, "buyer_desktop": True}},
    )
    assert r.status_code == 200, r.text
    d = (await anon.get(f"{API}/site")).json()["notify_defaults"]["buyer"]
    assert d == {"sound": False, "desktop": True}


async def _quote(agent, conv, **extra):
    body = {"title": "Deal", "amount": "7.00", "qty": 1, "expires_in_minutes": 30, **extra}
    return await agent.post(f"{API}/admin/conversations/{conv['id']}/quotes", json=body)


async def test_quote_for_variant_product(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    p = await env.product(mode="manual", price=1990, floor=100)
    a = await add_variant(admin, p, "A", "5.00", floor="3.00", manual_stock=1)
    b = await add_variant(admin, p, "B", "8.00", floor="3.00")
    plain = await env.product(mode="manual", price=1000, floor=100, title="Plain")
    conv = await open_conversation(buyer)
    # variant required when the product has active variants
    r = await _quote(agent, conv, product_id=str(p.id))
    assert r.status_code == 422 and r.json()["error"]["code"] == "variant_required"
    # below the variant floor
    assert (
        await _quote(agent, conv, product_id=str(p.id), variant_id=a["id"], amount="2.00")
    ).status_code == 422
    # foreign / unknown variant, variant of another product
    ghost = "00000000-0000-0000-0000-000000000001"
    assert (await _quote(agent, conv, product_id=str(p.id), variant_id=ghost)).status_code == 404
    assert (
        await _quote(agent, conv, product_id=str(plain.id), variant_id=a["id"])
    ).status_code == 404
    assert (await _quote(agent, conv, variant_id=a["id"])).status_code == 422
    # product without variants unchanged
    assert (await _quote(agent, conv, product_id=str(plain.id))).status_code == 200
    # happy path
    q = (await _quote(agent, conv, product_id=str(p.id), variant_id=a["id"])).json()
    assert q["variant_id"] == a["id"] and q["variant_name"] == "A"
    r = await buyer.post(f"{API}/quotes/{q['quote_id']}/accept")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["amount"] == "7.00" and o["variant_id"] == a["id"] and o["variant_name"] == "A"
    assert await env.scalar("SELECT manual_stock FROM product_variants WHERE id=:i", i=a["id"]) == 0
    # second quote on the sold-out variant fails per-variant stock
    q2 = (await _quote(agent, conv, product_id=str(p.id), variant_id=a["id"])).json()
    assert (await buyer.post(f"{API}/quotes/{q2['quote_id']}/accept")).status_code == 409
    # variant B is independent
    q3 = (await _quote(agent, conv, product_id=str(p.id), variant_id=b["id"])).json()
    assert (await buyer.post(f"{API}/quotes/{q3['quote_id']}/accept")).status_code == 200
    # archived variant refused for new quotes
    await admin.patch(
        f"{API}/admin/products/{p.id}/variants/{b['id']}", json={"status": "archived"}
    )
    r = await _quote(agent, conv, product_id=str(p.id), variant_id=b["id"])
    assert r.status_code == 422
