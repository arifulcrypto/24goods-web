"""Regression tests for the security-review findings (M1, M3-M6, L1, L5, L8, L9, search)."""

from __future__ import annotations

import dataclasses
import time

import pytest
from sqlalchemy import text

from app.core.money import MoneyError, parse_amount
from app.modules.uploads.processing import sniff_voice
from tests.helpers import notify_params, open_conversation, simulate_gateway_payment
from tests.test_chat import png_bytes
from tests.test_uploads import OGG_OPUS, WEBM

NOTIFY = "/api/v1/pay/notify/zpay"


async def _delivered(env, buyer, product, qty: int) -> dict:
    o = (
        await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": qty})
    ).json()
    url = (
        await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    ).json()
    await simulate_gateway_payment(env, url["pay_url"])
    await env.drain()
    return o


async def _stock(env, n: int):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, [f"CODE-{i:04d}-{time.monotonic_ns()}" for i in range(n)])
    return p


def _tune_delivery(env, **kw) -> None:
    env.delivery_ctx.settings = dataclasses.replace(env.delivery_ctx.settings, **kw)


# ---------------------------------------------------------------- M1 reveal DoS
async def test_refused_reveals_never_inflate_the_global_budget(env):
    _tune_delivery(env, reveal_max_per_min=5)
    p = await _stock(env, 10)
    a, _ = await env.login("buyer")
    b, _ = await env.login("buyer")
    oa = await _delivered(env, a, p, 3)
    ob = await _delivered(env, b, p, 2)
    assert (await a.post(f"/api/v1/orders/{oa['id']}/reveal")).status_code == 200  # 3/5
    for _ in range(5):  # 3 + 3 > 5: refused, and the refusals must not count
        r = await a.post(f"/api/v1/orders/{oa['id']}/reveal")
        assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
    r = await b.post(f"/api/v1/orders/{ob['id']}/reveal")  # 3 + 2 = 5: still fits
    assert r.status_code == 200 and len(r.json()["secrets"]) == 2


async def test_per_buyer_reveal_budget_in_delivery(env):
    _tune_delivery(env, reveal_per_buyer_cards=4)
    p = await _stock(env, 10)
    a, _ = await env.login("buyer")
    b, _ = await env.login("buyer")
    oa = await _delivered(env, a, p, 3)
    ob = await _delivered(env, b, p, 3)
    assert (await a.post(f"/api/v1/orders/{oa['id']}/reveal")).status_code == 200
    r = await a.post(f"/api/v1/orders/{oa['id']}/reveal")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    # buyer A's refusal consumed nothing global: buyer B is unaffected
    assert (await b.post(f"/api/v1/orders/{ob['id']}/reveal")).status_code == 200


async def test_per_buyer_reveal_budget_in_api(env):
    env.res.settings.reveal_per_buyer_cards = 4
    p = await _stock(env, 6)
    a, _ = await env.login("buyer")
    oa = await _delivered(env, a, p, 3)
    r = await a.post(f"/api/v1/orders/{oa['id']}/reveal")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"  # L9
    r = await a.post(f"/api/v1/orders/{oa['id']}/reveal")
    assert r.status_code == 429 and "retry-after" in r.headers
    # refused at the API: the delivery service never decrypted a second time
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='order.reveal'") == 1


async def test_auto_card_quote_qty_capped_at_10(env):
    auto = await env.product(mode="auto_card", price=100, floor=0)
    manual = await env.product(mode="manual", price=100, floor=0)
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    url = f"/api/v1/admin/conversations/{conv['id']}/quotes"
    body = {"title": "bulk", "amount": "11.00", "qty": 11, "expires_in_minutes": 30}
    r = await agent.post(url, json={**body, "product_id": str(auto.id)})
    assert r.status_code == 422
    assert (
        await agent.post(url, json={**body, "product_id": str(auto.id), "qty": 10})
    ).status_code == 200
    assert (await agent.post(url, json={**body, "product_id": str(manual.id)})).status_code == 200


