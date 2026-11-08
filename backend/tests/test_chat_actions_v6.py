"""Sender-only bounded recall, shared pin authorization and private media revocation."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from tests.helpers import open_conversation
from tests.test_chat import png_bytes


async def _message(buyer, conv, key="recall1", **body):
    r = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": key, "type": "text", "text": "private", **body},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_sender_recall_window_idor_and_pin(env):
    buyer, _ = await env.login()
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    m = await _message(buyer, conv)
    path = f"/api/v1/chat/messages/{m['id']}"
    assert (await agent.post(path + "/recall", json={})).status_code == 403
    assert (await agent.post(path + "/pin", json={"pinned": True})).json()["payload"]["pinned"]
    stranger, _ = await env.login(email="other@example.com")
    assert (await stranger.post(path + "/pin", json={"pinned": False})).status_code == 404
    r = await buyer.post(path + "/recall", json={})
    assert r.status_code == 200, r.text
    assert r.json()["text"] is None and r.json()["payload"]["recalled"]
    assert (await buyer.post(path + "/recall", json={})).status_code == 200
    m = await _message(buyer, conv, "late")
    async with env.owner_session() as db:
        await db.execute(
            text("UPDATE messages SET created_at=:at WHERE id=:id"),
            {"at": datetime.now(timezone.utc) - timedelta(seconds=181), "id": m["id"]},
        )
        await db.commit()
    assert (await buyer.post(f"/api/v1/chat/messages/{m['id']}/recall", json={})).status_code == 409


async def test_image_thumbnail_is_authorized_and_recall_revokes_media(env):
    buyer, _ = await env.login()
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    up = await buyer.post(
        "/api/v1/uploads",
        data={"kind": "image"},
        files={"file": ("safe.png", png_bytes(size=(1200, 800)), "image/png")},
    )
    assert up.status_code == 200, up.text
    att = up.json()
    m = await _message(buyer, conv, "image", type="image", text=None, attachment_id=att["id"])
    thumb = await agent.get(att["url"] + "?variant=small")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    assert "no-store" in thumb.headers["cache-control"]
    stranger, _ = await env.login(email="not-a-party@example.com")
    assert (await stranger.get(att["url"] + "?variant=small")).status_code == 404
    r = await buyer.post(f"/api/v1/chat/messages/{m['id']}/recall", json={})
    assert r.status_code == 200, r.text
    for client in [buyer, agent]:
        assert (await client.get(att["url"])).status_code == 404
        assert (await client.get(att["url"] + "?variant=small")).status_code == 404
    retry = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "reuse-deleted", "type": "image", "attachment_id": att["id"]},
    )
    assert retry.status_code == 404
