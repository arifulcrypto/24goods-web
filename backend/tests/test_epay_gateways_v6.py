"""Xiaodou/EPay contract, tenant isolation and original-provider recovery regressions."""

from __future__ import annotations

import secrets
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core import safefetch
from app.core.errors import Validation
from app.models import PaymentAttempt, PaymentGateway
from app.modules.payments.gateways import normalize_gateway
from app.payments import epay
from app.payments.zpay import QueryResult, ZPayClient, ZPayConfig, ZPayQueryError
from tests.conftest import PASSWORD
from tests.helpers import buy


@pytest.mark.parametrize("endpoint", ["", "/", "/submit.php", "/mapi.php", "/api.php"])
def test_normalizes_documented_gateway_forms(endpoint):
    assert normalize_gateway("https://gateway.example" + endpoint) == "https://gateway.example"


@pytest.mark.parametrize(
    "value",
    [
        "http://pay.example",
        "https://a:b@pay.example",
        "https://pay.example?key=example",
        "https://pay.example/#x",
        "https://pay.example/a/../b",
    ],
)
def test_unsafe_gateway_syntax_refused(value):
    with pytest.raises(Validation):
        normalize_gateway(value)


async def test_form_create_has_exact_signed_amount_and_no_json():
    key = secrets.token_hex(16)

    def respond(request):
        from urllib.parse import parse_qsl

        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        fields = dict(parse_qsl(request.content.decode()))
        assert fields["money"] == "10.01" and epay.verify_signature(fields, key)
        return httpx.Response(200, json={"code": 1, "qrcode": "https://qr.alipay.com/example"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = ZPayClient(
            ZPayConfig(
                "fixture",
                key,
                "https://pay.example",
                "https://pay.example",
                "https://shop.example/notify",
                "https://shop.example/return",
            ),
            http,
        )
        view = await client.create_payment("GEXAMPLE000001", "item", Decimal("10.01"), "1.1.1.1")
        assert view["kind"] == "qr" and view["payload"].startswith("https://qr.alipay.com/")


async def test_hostile_qr_or_html_is_refused():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 1, "qrcode": "javascript:alert(1)"})
        )
    ) as http:
        c = ZPayClient(
            ZPayConfig(
                "fixture",
                "fixture-only",
                "https://pay.example",
                "https://pay.example",
                "https://shop.example/n",
                "https://shop.example/r",
            ),
            http,
        )
        with pytest.raises(ZPayQueryError):
            await c.create_payment("GEXAMPLE000001", "item", Decimal("1.00"), "1.1.1.1")


async def _gateway(env, admin, monkeypatch, pid="fixture1"):
    async def vetted(url):
        return safefetch.Target(url, "https", "gateway.example", 443, "/", "1.1.1.1")

    monkeypatch.setattr(safefetch, "vet_url", vetted)
    key = secrets.token_hex(16)
    r = await admin.post(
        "/api/v1/admin/payment-gateways",
        json={
            "label": "Contract fixture",
            "gateway": "https://gateway.example/submit.php",
            "merchant_id": pid,
            "secret": key,
            "current_password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    ref = r.json()["id"]
    assert key not in r.text
    r = await admin.post(
        f"/api/v1/admin/payment-gateways/{ref}/activate",
        json={"current_password": PASSWORD, "ownership_confirmed": True},
    )
    assert r.status_code == 200, r.text
    return ref, key


async def test_original_provider_survives_rotation_and_duplicate_notify(env, monkeypatch):
    admin, _ = await env.login("admin")
    buyer, _ = await env.login()
    ref, key = await _gateway(env, admin, monkeypatch)

    async def create(self, *args):
        return {"kind": "qr", "payload": "https://qr.alipay.com/example"}

    monkeypatch.setattr(ZPayClient, "create_payment", create)
    product = await env.product(mode="manual")
    order = await buy(buyer, product)
    r = await buyer.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200 and r.json()["pay_url"].startswith("/pay/epay/"), r.text
    async with env.api_session() as db:
        att = await db.scalar(
            select(PaymentAttempt).where(PaymentAttempt.tenant_id == env.tenant_id)
        )
        remote = att.gateway_ref
    await _gateway(env, admin, monkeypatch, "fixture2")

    async def query(self, no):
        assert self.config.pid == "fixture1" and no == remote
        return QueryResult(True, True, Decimal(order["amount"]), "same-provider-trade", remote)

    monkeypatch.setattr(ZPayClient, "query_order", query)
    params = {
        "pid": "fixture1",
        "out_trade_no": remote,
        "trade_no": "same-provider-trade",
        "money": order["amount"],
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = epay.sign(params, key)
    for _ in range(2):
        assert (
            await env.client(csrf=False).get("/api/v1/pay/notify/zpay", params=params)
        ).text == "success"
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT count(*) FROM outbox WHERE topic = 'order.paid'") == 1
    async with env.api_session(env.other_tenant_id) as db:
        assert (
            await db.scalar(
                select(PaymentGateway).where(
                    PaymentGateway.id == __import__("uuid").UUID(ref),
                    PaymentGateway.tenant_id == env.other_tenant_id,
                )
            )
            is None
        )
    other, _ = await env.login()
    assert (await other.get(f"/api/v1/pay/epay/{order['order_no']}")).status_code == 404
    view = await buyer.get(f"/api/v1/pay/epay/{order['order_no']}")
    assert view.json()["status"] == "paid" and view.json()["qr_svg"] is None


async def test_gateway_creation_requires_fresh_auth_and_never_returns_secret(env, monkeypatch):
    admin, _ = await env.login("admin")
    key = secrets.token_hex(16)
    r = await admin.post(
        "/api/v1/admin/payment-gateways",
        json={
            "label": "test",
            "gateway": "https://gateway.example",
            "merchant_id": "fixture",
            "secret": key,
            "current_password": "incorrect",
        },
    )
    assert r.status_code == 403 and key not in r.text
    buyer, _ = await env.login()
    assert (await buyer.get("/api/v1/admin/payment-gateways")).status_code == 403


async def test_legacy_environment_key_cannot_bypass_versioned_attempt_or_cutover(env, monkeypatch):
    from app.core.errors import Unavailable
    from app.modules.payments.gateways import legacy_allowed, routed_resources

    admin, _ = await env.login("admin")
    await _gateway(env, admin, monkeypatch)
    buyer, _ = await env.login()
    order = await buy(buyer, await env.product(mode="manual"))
    async with env.api_session() as db:
        assert not await legacy_allowed(db, env.tenant_id, order["order_no"])
        with pytest.raises(Unavailable):
            await routed_resources(db, env.res, env.tenant_id, order["order_no"])