# ---------------------------------------------------------------- M4 notify flood
async def test_notify_per_ip_rate_limit(env):
    env.res.settings.notify_rate_per_min = 3
    c = env.client(csrf=False)
    forged = {**notify_params("GAAAAAAAAAAAA", "1.00"), "sign": "x"}
    replies = [(await c.get(NOTIFY, params=forged)).text for _ in range(3)]
    assert replies == ["fail"] * 3
    r = await c.get(NOTIFY, params={"pid": "1"})
    assert r.status_code == 200 and r.text == "fail"
    # the 4th request was refused before verification: no extra audit/counter work
    day = time.strftime("%Y%m%d", time.gmtime())
    assert int(await env.s.redis.get(f"sec:count:payment.notify_bad_signature:{day}")) == 3


async def test_bad_signature_audits_are_sampled_per_ip(env):
    product = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    o = (await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    forged = notify_params(o["order_no"], "19.90")
    forged["sign"] = "0" * 32
    c = env.client(csrf=False)
    for _ in range(6):
        assert (await c.get(NOTIFY, params=forged)).text == "fail"
    await c.get(NOTIFY, params=forged, headers={"X-Forwarded-For": "203.0.113.50"})
    rows = await env.scalar(
        "SELECT count(*) FROM audit_log WHERE action='payment.notify_bad_signature'"
    )
    assert rows == 2  # one per (IP, minute)
    day = time.strftime("%Y%m%d", time.gmtime())
    assert int(await env.s.redis.get(f"sec:count:payment.notify_bad_signature:{day}")) == 7


# ---------------------------------------------------------------- M5 order abuse
async def test_pending_order_cap(env):
    product = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    codes = [
        (
            await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
        ).status_code
        for _ in range(6)
    ]
    assert codes == [200] * 5 + [409]
    r = await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.json()["error"]["code"] == "conflict"
    other, _ = await env.login("buyer")  # per buyer
    r = await other.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})
    assert r.status_code == 200


async def test_pay_and_create_rate_limits(env):
    product = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    o = (await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    codes = [
        (
            await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
        ).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    for _ in range(19):  # 1 create already used; quote-accept shares this bucket
        await buyer.post(
            "/api/v1/orders", json={"product_id": "00000000-0000-0000-0000-000000000000", "qty": 1}
        )
    r = await buyer.post("/api/v1/quotes/00000000-0000-0000-0000-000000000000/accept")
    assert r.status_code == 429


# ---------------------------------------------------------------- M6 voice
async def test_voice_served_as_attachment_and_image_inline(env):
    buyer, _ = await env.login("buyer")
    v = (
        await buyer.post(
            "/api/v1/uploads",
            data={"kind": "voice", "duration_ms": "1000"},
            files={"file": ("v.ogg", OGG_OPUS + b"\x00" * 64, "audio/ogg")},
        )
    ).json()
    r = await buyer.get(v["url"])
    assert r.headers["content-disposition"] == "attachment"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    i = (
        await buyer.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("i.png", png_bytes(), "image/png")},
        )
    ).json()
    assert (await buyer.get(i["url"])).headers["content-disposition"] == "inline"


@pytest.mark.parametrize(
    "head",
    [
        b"OggS" + b"\x00" * 60,  # no BOS page / codec header
        b"\x1a\x45\xdf\xa3\x9f\x42\x82\x88matroska" + b"\x00" * 30,  # EBML but not webm
        b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ",  # QuickTime video brand
        b"\x00\x00\x00\x08ftyp",  # truncated ftyp box
        b"\xff\xf9\x90\x64" + b"\x00" * 10,  # MPEG frame sync with reserved layer (ADTS-like)
        b"\xff\xfb\xf0\x64" + b"\x00" * 10,  # bad bitrate index
        b"ID3\x09" + b"\x00" * 10,  # bogus ID3 version
    ],
)
def test_voice_magic_rejects_lookalikes(head):
    assert sniff_voice(head) is None


