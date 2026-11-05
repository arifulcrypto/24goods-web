"""A buyer must never read or act on another buyer's conversations, orders or files."""

from __future__ import annotations

from tests.helpers import open_conversation, send_quote
from tests.test_chat import png_bytes


async def test_buyer_isolation(env):
    product = await env.product(mode="auto_card")
    await env.import_codes(product, ["IDOR-0001", "IDOR-0002"])
    alice, _ = await env.login("buyer")
    mallory, _ = await env.login("buyer")
    agent, _ = await env.login("agent")

    conv = await open_conversation(alice, product.id)
    att = (
        await alice.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("a.png", png_bytes(), "image/png")},
        )
    ).json()
    await alice.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "1", "type": "image", "attachment_id": att["id"]},
    )
    quote = await send_quote(agent, conv["id"])
    order = (
        await alice.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    ).json()

    cid, oid = conv["id"], order["id"]
    checks = [
        ("GET", f"/api/v1/conversations/{cid}/messages", None),
        (
            "POST",
            f"/api/v1/conversations/{cid}/messages",
            {"client_msg_id": "evil", "type": "text", "text": "hi"},
        ),
        ("POST", f"/api/v1/conversations/{cid}/read", {"message_id": 1}),
        ("GET", f"/api/v1/orders/{oid}", None),
        ("GET", f"/api/v1/orders/{order['order_no']}", None),
        ("POST", f"/api/v1/orders/{oid}/pay", {"channel": "zpay_alipay"}),
        ("POST", f"/api/v1/orders/{oid}/reveal", None),
        ("POST", f"/api/v1/orders/{oid}/confirm", None),
        ("POST", f"/api/v1/quotes/{quote['quote_id']}/accept", None),
        ("GET", f"/api/v1/files/{att['id']}", None),
    ]
    for method, url, body in checks:
        r = await mallory.request(method, url, json=body)
        assert r.status_code == 404, (method, url, r.status_code)
        assert r.json()["error"]["code"] == "not_found"
    assert (await mallory.get("/api/v1/conversations")).json() == []
    assert (await mallory.get("/api/v1/orders")).json()["total"] == 0

    # anonymous
    anon = env.client()
    assert (await anon.get(f"/api/v1/files/{att['id']}")).status_code == 401
    assert (await anon.get(f"/api/v1/orders/{oid}")).status_code == 401
    # owner and staff can read the file
    assert (await alice.get(f"/api/v1/files/{att['id']}")).status_code == 200
    assert (await agent.get(f"/api/v1/files/{att['id']}")).status_code == 200


async def test_internal_reveal_requires_token_and_owner(env):
    import httpx

    from tests.conftest import INTERNAL_TOKEN

    buyer, user = await env.login("buyer")
    _other, other_user = await env.login("buyer")
    product = await env.product()
    o = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    # out of stock -> create one with stock for the order
    await env.import_codes(product, ["REV-0001"])
    o = (await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    d = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.delivery_app), base_url="http://d")
    try:
        body = {"order_id": o["id"], "buyer_id": str(user.id)}
        assert (await d.post("/internal/reveal", json=body)).status_code == 401
        r = await d.post("/internal/reveal", json=body, headers={"X-Internal-Token": "x" * 43})
        assert r.status_code == 401
        h = {"X-Internal-Token": INTERNAL_TOKEN}
        r = await d.post(
            "/internal/reveal", json={**body, "buyer_id": str(other_user.id)}, headers=h
        )
        assert r.status_code == 404
        r = await d.post("/internal/reveal", json=body, headers=h)
        assert r.status_code == 409  # not delivered: never decrypt
        assert (
            await env.scalar("SELECT count(*) FROM audit_log WHERE action='reveal.refused_state'")
            == 1
        )
    finally:
        await d.aclose()
