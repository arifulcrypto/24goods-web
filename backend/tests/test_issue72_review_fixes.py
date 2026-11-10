# ruff: noqa: F811
"""Regressions for the cold reviews of the #72 branch (payment-auditor + security-reviewer)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from app.modules.orders.service import expire_pending
from app.payments import bepusdt
from tests.helpers import open_conversation, send_quote
from tests.test_issue72_payments import _abandon, _close
from tests.test_usdt_v52 import (
    _paid_order,
    mock,  # noqa: F401
)

API = "/api/v1"


async def test_close_held_requeries_closed_attempts_too(env, mock):
    """Audit M1: a closed attempt is skipped by the background poll, never by the safety check."""
    _buyer, order, _page, trade_id = await _paid_order(env, mock)
    await _abandon(env, order)
    assert await _close(env) == 1
    await mock.patch_trade(trade_id, status=2)  # the old address was paid; the notify was lost
    async with env.owner_session() as db:  # held without a recorded payment (e.g. bad notify)
        await db.execute(
            text("UPDATE orders SET status='manual_review' WHERE id=:i"), {"i": order["id"]}
        )
        await db.commit()
    admin, _ = await env.login("admin")
    r = await admin.post(
        f"{API}/admin/orders/{order['id']}/release-hold",
        json={"outcome": "close", "reason": "gateway console shows nothing"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_paid", r.text
    assert await env.scalar("SELECT count(*) FROM payments") == 1
    assert await env.scalar("SELECT status FROM orders") == "manual_review"


@pytest.mark.parametrize(
    ("body", "exc"),
    [
        ({"status_code": 400, "message": "bad"}, bepusdt.BepusdtRefused),
        ({"status_code": 200, "data": None}, bepusdt.BepusdtError),
        ({"status_code": "200", "data": {}}, bepusdt.BepusdtError),
        ({"message": "no status"}, bepusdt.BepusdtError),
    ],
)
async def test_only_a_well_formed_refusal_is_a_refusal(body, exc):
    """Audit M2: a malformed success may hide a created transaction -> unknown, not refused."""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=json.dumps(body)))
    )
    client = bepusdt.BepusdtClient(
        bepusdt.BepusdtConfig("https://usdt.test", "tok", "https://n.test", 5), http
    )
    with pytest.raises(exc) as info:
        await client._post("/api/v1/order/create-transaction", "{}")
    if exc is bepusdt.BepusdtError:
        assert not isinstance(info.value, bepusdt.BepusdtRefused)
    await http.aclose()


async def test_quote_only_expiry_pass_does_not_crash(env):
    """Security M1: the quote re-broadcast used a leaked loop variable as its tenant."""
    p = await env.product(mode="manual")
    b, _ = await env.login("buyer")
    agent, _ = await env.login("agent")
    conv = await open_conversation(b, p.id)
    q = await send_quote(agent, conv["id"], amount="19.90", product_id=str(p.id))
    async with env.owner_session() as db:
        await db.execute(text("UPDATE quotes SET expires_at = now() - interval '1 second'"))
        await db.commit()
    await expire_pending(env.res.maker, env.res)  # used to raise UnboundLocalError here
    assert await env.scalar("SELECT status FROM quotes WHERE id=:q", q=q["quote_id"]) == "expired"
