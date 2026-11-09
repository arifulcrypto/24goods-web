"""Issue #58 §4/§5 (review-v8b): every order reaches CS; avatars (hostile uploads, visibility)."""

from __future__ import annotations

import asyncio
import io
import json

from PIL import Image

from app.modules.me import service as me_service
from tests.helpers import buy, open_conversation
from tests.test_chat import png_bytes

API = "/api/v1"


async def _conv_count(env, buyer_id) -> int:
    return await env.scalar(
        "SELECT count(*) FROM conversations WHERE buyer_id=:b AND status='open'", b=buyer_id
    )


async def test_order_without_chat_gets_conversation_and_card(env):
    p = await env.product(mode="manual")
    buyer, user = await env.login("buyer")
    assert await _conv_count(env, user.id) == 0
    o = await buy(buyer, p)
    assert await _conv_count(env, user.id) == 1
    cid = await env.scalar("SELECT conversation_id FROM orders WHERE id=:i", i=o["id"])
    assert cid is not None
    kinds = await env.scalar(
        "SELECT count(*) FROM messages WHERE conversation_id=:c AND type='order_card'", c=cid
    )
    assert kinds == 1


async def test_two_orders_share_one_conversation(env):
    p = await env.product(mode="manual", price=1990)
    buyer, user = await env.login("buyer")
    await asyncio.gather(buy(buyer, p), buy(buyer, p))
    assert await _conv_count(env, user.id) == 1
    n = await env.scalar(
        "SELECT count(DISTINCT conversation_id) FROM orders WHERE buyer_id=:b", b=user.id
    )
    assert n == 1


async def test_staff_never_get_buyer_conversation(env):
    from app.modules.chat.service import conversation_id_for_order

    _, agent = await env.login("agent")
    async with env.owner_session(agent.tenant_id) as db:
        assert await conversation_id_for_order(db, agent) is None
    assert await _conv_count(env, agent.id) == 0


async def test_staff_sse_gets_order_created_paid_cancelled(env):
    p = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    pubsub = env.s.redis.pubsub()
    await pubsub.subscribe(f"rt:staff:{env.tenant_id}")
    await asyncio.sleep(0.05)
    o = await buy(buyer, p)
    r = await buyer.post(f"{API}/orders/{o['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    statuses = []
    for _ in range(40):
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
        if m:
            d = json.loads(m["data"])
            if d["event"] == "order":
                statuses.append(d["data"]["status"])
        if "cancelled" in statuses:
            break
    await pubsub.aclose()
    assert statuses[0] == "pending_payment" and "cancelled" in statuses


async def _put(client, data: bytes, name="a.png"):
    return await client.put(
        f"{API}/me/avatar", files={"file": (name, data, "application/octet-stream")}
    )


async def test_avatar_upload_reencoded_square_and_served(env, services):
    buyer, user = await env.login("buyer")
    r = await _put(buyer, png_bytes(size=(600, 300)))
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url and url.startswith(f"/api/v1/avatars/{user.id}")
    assert (await buyer.get(f"{API}/auth/me")).json()["avatar_url"] == url
    f = await buyer.get(url)
    assert f.status_code == 200
    assert f.headers["content-security-policy"] == "sandbox"
    assert f.headers["x-content-type-options"] == "nosniff"
    img = Image.open(io.BytesIO(f.content))
    assert img.size == (256, 256) and not img.getexif()
    key = await env.scalar("SELECT avatar_key FROM users WHERE id=:i", i=user.id)
    assert (services.upload_dir / key[:2] / key).is_file()
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='me.avatar_set'") == 1
    # replace removes the old file; delete clears it
    r = await _put(buyer, png_bytes(size=(64, 64)))
    assert not (services.upload_dir / key[:2] / key).exists()
    r = await buyer.delete(f"{API}/me/avatar")
    assert r.status_code == 200 and r.json()["avatar_url"] is None
    assert (await buyer.get(url)).status_code == 404


async def test_avatar_rejects_non_image_and_oversize(env):
    buyer, _ = await env.login("buyer")
    assert (await _put(buyer, b"GIF-not-really, just text" * 10)).status_code in (400, 413, 422)
    assert (await _put(buyer, b"<svg onload=alert(1)>")).status_code in (400, 413, 422)
    big = png_bytes(size=(64, 64)) + b"\x00" * (2 * 1024 * 1024 + 10)
    assert (await _put(buyer, big)).status_code in (400, 413, 422)
    assert (await _put(env.client(), png_bytes())).status_code == 401


async def test_avatar_visibility(env):
    a, ua = await env.login("buyer")
    b, _ub = await env.login("buyer")
    staff, us = await env.login("agent")
    for c in (a, staff):
        assert (await _put(c, png_bytes())).status_code == 200
    ua_url = f"{API}/avatars/{ua.id}"
    assert (await a.get(ua_url)).status_code == 200
    assert (await staff.get(ua_url)).status_code == 200  # staff of the tenant
    assert (await b.get(ua_url)).status_code == 404  # another buyer
    # staff avatar: hidden until the staff member wrote in the buyer's conversation
    s_url = f"{API}/avatars/{us.id}"
    assert (await a.get(s_url)).status_code == 404
    conv = await open_conversation(a)
    r = await staff.post(
        f"{API}/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "av-1", "type": "text", "text": "hi"},
    )
    assert r.status_code == 200, r.text
    assert (await a.get(s_url)).status_code == 200
    assert (await b.get(s_url)).status_code == 404
    msgs = (await a.get(f"{API}/conversations/{conv['id']}/messages")).json()
    items = msgs["items"] if isinstance(msgs, dict) else msgs
    sender = next(m["sender"] for m in items if m["sender"]["role"] != "buyer")
    assert sender["avatar_url"] and sender["avatar_url"].startswith(s_url)


async def test_avatar_cross_tenant_is_none(env):
    a, ua = await env.login("buyer")
    assert (await _put(a, png_bytes())).status_code == 200
    other_staff = await env.user("agent", tenant_id=env.other_tenant_id)
    async with env.owner_session(env.other_tenant_id) as db:
        assert await me_service.avatar_for_viewer(db, ua.id, other_staff) is None
    # same-tenant staff still can
    _, staff = await env.login("agent")
    async with env.owner_session(env.tenant_id) as db:
        assert await me_service.avatar_for_viewer(db, ua.id, staff) is not None
