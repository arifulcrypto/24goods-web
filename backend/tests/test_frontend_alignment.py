"""Behaviour agreed with the frontend (contract clarifications)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from app.modules.realtime.hub import RealtimeHub
from app.modules.realtime.publish import public_channel, staff_channel, user_channel
from tests.helpers import open_conversation, send_quote
from tests.test_chat import png_bytes


async def _drain_events(sub, timeout=0.5) -> list[dict]:
    out = []
    while True:
        try:
            out.append(await asyncio.wait_for(sub.queue.get(), timeout))
        except asyncio.TimeoutError:
            return out


async def test_quote_card_republished_with_same_id(env):
    buyer, user = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    q = await send_quote(agent, conv["id"], amount="9.90")
    card_id = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()[-1]["id"]
    hub = RealtimeHub(env.s.redis)
    sub = hub.subscribe([user_channel(user.id)])
    await hub.wait_ready()
    order = (await buyer.post(f"/api/v1/quotes/{q['quote_id']}/accept")).json()
    events = await _drain_events(sub)
    msgs = [e["data"]["message"] for e in events if e["event"] == "message"]
    card = next(m for m in msgs if m["id"] == card_id)
    assert card["type"] == "quote_card" and card["payload"]["status"] == "accepted"
    assert card["payload"]["order_id"] == order["id"]
    assert any(m["type"] == "order_card" for m in msgs)
    assert any(m["payload"] and m["payload"].get("event") == "quote_accepted" for m in msgs)
    conv_events = [e["data"] for e in events if e["event"] == "conversation"]
    assert conv_events and conv_events[-1]["last_message"]["payload"]["event"] == "quote_accepted"
    assert any(e["event"] == "order" for e in events)

    # withdraw on a second quote -> same quote_card id with status withdrawn + system message
    q2 = await send_quote(agent, conv["id"], amount="8.00")
    await _drain_events(sub, 0.2)
    await agent.post(f"/api/v1/admin/quotes/{q2['quote_id']}/withdraw")
    msgs = [e["data"]["message"] for e in await _drain_events(sub) if e["event"] == "message"]
    assert any(m["type"] == "quote_card" and m["payload"]["status"] == "withdrawn" for m in msgs)
    assert any(m["payload"].get("event") == "quote_withdrawn" for m in msgs if m["payload"])
    await hub.stop()


async def test_order_card_republished_on_price_change(env):
    product = await env.product(mode="manual", price=5000, floor=1000)
    buyer, user = await env.login("buyer")
    agent, _ = await env.login("agent")
    await open_conversation(buyer)
    order = (
        await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    ).json()
    hub = RealtimeHub(env.s.redis)
    sub = hub.subscribe([user_channel(user.id)])
    await hub.wait_ready()
    await agent.patch(
        f"/api/v1/admin/orders/{order['id']}/price", json={"amount": "40.00", "reason": "discount"}
    )
    msgs = [e["data"]["message"] for e in await _drain_events(sub) if e["event"] == "message"]
    card = next(m for m in msgs if m["type"] == "order_card")
    assert card["payload"]["amount"] == "40.00"
    await hub.stop()


async def test_staff_read_unread_and_assign_messages(env):
    buyer, _ = await env.login("buyer")
    agent, agent_user = await env.login("agent", name="Lily")
    other, other_user = await env.login("agent", name="Max")
    conv = await open_conversation(buyer)
    url = f"/api/v1/conversations/{conv['id']}/messages"
    m1 = (await buyer.post(url, json={"client_msg_id": "u1", "type": "text", "text": "a"})).json()
    await buyer.post(url, json={"client_msg_id": "u2", "type": "text", "text": "b"})
    items = (await agent.get("/api/v1/admin/conversations")).json()["items"]
    assert items[0]["unread"] == 2
    r = await agent.post(
        f"/api/v1/admin/conversations/{conv['id']}/read", json={"message_id": m1["id"]}
    )
    assert r.status_code == 204
    assert (await other.get("/api/v1/admin/conversations")).json()["items"][0]["unread"] == 1

    cid = conv["id"]
    await agent.post(
        f"/api/v1/admin/conversations/{cid}/assign", json={"agent_id": str(agent_user.id)}
    )
    r = await agent.post(
        f"/api/v1/admin/conversations/{cid}/assign", json={"agent_id": str(other_user.id)}
    )
    assert r.json()["assigned_agent"]["display_name"] == "Max"
    await agent.post(f"/api/v1/admin/conversations/{cid}/close")
    msgs = (await buyer.get(url)).json()
    sys = [
        (m["payload"]["event"], m["payload"].get("name"), m["text"])
        for m in msgs
        if m["type"] == "system"
    ]
    assert sys == [
        ("agent_joined", "Lily", "Lily joined the conversation."),
        ("agent_left", "Lily", "Lily left the conversation."),
        ("agent_joined", "Max", "Max joined the conversation."),
        ("conversation_closed", None, "The conversation was closed."),
    ]


async def test_messages_cursor_semantics(env):
    buyer, _ = await env.login("buyer")
    conv = await open_conversation(buyer)
    url = f"/api/v1/conversations/{conv['id']}/messages"
    ids = []
    for i in range(7):
        r = await buyer.post(url, json={"client_msg_id": f"c{i}", "type": "text", "text": str(i)})
        ids.append(r.json()["id"])
    assert [m["id"] for m in (await buyer.get(url, params={"limit": 3})).json()] == ids[-3:]
    page = (await buyer.get(url, params={"before_id": ids[4], "limit": 3})).json()
    assert [m["id"] for m in page] == ids[1:4]
    page = (await buyer.get(url, params={"after_id": ids[2], "limit": 100})).json()
    assert [m["id"] for m in page] == ids[3:]


async def test_admin_orders_buyer_filter_floor_price_and_product_detail(env):
    product = await env.product(mode="manual", price=5000, floor=4200)
    b1, u1 = await env.login("buyer")
    b2, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    o1 = (await b1.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 2})).json()
    await b2.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    page = (await agent.get("/api/v1/admin/orders", params={"buyer_id": str(u1.id)})).json()
    assert page["total"] == 1 and page["items"][0]["id"] == o1["id"]
    detail = (await agent.get(f"/api/v1/admin/orders/{o1['id']}")).json()
    assert detail["floor_price"] == "42.00"
    assert detail["events"][0]["from"] is None and detail["events"][0]["to"] == "pending_payment"
    assert isinstance(detail["events"][0]["actor"], str)
    p = (await agent.get(f"/api/v1/admin/products/{product.id}")).json()
    assert p["id"] == str(product.id) and p["floor_price"] == "42.00" and "title_i18n" in p
    stats = (await agent.get("/api/v1/admin/stats")).json()
    assert stats["currency"] == "CNY"


async def test_product_images_rules(env):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login("buyer")
    p = await env.product()
    up = await admin.post(
        f"/api/v1/admin/products/{p.id}/images", files={"file": ("a.png", png_bytes(), "image/png")}
    )
    url1 = up.json()["url"]
    detail = (await admin.get(f"/api/v1/admin/products/{p.id}")).json()
    assert detail["cover"] == url1 and detail["images"] == [url1]
    # admin's own chat-style upload can be attached (and becomes public)
    own = (
        await admin.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("b.png", png_bytes(), "image/png")},
        )
    ).json()
    r = await admin.patch(f"/api/v1/admin/products/{p.id}", json={"images": [own["url"], url1]})
    assert r.status_code == 200 and r.json()["images"] == [own["url"], url1]
    assert (await env.client().get(own["url"])).status_code == 200
    # someone else's private upload can never be published
    theirs = (
        await buyer.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("c.png", png_bytes(), "image/png")},
        )
    ).json()
    r = await admin.patch(f"/api/v1/admin/products/{p.id}", json={"images": [theirs["url"]]})
    assert r.status_code == 422
    assert (await env.client().get(theirs["url"])).status_code == 401


async def test_buyer_agent_status_event_is_aggregated(env):
    a1, _ = await env.login("agent")
    a2, u2 = await env.login("agent")
    await a1.put("/api/v1/admin/agent/status", json={"status": "online", "message": "hi"})
    hub = RealtimeHub(env.s.redis)
    pub = hub.subscribe([public_channel(env.tenant_id)])
    staff = hub.subscribe([staff_channel(env.tenant_id)])
    await hub.wait_ready()
    await a2.put("/api/v1/admin/agent/status", json={"status": "away"})
    (ev,) = await _drain_events(pub)
    assert ev["data"]["status"] == "online" and ev["data"]["message"] == "hi"  # best, not a2
    (sev,) = [e for e in await _drain_events(staff) if e["event"] == "agent_status"]
    assert sev["data"]["status"] == "away" and sev["data"]["agent_id"] == str(u2.id)
    await hub.stop()


async def test_relative_pay_url_and_prod_guards(env, services):
    from app.core.config import Settings
    from app.core.resources import zpay_config
    from app.payments.zpay import ZPayClient
    from tests.conftest import make_settings

    s = make_settings(services, zpay_gateway="/mockpay")
    url = ZPayClient(zpay_config(s), None).build_pay_url("GABCDEFGHIJKL", "x", Decimal("1.00"))  # type: ignore[arg-type]
    assert url.startswith("/mockpay/submit.php?") and not urlsplit(url).netloc
    with pytest.raises(ValueError, match="https"):
        Settings(_env_file=None, env="prod", zpay_gateway="/mockpay", cookie_secure=True)
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(_env_file=None, env="prod", zpay_gateway="https://zpayz.cn")


async def test_voice_mp4_and_webp_image(env):
    import io

    from PIL import Image

    buyer, _ = await env.login("buyer")
    mp4 = b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 500
    r = await buyer.post(
        "/api/v1/uploads",
        data={"kind": "voice", "duration_ms": "3000"},
        files={"file": ("v.m4a", mp4, "audio/mp4")},
    )
    assert r.status_code == 200 and r.json()["mime"] == "audio/mp4"
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), (1, 2, 3)).save(buf, "WEBP")
    r = await buyer.post(
        "/api/v1/uploads",
        data={"kind": "image"},
        files={"file": ("i.webp", buf.getvalue(), "image/webp")},
    )
    assert r.status_code == 200 and r.json()["kind"] == "image"
