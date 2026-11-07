"""review-v5.2 §10: Telegram staff notifications (HTTP mocked, secrets never leak)."""

from __future__ import annotations

import logging

import pytest

from app.core import notify_tg
from tests.helpers import buy, open_conversation, pay_order
from tests.test_v5_core import put_site, set_secret

API = "/api/v1"
TOKEN = "123456:SECRET-TOKEN-ABCDEF"


class FakeResp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class Telegram:
    def __init__(self, orig) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = 200
        self.orig = orig

    async def post(self, url, *a, **kw):
        if "api.telegram.org" not in str(url):
            return await self.orig(url, *a, **kw)
        self.calls.append((str(url), kw["json"]))
        assert kw["timeout"] == 5.0
        return FakeResp(self.status)

    @property
    def texts(self) -> list[str]:
        return [j["text"] for _, j in self.calls]


@pytest.fixture
async def tg(env, monkeypatch):
    t = Telegram(env.res.http.post)
    monkeypatch.setattr(env.res.http, "post", t.post)
    yield t
    await notify_tg.drain()


async def _enable(env, admin, **events):
    await set_secret(admin, "telegram_bot_token", TOKEN)
    return await put_site(
        env,
        admin,
        {
            "notifications": {
                "telegram_enabled": True,
                "telegram_chat_ids": ["8847311752"],
                **events,
            }
        },
    )


async def test_disabled_sends_nothing(env, tg):
    admin, _ = await env.login("admin")
    await set_secret(admin, "telegram_bot_token", TOKEN)  # token but not enabled
    p = await env.product(mode="manual")
    buyer, _u = await env.login("buyer")
    await pay_order(env, buyer, await buy(buyer, p))
    await notify_tg.drain()
    assert tg.calls == []


async def test_paid_order_sends_one_message_without_secrets(env, tg):
    admin, _ = await env.login("admin")
    out = await _enable(env, admin)
    assert out["notifications"]["telegram_chat_ids"] == ["8847311752"]
    p = await env.product(mode="manual", title="Netflix <b>1m</b>")
    buyer, bu = await env.login("buyer")
    order = await buy(buyer, p)
    await pay_order(env, buyer, order)
    await notify_tg.drain()
    assert len(tg.calls) == 1
    url, body = tg.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == "8847311752" and body["parse_mode"] == "HTML"
    assert order["order_no"] in body["text"] and "19.90 CNY" in body["text"]
    assert "Netflix &lt;b&gt;1m&lt;/b&gt;" in body["text"]  # escaped
    assert "/admin/orders" in body["text"]
    assert TOKEN not in body["text"] and bu.email not in body["text"]


async def test_event_toggle_off(env, tg):
    admin, _ = await env.login("admin")
    await _enable(env, admin, event_order_paid=False)
    p = await env.product(mode="manual")
    buyer, _u = await env.login("buyer")
    await pay_order(env, buyer, await buy(buyer, p))
    await notify_tg.drain()
    assert tg.calls == []


async def test_chat_message_throttled_and_masked(env, tg):
    admin, _ = await env.login("admin")
    await _enable(env, admin)
    buyer, _bu = await env.login("buyer", email="alice@example.com")
    conv = await open_conversation(buyer)
    for i in range(3):
        r = await buyer.post(
            f"{API}/conversations/{conv['id']}/messages",
            json={"client_msg_id": f"m{i}", "type": "text", "text": "hello secret-ish text"},
        )
        assert r.status_code == 200, r.text
    await notify_tg.drain()
    assert len(tg.calls) == 1
    text = tg.texts[0]
    assert "a***@example.com" in text and "alice@example.com" not in text
    assert "hello" not in text and "/admin/inbox" in text


async def test_after_sales_and_low_stock(env, tg):
    admin, _ = await env.login("admin")
    await _enable(env, admin)
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["CARD-SECRET-AAA", "CARD-SECRET-BBB"])
    buyer, _u = await env.login("buyer")
    order = await buy(buyer, p)
    await pay_order(env, buyer, order)
    await notify_tg.drain()
    kinds = sorted(t.split("\n")[0] for t in tg.texts)
    assert kinds == ["<b>Low card stock</b>", "<b>Order paid</b>"]
    assert all("CARD-SECRET" not in t for t in tg.texts)
    # a second paid order within the hour does not repeat the low-stock message
    order2 = await buy(buyer, p)
    await pay_order(env, buyer, order2)
    await notify_tg.drain()
    assert sum("Low card stock" in t for t in tg.texts) == 1

    assert await env.drain() >= 1
    r = await buyer.post(
        f"{API}/orders/{order['id']}/after-sales",
        json={"reason": "invalid_code", "description": "does not work at all"},
    )
    assert r.status_code == 200, r.text
    await notify_tg.drain()
    assert any("After-sales request" in t and order["order_no"] in t for t in tg.texts)


async def test_failure_never_raises_and_token_not_logged(env, tg, caplog):
    admin, _ = await env.login("admin")
    await _enable(env, admin)
    tg.status = 500
    caplog.set_level(logging.DEBUG)
    p = await env.product(mode="manual")
    buyer, _u = await env.login("buyer")
    await pay_order(env, buyer, await buy(buyer, p))  # payment still succeeds
    await notify_tg.drain()
    assert len(tg.calls) == 1
    assert "telegram send failed" in caplog.text
    assert TOKEN not in caplog.text and "api.telegram.org" not in caplog.text


async def test_token_masked_in_responses_and_validation(env, tg):
    admin, _ = await env.login("admin")
    out = await set_secret(admin, "telegram_bot_token", TOKEN)
    assert out["secrets"]["telegram_bot_token"] == {"configured": True, "last4": TOKEN[-4:]}
    site = (await admin.get(f"{API}/admin/site")).text
    assert TOKEN not in site
    for bad in (["abc"], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"], ["1.5"]):
        r = await admin.put(f"{API}/admin/site", json={"notifications": {"telegram_chat_ids": bad}})
        assert r.status_code == 422, bad
    audit = await env.scalar(
        "SELECT count(*) FROM audit_log WHERE detail::text LIKE :p", p=f"%{TOKEN}%"
    )
    assert audit == 0


async def test_test_endpoint_admin_only_and_sends(env, tg):
    admin, _ = await env.login("admin")
    agent, _a = await env.login("agent")
    assert (await agent.post(f"{API}/admin/site/telegram/test")).status_code == 403
    r = await admin.post(f"{API}/admin/site/telegram/test")
    assert r.json() == {"ok": False, "error": "no_token"}
    await set_secret(admin, "telegram_bot_token", TOKEN)
    r = await admin.post(f"{API}/admin/site/telegram/test")
    assert r.json() == {"ok": False, "error": "no_chat"}
    await put_site(env, admin, {"notifications": {"telegram_chat_ids": ["-100123"]}})
    r = await admin.post(f"{API}/admin/site/telegram/test")  # works even while disabled
    assert r.json() == {"ok": True} and tg.calls[0][1]["chat_id"] == "-100123"
    tg.status = 403
    assert (await admin.post(f"{API}/admin/site/telegram/test")).json()["ok"] is False
