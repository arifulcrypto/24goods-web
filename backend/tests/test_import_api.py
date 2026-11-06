"""Admin import endpoints: RBAC, rate limit, audit, offline fetch via mock transport."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.modules.admin import importer as import_api
from tests.test_chat import png_bytes
from tests.test_commerce_v4 import set_settings

FIX = Path(__file__).parent / "fixtures" / "import"
PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def fake_net(monkeypatch):
    calls: list[httpx.Request] = []
    table = {
        "gmailbuy.com": [PUBLIC_IP],
        "go.ka8.ai": [PUBLIC_IP],
        "img.test": [PUBLIC_IP],
        "evil.test": ["10.0.0.9"],
    }

    async def resolver(host, port):
        return table[host]

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        host, path = req.headers["host"], req.url.path
        if host == "gmailbuy.com":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=(FIX / "gmailbuy.html").read_bytes(),
            )
        if host == "go.ka8.ai" and path.startswith("/api/v1/public/products/"):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=(FIX / "ka8_api.json").read_bytes(),
            )
        if host == "img.test" and path == "/a.png":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes())
        if host == "img.test" and path == "/fake.png":
            return httpx.Response(
                200, headers={"content-type": "image/png"}, content=b"<html>not an image</html>"
            )
        return httpx.Response(404)

    monkeypatch.setattr(import_api, "_resolver", resolver)
    monkeypatch.setattr(import_api, "_transport", httpx.MockTransport(handler))
    return calls


URL = "/api/v1/admin/products/import/fetch"


async def test_requires_admin(env, fake_net):
    anon = env.client()
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    for path, body in (
        (URL, {"url": "https://gmailbuy.com/home/goods.html?id=8"}),
        ("/api/v1/admin/products/import/parse", {"text": "x y"}),
        ("/api/v1/admin/content/images/fetch", {"url": "https://img.test/a.png"}),
    ):
        assert (await anon.post(path, json=body)).status_code == 401
        assert (await buyer.post(path, json=body)).status_code == 403
        assert (await agent.post(path, json=body)).status_code == 403
    assert fake_net == []


async def test_fetch_gmailbuy_with_markup_and_audit(env, fake_net):
    await set_settings(
        env, {"import": {"markup_kind": "fixed", "markup_value": 150, "round_to_minor": 100}}
    )
    admin, u = await env.login("admin")
    r = await admin.post(URL, json={"url": "https://gmailbuy.com/home/goods.html?id=8&sid=8"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["price_minor"] == 480 and d["suggested_price_minor"] == 700  # 480+150 -> 630 -> 700
    assert d["title"].startswith("【优质】") and d["site"] == "gmailbuy.com"
    assert d["source_url"] == "https://gmailbuy.com/home/goods.html?id=8&sid=8"
    # connection was pinned to the vetted IP, Host kept
    assert fake_net[0].url.host == PUBLIC_IP and fake_net[0].headers["host"] == "gmailbuy.com"
    row = await env.scalar(
        "SELECT target FROM audit_log WHERE action='product.import_fetch' AND actor_id=:a",
        a=str(u.id),
    )
    assert row == "host:gmailbuy.com"  # host only: no path or query


async def test_fetch_ka8_uses_json_api(env, fake_net):
    admin, _ = await env.login("admin")
    r = await admin.post(URL, json={"url": "https://go.ka8.ai/products/ChatGPT-Plus"})
    d = r.json()
    assert r.status_code == 200 and d["price_minor"] == 10800
    assert d["title"].startswith("ChatGPT-Plus") and d["warnings"] == []
    assert fake_net[0].url.path == "/api/v1/public/products/ChatGPT-Plus"


async def test_fetch_rejects_private_targets(env, fake_net):
    admin, _ = await env.login("admin")
    for url in (
        "http://evil.test/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/api/v1/admin/stats",
        "http://[::1]/",
        "file:///etc/passwd",
    ):
        r = await admin.post(URL, json={"url": url})
        assert r.status_code == 422, url
        assert r.json()["error"]["code"] in ("unsafe_url", "fetch_failed")
    assert fake_net == []


async def test_fetch_rate_limited_30_per_minute(env, fake_net):
    admin, _ = await env.login("admin")
    codes = [
        (await admin.post(URL, json={"url": "http://127.0.0.1/"})).status_code for _ in range(31)
    ]
    assert codes[:30] == [422] * 30 and codes[30] == 429
    other, _ = await env.login("admin")  # per admin, not global
    assert (await other.post(URL, json={"url": "http://127.0.0.1/"})).status_code == 422


async def test_parse_endpoint(env):
    admin, _ = await env.login("admin")
    P = "/api/v1/admin/products/import/parse"
    r = await admin.post(P, json={"text": "Netflix Premium\n价格: ¥ 30\nfour screens"})
    d = r.json()
    assert r.status_code == 200
    assert (d["title"], d["price_minor"], d["suggested_price_minor"]) == (
        "Netflix Premium",
        3000,
        3600,
    )
    r = await admin.post(
        P,
        json={
            "html": (FIX / "gmailbuy.html").read_text(encoding="utf-8"),
            "base_url": "https://gmailbuy.com/home/goods.html?id=8",
        },
    )
    assert r.json()["price_minor"] == 480
    assert (await admin.post(P, json={})).status_code == 422
    assert (await admin.post(P, json={"text": "  "})).status_code == 422


async def test_image_fetch_goes_through_upload_pipeline(env, fake_net):
    admin, _ = await env.login("admin")
    P = "/api/v1/admin/content/images/fetch"
    r = await admin.post(P, json={"url": "https://img.test/a.png"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"id", "url"} and body["url"].endswith(body["id"])
    assert await env.scalar("SELECT count(*) FROM attachments WHERE id=:i", i=body["id"]) == 1
    # content-type lies, magic bytes do not: the hostile-upload pipeline rejects it
    bad = await admin.post(P, json={"url": "https://img.test/fake.png"})
    assert bad.status_code in (400, 415, 422)
    priv = await admin.post(P, json={"url": "http://evil.test/a.png"})
    assert priv.status_code == 422