def test_voice_magic_accepts_real_headers():
    assert sniff_voice(OGG_OPUS) == "audio/ogg"
    assert sniff_voice(WEBM) == "audio/webm"
    assert sniff_voice(b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso6mp41") == "audio/mp4"
    assert sniff_voice(b"ID3\x04\x00\x00\x00\x00\x00\x00") == "audio/mpeg"


# ---------------------------------------------------------------- L1 cookie prefix
async def test_secure_cookie_uses_host_prefix(env):
    env.res.settings.cookie_secure = True
    c = env.client()
    r = await c.post(
        "/api/v1/auth/register",
        json={
            "email": "sec@example.com",
            "password": "long-enough-pw",
            "display_name": "S",
            "locale": "en",
        },
    )
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-sid=") and "Secure" in cookie and "Path=/" in cookie
    assert "Domain" not in cookie
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    raw = env.client()
    assert (
        await raw.get("/api/v1/auth/me", headers={"Cookie": f"__Host-sid={token}"})
    ).status_code == 200
    assert (await raw.get("/api/v1/auth/me", headers={"Cookie": f"sid={token}"})).status_code == 401


# ---------------------------------------------------------------- L5 money parsing
@pytest.mark.parametrize(
    "raw", ["1e999999999", "1E5", "9" * 11, "99999999999.99", "1.5e-3", "+1", "1_000", "١٢"]
)
def test_money_rejects_exponents_and_huge(raw):
    with pytest.raises(MoneyError):
        parse_amount(raw)


async def test_huge_amount_is_a_validation_error_not_500(env):
    product = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    o = (await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    for amount in ("1e999999999", "9999999999999"):
        r = await agent.patch(
            f"/api/v1/admin/orders/{o['id']}/price", json={"amount": amount, "reason": "x"}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation"


# ---------------------------------------------------------------- L8 delivery attachments
async def test_manual_delivery_attachment_must_be_own_upload(env):
    product = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    victim, _ = await env.login("buyer")
    agent, _ = await env.login("admin")  # even admins may not attach someone else's file
    private = (
        await victim.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("p.png", png_bytes(), "image/png")},
        )
    ).json()
    o = (await buyer.post("/api/v1/orders", json={"product_id": str(product.id), "qty": 1})).json()
    pay = (
        await buyer.post(f"/api/v1/orders/{o['id']}/pay", json={"channel": "zpay_alipay"})
    ).json()
    await simulate_gateway_payment(env, pay["pay_url"])
    url = f"/api/v1/admin/orders/{o['id']}/deliver"
    r = await agent.post(url, json={"text": "here", "attachment_id": private["id"]})
    assert r.status_code == 404
    assert (await buyer.get(private["url"])).status_code == 404
    own = (
        await agent.post(
            "/api/v1/uploads",
            data={"kind": "image"},
            files={"file": ("d.png", png_bytes(), "image/png")},
        )
    ).json()
    r = await agent.post(url, json={"text": "here", "attachment_id": own["id"]})
    assert r.status_code == 200, r.text
    assert (await buyer.get(own["url"])).status_code == 200


# ---------------------------------------------------------------- L9 + search
async def test_api_responses_default_to_no_store(env):
    r = await env.client().get("/api/v1/categories")
    assert r.headers["cache-control"] == "no-store"


async def test_fuzzy_search_uses_trigram_operator_and_index(env):
    await env.product(title="Steam Wallet Code")
    items = (await env.client().get("/api/v1/products", params={"q": "walet"})).json()["items"]
    assert [i["title"] for i in items] == ["Steam Wallet Code"]  # typo: only %> can match
    async with env.owner_session() as db:
        # `%>` must belong to gin_trgm_ops, i.e. be answerable from ix_products_search_trgm
        indexable = await db.scalar(
            text(
                "SELECT count(*) FROM pg_amop a JOIN pg_opfamily f ON f.oid = a.amopfamily "
                "WHERE f.opfname = 'gin_trgm_ops' AND a.amopopr = '%>(text,text)'::regoperator"
            )
        )
        idx = await db.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_products_search_trgm'")
        )
    assert indexable == 1 and "gin_trgm_ops" in idx and "search_text" in idx
    r = await env.client().get("/api/v1/products", params={"page": 1001})
    assert r.status_code == 422
