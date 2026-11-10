from __future__ import annotations

import io

from PIL import Image

from tests.helpers import open_conversation


def png_bytes(size=(64, 48), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


async def test_conversation_find_or_create_and_product_cards(env):
    p1 = await env.product(title="P1")
    p2 = await env.product(title="P2")
    buyer, _ = await env.login("buyer")
    c1 = await open_conversation(buyer, p1.id)
    c2 = await open_conversation(buyer, p1.id)
    assert c1["id"] == c2["id"]
    c3 = await open_conversation(buyer, p2.id)
    assert c3["id"] == c1["id"] and c3["product"]["title"] == "P2"
    msgs = (await buyer.get(f"/api/v1/conversations/{c1['id']}/messages")).json()
    assert [m["payload"]["title"] for m in msgs] == ["P1", "P2"]
    assert all(m["type"] == "product_card" for m in msgs)
    convs = (await buyer.get("/api/v1/conversations")).json()
    assert len(convs) == 1 and convs[0]["last_message"]["id"] == msgs[-1]["id"]


async def test_send_text_idempotent_and_limits(env):
    buyer, user = await env.login("buyer")
    conv = await open_conversation(buyer)
    url = f"/api/v1/conversations/{conv['id']}/messages"
    body = {"client_msg_id": "c-1", "type": "text", "text": "  hello  "}
    m1 = (await buyer.post(url, json=body)).json()
    m2 = (await buyer.post(url, json=body)).json()
    assert m1["id"] == m2["id"] and m1["text"] == "hello"
    assert m1["sender"] == {
        "id": str(user.id),
        "role": "buyer",
        "display_name": "Buyer",
        "avatar_url": None,
        "avatar_preset": None,
    }
    assert await env.scalar("SELECT count(*) FROM messages WHERE type='text'") == 1
    r = await buyer.post(url, json={"client_msg_id": "c-2", "type": "text", "text": "x" * 2001})
    assert r.status_code == 422
    r = await buyer.post(url, json={"client_msg_id": "c-3", "type": "text", "text": "   "})
    assert r.status_code == 422
    r = await buyer.post(url, json={"client_msg_id": "c-4", "type": "system", "text": "x"})
    assert r.status_code == 422  # buyers cannot forge system/quote/order cards


async def test_message_rate_limit(env, monkeypatch):
    # The limiter is a fixed 60 s window; pin the clock it reads to mid-window so a minute
    # boundary can never split the 31 requests (that made this test flaky).
    import time
    from types import SimpleNamespace

    from app.core import ratelimit

    mid = (int(time.time()) // 60) * 60 + 30
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=lambda: mid))
    buyer, _ = await env.login("buyer")
    conv = await open_conversation(buyer)
    url = f"/api/v1/conversations/{conv['id']}/messages"
    codes = [
        (
            await buyer.post(url, json={"client_msg_id": f"m{i}", "type": "text", "text": "hi"})
        ).status_code
        for i in range(31)
    ]
    assert codes[:30] == [200] * 30 and codes[30] == 429


async def test_image_message_and_pagination_and_unread(env):
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    up = await buyer.post(
        "/api/v1/uploads",
        data={"kind": "image"},
        files={"file": ("cat.png", png_bytes(), "image/png")},
    )
    assert up.status_code == 200, up.text
    att = up.json()
    assert att["kind"] == "image" and att["mime"] == "image/jpeg" and att["width"] == 64
    url = f"/api/v1/conversations/{conv['id']}/messages"
    m = (
        await buyer.post(
            url, json={"client_msg_id": "img-1", "type": "image", "attachment_id": att["id"]}
        )
    ).json()
    assert m["type"] == "image" and m["attachment"]["url"] == f"/api/v1/files/{att['id']}"
    # staff sees it and replies; the reply auto-assigns the conversation
    staff_url = f"/api/v1/admin/conversations/{conv['id']}/messages"
    assert (await agent.get(staff_url)).json()[-1]["id"] == m["id"]
    reply = await agent.post(
        staff_url, json={"client_msg_id": "a-1", "type": "text", "text": "Nice picture"}
    )
    assert reply.status_code == 200 and reply.json()["sender"]["role"] == "agent"
    convs = (await buyer.get("/api/v1/conversations")).json()
    assert convs[0]["unread"] == 1 and convs[0]["assigned_agent"] is not None
    r = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/read", json={"message_id": reply.json()["id"]}
    )
    assert r.status_code == 204
    assert (await buyer.get("/api/v1/conversations")).json()[0]["unread"] == 0
    after = (await buyer.get(url, params={"after_id": m["id"]})).json()
    assert [x["id"] for x in after] == [reply.json()["id"]]
    before = (await buyer.get(url, params={"before_id": reply.json()["id"], "limit": 1})).json()
    assert [x["id"] for x in before] == [m["id"]]
    staff_list = (await agent.get("/api/v1/admin/conversations", params={"filter": "mine"})).json()
    assert staff_list["total"] == 1
    assert (await agent.get("/api/v1/admin/conversations", params={"filter": "unassigned"})).json()[
        "total"
    ] == 0


async def test_cannot_attach_someone_elses_upload(env):
    a, _ = await env.login("buyer")
    b, _ = await env.login("buyer")
    att = (
        await a.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("x.png", png_bytes(), "image/png")},
        )
    ).json()
    conv_b = await open_conversation(b)
    r = await b.post(
        f"/api/v1/conversations/{conv_b['id']}/messages",
        json={"client_msg_id": "x", "type": "image", "attachment_id": att["id"]},
    )
    assert r.status_code == 404


async def test_assign_close_and_quote_withdraw(env):
    buyer, _ = await env.login("buyer")
    agent, agent_user = await env.login("agent")
    conv = await open_conversation(buyer)
    r = await agent.post(
        f"/api/v1/admin/conversations/{conv['id']}/assign", json={"agent_id": str(agent_user.id)}
    )
    assert r.status_code == 200 and r.json()["assigned_agent"]["id"] == str(agent_user.id)
    q = (
        await agent.post(
            f"/api/v1/admin/conversations/{conv['id']}/quotes",
            json={"title": "Deal", "amount": "9.99", "qty": 1, "expires_in_minutes": 5},
        )
    ).json()
    r = await agent.post(f"/api/v1/admin/quotes/{q['quote_id']}/withdraw")
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    r = await buyer.post(f"/api/v1/quotes/{q['quote_id']}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"
    for bad in [
        {"expires_in_minutes": 4},
        {"expires_in_minutes": 1441},
        {"amount": "1.234"},
        {"amount": "-1"},
    ]:
        body = {"title": "Deal", "amount": "9.99", "qty": 1, "expires_in_minutes": 30, **bad}
        r = await agent.post(f"/api/v1/admin/conversations/{conv['id']}/quotes", json=body)
        assert r.status_code == 422, bad
    r = await agent.post(f"/api/v1/admin/conversations/{conv['id']}/close")
    assert r.json()["status"] == "closed"
    r = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "late", "type": "text", "text": "hello?"},
    )
    assert r.status_code == 409
    assert (await open_conversation(buyer))["id"] != conv["id"]  # a fresh one opens
