from __future__ import annotations

import asyncio
import json

from app.modules.realtime.hub import RealtimeHub, sse_events
from app.modules.realtime.publish import publish, staff_channel, user_channel
from tests.helpers import open_conversation


async def test_hub_fanout_and_sse_format(env):
    hub = RealtimeHub(env.s.redis)
    sub = hub.subscribe([user_channel("u1")])
    other = hub.subscribe([user_channel("u2")])
    await hub.wait_ready()
    gen = sse_events(hub, sub, heartbeat_s=0.2)
    assert await gen.__anext__() == "retry: 4000\n\n"
    await publish(env.s.redis, user_channel("u1"), "message", {"conversation_id": "c", "x": 1})
    chunk = await asyncio.wait_for(gen.__anext__(), 2)
    assert chunk.startswith("event: message\ndata: ")
    assert json.loads(chunk.split("data: ", 1)[1]) == {"conversation_id": "c", "x": 1}
    assert await asyncio.wait_for(gen.__anext__(), 2) == ": ping\n\n"  # heartbeat
    assert other.queue.empty()
    await gen.aclose()
    assert user_channel("u1") not in hub._subs
    await hub.stop()


async def test_chat_message_published_to_buyer_and_staff(env):
    buyer, user = await env.login("buyer")
    hub = RealtimeHub(env.s.redis)
    sub_buyer = hub.subscribe([user_channel(user.id)])
    sub_staff = hub.subscribe([staff_channel(env.tenant_id)])
    await hub.wait_ready()
    conv = await open_conversation(buyer)
    await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "rt-1", "type": "text", "text": "ping"},
    )
    for sub in (sub_buyer, sub_staff):
        events = []
        while len(events) < 2:
            events.append(await asyncio.wait_for(sub.queue.get(), 2))
        kinds = {e["event"] for e in events}
        assert kinds == {"message", "conversation"}
        msg = next(e for e in events if e["event"] == "message")["data"]
        assert msg["message"]["text"] == "ping" and msg["conversation_id"] == conv["id"]
    await hub.stop()


async def test_stream_requires_auth(env):
    r = await env.client().get("/api/v1/stream")
    assert r.status_code == 401
    buyer, _ = await env.login("buyer")
    r = await buyer.get("/api/v1/admin/stream")
    assert r.status_code == 403
