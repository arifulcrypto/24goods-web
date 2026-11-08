"""Regression tests for the source-verified core findings in the 2026-11-08 audit."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text, update

from app.core import secretbox, turnstile
from app.core.errors import RateLimited, Unavailable, UploadRejected
from app.core.middleware import BodyLimitMiddleware
from app.models import User
from app.modules import jobs
from app.modules.auth import codes
from app.modules.chat.messages import insert_message
from app.modules.common.views import message_view
from app.modules.realtime import hub as realtime
from app.modules.realtime.router import _alive
from app.modules.uploads import service as uploads
from tests.helpers import notify_params
from tests.test_commerce_v4 import buy, input_product


async def test_busy_sse_rechecks_before_post_deadline_event(monkeypatch):
    hub = realtime.RealtimeHub(None)
    monkeypatch.setattr(hub, "ensure_started", lambda: None)
    sub = hub.subscribe(["busy"])
    for n in range(5):
        sub.offer({"event": "protected", "data": n})
    alive = AsyncMock(return_value=False)
    gen = realtime.sse_events(hub, sub, heartbeat_s=0.01, alive=alive)
    assert await anext(gen) == "retry: 4000\n\n"
    assert "event: protected" in await anext(gen)
    await asyncio.sleep(0.02)  # the queue stays nonempty throughout the deadline
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
    alive.assert_awaited_once()
    assert not hub._admitted and not hub._subs and not hub._counts


@pytest.mark.parametrize(
    ("kind", "cap"),
    [("account", 4), ("ip", 16), ("tenant", 64), (None, 256)],
)
async def test_subscriber_budgets_release_idempotently(monkeypatch, kind, cap):
    hub = realtime.RealtimeHub(None)
    monkeypatch.setattr(hub, "ensure_started", lambda: None)
    kwargs = {kind: "same"} if kind else {}
    subs = [hub.subscribe(["test"], **kwargs) for _ in range(cap)]
    with pytest.raises(RateLimited if kind else Unavailable):
        hub.subscribe(["test"], **kwargs)
    hub.unsubscribe(subs[0])
    hub.unsubscribe(subs[0])
    fresh = hub.subscribe(["test"], **kwargs)
    for sub in [*subs[1:], fresh]:
        hub.unsubscribe(sub)
    assert not hub._admitted and not hub._counts


async def test_image_admission_precedes_read_and_releases_after_failure(monkeypatch):
    gate = asyncio.Semaphore(1)
    monkeypatch.setattr(uploads, "_image_slot", gate)
    file = SimpleNamespace(read=AsyncMock(return_value=b"invalid"))
    async with gate:
        with pytest.raises(Unavailable):
            await uploads.image_from_upload(file)
        file.read.assert_not_awaited()
    with pytest.raises(UploadRejected):
        await uploads.image_from_upload(file)
    assert not gate.locked()


async def test_upload_budget_refuses_before_reading_multipart_and_preserves_other_routes():
    entered, release = asyncio.Event(), asyncio.Event()

    async def app(scope, receive, send):
        if scope["path"] == "/upload":
            entered.set()
            await release.wait()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = BodyLimitMiddleware(app, 100, 1000, re.compile(r"^/upload$"))
    middleware.MAX_UPLOAD_REQUESTS = 1
    messages = []
    receive = AsyncMock()

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": []}
    first = asyncio.create_task(middleware(scope, receive, send))
    await entered.wait()
    await middleware(scope, receive, send)
    assert messages[0]["status"] == 503
    receive.assert_not_awaited()
    await middleware({**scope, "path": "/callback"}, receive, send)
    assert messages[-2]["status"] == 204
    release.set()
    await first
    assert middleware._uploads == 0 and not middleware._uploads_by_ip


def _turnstile_request(
    monkeypatch,
    *,
    secret="real-secret",  # noqa: S107
    env="test",
    body=None,
):
    @asynccontextmanager
    async def session(*args):
        yield None

    monkeypatch.setattr(turnstile, "tenant_session", session)
    monkeypatch.setattr(secretbox, "get_secret", AsyncMock(return_value=secret))
    res = SimpleNamespace(
        maker=None,
        settings=SimpleNamespace(env=env, public_base_url="https://shop.example"),
        redis=SimpleNamespace(set=AsyncMock(return_value=True)),
        http=SimpleNamespace(
            post=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json=body or {"success": True, "action": "login", "hostname": "shop.example"},
                )
            )
        ),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(res=res)),
        state=SimpleNamespace(tenant_id="tenant", client_ip="198.51.100.1"),
        headers={"host": "shop.example"},
    )
    ts = {"security": {"turnstile_enabled": True, "turnstile_site_key": "site"}}
    return request, ts, res


@pytest.mark.parametrize(
    "body",
    [
        {"success": True, "action": "register", "hostname": "shop.example"},
        {"success": True, "action": "login"},
        {"success": True, "action": "login", "hostname": "attacker.example"},
        {"success": True, "hostname": "shop.example"},
        {"success": False},
        [True],
    ],
)
async def test_turnstile_rejects_unbound_or_failed_response(monkeypatch, body):
    request, ts, _ = _turnstile_request(monkeypatch, body=body)
    with pytest.raises(turnstile.TurnstileFailed):
        await turnstile.require_turnstile(request, ts, "login", "token")


@pytest.mark.parametrize("secret", [None, "1x0000000000000000000000000000000AA"])
async def test_turnstile_missing_secret_and_production_test_secret_fail_closed(monkeypatch, secret):
    request, ts, res = _turnstile_request(monkeypatch, secret=secret, env="prod")
    with pytest.raises(turnstile.TurnstileFailed):
        await turnstile.require_turnstile(request, ts, "login", "token")
    res.http.post.assert_not_awaited()


async def test_turnstile_success_replay_outage_and_explicit_disabled(monkeypatch):
    request, ts, res = _turnstile_request(monkeypatch)
    await turnstile.require_turnstile(request, ts, "login", "token")
    res.redis.set.return_value = False
    with pytest.raises(turnstile.TurnstileFailed):
        await turnstile.require_turnstile(request, ts, "login", "token")
    assert res.http.post.await_count == 1
    res.redis.set.return_value = True
    res.http.post.side_effect = httpx.ConnectTimeout("offline")
    with pytest.raises(turnstile.TurnstileFailed):
        await turnstile.require_turnstile(request, ts, "login", "new-token")
    ts["security"]["turnstile_enabled"] = False
    await turnstile.require_turnstile(request, ts, "login", None)


async def test_otp_exactly_one_correct_consumer_and_generation_replacement(env, monkeypatch):
    redis, settings, tenant = env.res.redis, env.res.settings, env.tenant_id
    email = "concurrent-code@example.com"
    code = await codes.issue(redis, settings, tenant, email, "reset")
    results = await asyncio.gather(
        *(codes.verify(redis, settings, tenant, email, "reset", code) for _ in range(12))
    )
    assert sum(results) == 1
    await redis.flushdb()
    monkeypatch.setattr(codes.secrets, "randbelow", lambda _: 123456)
    code = await codes.issue(redis, settings, tenant, email, "reset")
    original_eval = redis.eval
    replaced = False

    async def replace_after_attempt(script, *args):
        nonlocal replaced
        result = await original_eval(script, *args)
        if script == codes._ATTEMPT and result and not replaced:
            replaced = True
            await redis.flushdb()
            assert await codes.issue(redis, settings, tenant, email, "reset") == code
        return result

    monkeypatch.setattr(redis, "eval", replace_after_attempt)
    assert not await codes.verify(redis, settings, tenant, email, "reset", code)
    assert await codes.verify(redis, settings, tenant, email, "reset", code)
    assert not await codes.verify(redis, settings, env.other_tenant_id, email, "reset", code)


async def test_concurrent_password_resets_consume_code_once(env):
    user = await env.user("buyer", email="reset-race@example.com")
    code = await codes.issue(env.res.redis, env.res.settings, env.tenant_id, user.email, "reset")
    body = {"email": user.email, "code": code, "new_password": "a-brand-new-password"}
    clients = [env.client(), env.client()]
    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/password-reset", json=body) for client in clients)
    )
    assert sorted(r.status_code for r in responses) == [204, 400]
    assert (
        await env.scalar("SELECT count(*) FROM audit_log WHERE action='auth.password_reset'") == 1
    )


async def test_optional_input_clear_capability_unpublishing_and_cross_tenant(env):
    admin, _ = await env.login("admin")
    product, _ = await input_product(env, admin, required=False)
    buyer, _ = await env.login("buyer")
    order = await buy(buyer, product, buyer_input="old-value")
    assert order["can_edit_input"] and order["input_config"]["required"] is False
    unpublished = await admin.patch(
        f"/api/v1/admin/products/{product.id}", json={"status": "draft"}
    )
    assert unpublished.status_code == 200
    assert (await buyer.get(f"/api/v1/products/{product.id}")).status_code == 404
    response = await buyer.patch(
        f"/api/v1/orders/{order['id']}/input", json={"buyer_input": "first\r\nsecond"}
    )
    assert response.status_code == 200 and response.json()["buyer_input"] == "first\nsecond"
    response = await buyer.patch(
        f"/api/v1/orders/{order['id']}/input", json={"buyer_input": "x" * 51}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "buyer_input_too_long"
    response = await buyer.patch(
        f"/api/v1/orders/{order['id']}/input", json={"buyer_input": "\u200b\ufeff"}
    )
    assert response.status_code == 200 and response.json()["buyer_input"] is None
    assert response.json()["can_edit_input"]
    response = await buyer.patch(f"/api/v1/orders/{order['id']}/input", json={"buyer_input": None})
    assert response.status_code == 200 and response.json()["buyer_input"] is None
    foreign, _ = await env.login("buyer", tenant_id=env.other_tenant_id)
    assert (await foreign.get(f"/api/v1/orders/{order['id']}")).status_code in (401, 404)
    assert (
        await foreign.patch(f"/api/v1/orders/{order['id']}/input", json={"buyer_input": "x"})
    ).status_code in (401, 404)
    await admin.patch(f"/api/v1/admin/products/{product.id}", json={"buyer_input": None})
    view = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert view["input_config"] is None and view["can_edit_input"] is False
    result = await buyer.patch(f"/api/v1/orders/{order['id']}/input", json={"buyer_input": "x"})
    assert result.status_code == 422 and result.json()["error"]["code"] == "buyer_input_disabled"


async def test_legacy_input_copies_immediately_hidden_then_scrubbed_idempotently(env, gw):
    admin, _ = await env.login("admin")
    product, _ = await input_product(env, admin)
    buyer, user = await env.login("buyer")
    conv = (await buyer.post("/api/v1/conversations", json={})).json()
    marker = "synthetic-private-retention-marker"
    order = await buy(buyer, product, buyer_input=marker)
    gw.paid[order["order_no"]] = ("10.00", "TRETENTION")
    result = await env.client(csrf=False).get(
        "/api/v1/pay/notify/zpay",
        params=notify_params(order["order_no"], "10.00", trade_no="TRETENTION"),
    )
    assert result.text == "success"
    result = await buyer.patch(
        f"/api/v1/orders/{order['id']}/input", json={"buyer_input": marker + "-edited"}
    )
    assert result.status_code == 200
    async with env.api_session() as db:
        message, _ = await insert_message(
            db,
            tenant_id=env.tenant_id,
            conversation_id=uuid.UUID(conv["id"]),
            sender_id=user.id,
            sender_role="buyer",
            type="text",
            text=marker,
            client_msg_id="order-input:legacy-synthetic-order:2",
        )
        assert marker not in json.dumps(message_view(message, sender_name="buyer"))
        await db.execute(
            text(
                "UPDATE orders SET status='completed', completed_at=now()-interval '40 days' "
                "WHERE id=:id AND tenant_id=:tid"
            ),
            {"id": uuid.UUID(order["id"]), "tid": env.tenant_id},
        )
        await db.commit()
    response = await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")
    assert marker not in response.text
    assert await jobs.purge_buyer_input(env.res.maker) == 1
    assert await jobs.purge_buyer_input(env.res.maker) == 0
    order_view = (await buyer.get(f"/api/v1/orders/{order['id']}")).json()
    assert order_view["buyer_input"] is None
    async with env.api_session() as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM messages WHERE text=:marker"), {"marker": marker}
            )
            == 0
        )


@pytest.mark.parametrize("change", ["logout", "role", "suspended", "status"])
async def test_sse_liveness_rechecks_current_account_permissions(env, change):
    from datetime import datetime, timedelta, timezone

    client, user = await env.login("admin")
    request = SimpleNamespace(cookies=client.cookies)
    check = _alive(env.res, request, env.tenant_id, user)
    assert await check()
    if change == "logout":
        await env.res.sessions.revoke_user_sessions(user.id)
    else:
        now = datetime.now(timezone.utc)
        patches = {
            "role": {"role": "buyer"},
            "suspended": {"suspended_at": now, "suspended_until": now + timedelta(days=1)},
            "status": {"status": "deleted"},
        }
        async with env.api_session() as db:
            await db.execute(
                update(User)
                .where(User.id == user.id, User.tenant_id == env.tenant_id)
                .values(**patches[change])
            )
            await db.commit()
    assert not await check()
