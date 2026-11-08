"""Optional entry/payment challenge gates: direct API bypass, binding and retries."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import httpx
from starlette.requests import Request

from app.core import pow as powmod
from app.core.errors import Unavailable
from app.modules.challenges import service as gate
from app.modules.orders import service as orders
from app.modules.seo import template
from tests.helpers import buy
from tests.test_v5_core import put_site, set_secret

API = "/api/v1"


async def enable(env, *, payment=True, visit=False):
    admin, _ = await env.login("admin")
    await put_site(
        env,
        admin,
        {
            "security": {
                "payment_challenge_enabled": payment,
                "site_visit_enabled": visit,
                "challenge_preset": "low",
            }
        },
    )
    return admin


async def order(env):
    buyer, user = await env.login("buyer")
    item = await buy(buyer, await env.product(mode="manual", price=1000, floor=100))
    return buyer, user, item


async def challenge(client, item, *, target="order", channel="zpay_alipay"):
    response = await client.post(
        f"{API}/security/challenges",
        json={"action": "payment_initiation", "target": target, "ref": item, "channel": channel},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "channel": channel,
        "challenge_id": data["id"],
        "pow": {
            "challenge": data["pow"]["challenge"],
            "nonce": powmod.solve(data["pow"]["challenge"], data["pow"]["bits"]),
        },
        "idempotency_key": str(uuid.uuid4()),
    }


async def test_disabled_compatibility_and_public_policy(env):
    buyer, _, item = await order(env)
    policy = (await buyer.get(f"{API}/site")).json()["security_challenges"]
    assert policy == {"payment_initiation": False, "site_visit": False, "preset": "standard"}
    response = await buyer.post(f"{API}/orders/{item['id']}/pay", json={"channel": "zpay_alipay"})
    assert response.status_code == 200


async def test_payment_gate_no_bypass_and_same_key_returns_original(env, monkeypatch):
    await enable(env)
    buyer, _, item = await order(env)
    original = orders.pay_url
    spy = AsyncMock(side_effect=original)
    monkeypatch.setattr(orders, "pay_url", spy)
    missing = await buyer.post(f"{API}/orders/{item['id']}/pay", json={"channel": "zpay_alipay"})
    assert missing.status_code == 428 and spy.await_count == 0
    body = await challenge(buyer, item["id"])
    first = await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)
    assert first.status_code == 200, first.text
    repeated = await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)
    assert repeated.status_code == 200 and repeated.json() == first.json()
    assert spy.await_count == 1
    # An order number is also a supported owner reference.
    another = await buy(buyer, await env.product(mode="manual", price=1000, floor=100))
    body = await challenge(buyer, another["order_no"])
    assert (
        await buyer.post(f"{API}/orders/{another['order_no']}/pay", json=body)
    ).status_code == 200


async def test_payment_proof_session_owner_method_and_version_binding(env, monkeypatch):
    admin = await enable(env)
    await put_site(env, admin, {"payments": {"channels": {"usdt_trc20": {"enabled": True}}}})
    buyer, user, item = await order(env)
    body = await challenge(buyer, item["id"])
    spy = AsyncMock(return_value="/should-not-be-created")
    monkeypatch.setattr(orders, "pay_url", spy)
    other, _ = await env.login("buyer")
    assert (
        await other.post(
            f"{API}/security/challenges",
            json={
                "action": "payment_initiation",
                "target": "order",
                "ref": item["id"],
                "channel": "zpay_alipay",
            },
        )
    ).status_code == 404
    second = env.client()
    token = await env.res.sessions.create(user.id, user.tenant_id, user.role)
    second.cookies.set(env.res.sessions.cookie_name, token)
    assert (await second.post(f"{API}/orders/{item['id']}/pay", json=body)).status_code == 422
    assert (
        await buyer.post(f"{API}/orders/{item['id']}/pay", json={**body, "channel": "usdt_trc20"})
    ).status_code == 422
    repriced = await admin.patch(
        f"{API}/admin/orders/{item['id']}/price",
        json={"amount": "11.00", "reason": "challenge_binding_regression"},
    )
    assert repriced.status_code == 200
    assert (await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)).status_code == 422
    assert spy.await_count == 0


async def test_concurrent_proofs_reserve_one_initiation_and_uncertain_never_retries(
    env, monkeypatch
):
    await enable(env)
    buyer, _, item = await order(env)
    body = await challenge(buyer, item["id"])
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    original = orders.pay_url

    async def blocked(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(orders, "pay_url", blocked)
    first = asyncio.create_task(buyer.post(f"{API}/orders/{item['id']}/pay", json=body))
    await started.wait()
    duplicate = await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)
    assert duplicate.status_code == 409
    release.set()
    assert (await first).status_code == 200 and calls == 1
    # An uncertain provider call is never reissued, even with a new solved proof/key.
    another = await buy(buyer, await env.product(mode="manual", price=1000, floor=100))
    body = await challenge(buyer, another["id"])
    failing = AsyncMock(side_effect=Unavailable("upstream outcome unknown"))
    monkeypatch.setattr(orders, "pay_url", failing)
    assert (await buyer.post(f"{API}/orders/{another['id']}/pay", json=body)).status_code == 503
    assert (await buyer.post(f"{API}/orders/{another['id']}/pay", json=body)).status_code == 409
    fresh = await challenge(buyer, another["id"])
    assert (await buyer.post(f"{API}/orders/{another['id']}/pay", json=fresh)).status_code == 409
    assert failing.await_count == 1


async def test_checkout_gate_bound_to_members_and_once_only(env):
    await enable(env)
    buyer, _ = await env.login("buyer")
    product = await env.product(mode="manual", price=1000, floor=100)
    response = await buyer.post(
        f"{API}/checkout", json={"items": [{"product_id": str(product.id), "qty": 1}]}
    )
    assert response.status_code == 200
    no = response.json()["checkout"]["checkout_no"]
    assert (
        await buyer.post(f"{API}/checkouts/{no}/pay", json={"channel": "zpay_alipay"})
    ).status_code == 428
    body = await challenge(buyer, no, target="checkout")
    response = await buyer.post(f"{API}/checkouts/{no}/pay", json=body)
    assert response.status_code == 200, response.text
    assert (await buyer.post(f"{API}/checkouts/{no}/pay", json=body)).json() == response.json()


async def test_site_clearance_gates_data_and_seo_but_preserves_recovery(env, monkeypatch):
    admin = await enable(env, payment=False, visit=True)
    buyer, _, item = await order(env)
    public = env.client()
    assert (await public.get(f"{API}/security/site-clearance")).json() == {
        "required": True,
        "cleared": False,
    }
    for path in ("/products", "/products/recommended", "/categories", "/articles"):
        response = await public.get(API + path)
        assert (
            response.status_code == 428
            and response.json()["error"]["code"] == "site_visit_required"
        )
    monkeypatch.setattr(
        template,
        "load_template",
        AsyncMock(return_value='<html><head></head><body><div id="app"></div></body></html>'),
    )
    response = await public.get("/")
    assert response.status_code == 428 and "noindex" in response.headers["x-robots-tag"]
    assert item["title"] not in response.text
    assert (await buyer.get(f"{API}/orders/{item['id']}")).status_code == 200
    assert (await admin.get(f"{API}/admin/site")).status_code == 200
    data = (await public.post(f"{API}/security/challenges", json={"action": "site_visit"})).json()
    body = {
        "challenge_id": data["id"],
        "pow": {
            "challenge": data["pow"]["challenge"],
            "nonce": powmod.solve(data["pow"]["challenge"], data["pow"]["bits"]),
        },
    }
    response = await public.post(f"{API}/security/site-clearance", json=body)
    assert response.status_code == 204, response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert (await public.get(f"{API}/security/site-clearance")).json()["cleared"]
    catalog = await public.get(f"{API}/products")
    assert catalog.status_code == 200
    assert "no-store" in catalog.headers["cache-control"]
    assert "Cookie" in catalog.headers["vary"]
    assert (await public.post(f"{API}/security/site-clearance", json=body)).status_code == 422
    # Clearances cannot be replayed on another host or tenant.
    fake = Request(
        {
            "type": "http",
            "path": "/",
            "scheme": "http",
            "server": ("attacker.example", 80),
            "headers": [
                (b"host", b"attacker.example"),
                (b"cookie", "; ".join(f"{k}={v}" for k, v in public.cookies.items()).encode()),
            ],
        }
    )
    async with env.api_session() as db:
        ts = await gate.tenant_settings(db, env.tenant_id)
    assert not gate.clearance(fake, env.res, env.tenant_id, ts)
    same_host = Request(
        {
            **fake.scope,
            "server": ("testserver", 80),
            "headers": [
                (b"host", b"testserver"),
                (b"cookie", "; ".join(f"{k}={v}" for k, v in public.cookies.items()).encode()),
            ],
        }
    )
    assert not gate.clearance(same_host, env.res, env.other_tenant_id, ts)


async def test_expired_and_policy_changed_proofs_and_turnstile_action(env, monkeypatch):
    admin = await enable(env)
    buyer, _, item = await order(env)
    body = await challenge(buyer, item["id"])
    await env.res.redis.delete("pow:gate:" + body["challenge_id"])
    assert (await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)).status_code == 422
    body = await challenge(buyer, item["id"])
    await put_site(env, admin, {"security": {"challenge_preset": "high"}})
    assert (await buyer.post(f"{API}/orders/{item['id']}/pay", json=body)).status_code == 422
    await set_secret(admin, "turnstile_secret", "ts-real-secret")
    await put_site(
        env,
        admin,
        {
            "security": {
                "challenge_preset": "low",
                "turnstile_enabled": True,
                "turnstile_site_key": "site",
                "turnstile_actions": ["payment_initiation"],
            }
        },
    )
    body = await challenge(buyer, item["id"])

    async def wrong_action(*args, **kwargs):
        return httpx.Response(
            200, json={"success": True, "hostname": "testserver", "action": "site_visit"}
        )

    monkeypatch.setattr(env.res.http, "post", wrong_action)
    response = await buyer.post(
        f"{API}/orders/{item['id']}/pay", json={**body, "turnstile_token": "wrong-action-token"}
    )
    assert response.status_code == 400 and response.json()["error"]["code"] == "turnstile_failed"
