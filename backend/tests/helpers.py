from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

import httpx

from app.payments import epay
from tests.conftest import ZPAY_KEY, ZPAY_PID


def notify_params(order_no: str, money: str, trade_no: str = "T202611050001", **over) -> dict:
    p = {
        "pid": ZPAY_PID,
        "trade_no": trade_no,
        "out_trade_no": order_no,
        "type": "alipay",
        "name": "item",
        "money": money,
        "trade_status": "TRADE_SUCCESS",
    }
    p.update(over)
    p["sign"] = epay.sign(p, ZPAY_KEY)
    p["sign_type"] = "MD5"
    return p


async def simulate_gateway_payment(env, pay_url: str) -> httpx.Response:
    """Drive the mock gateway exactly like a browser: submit.php -> 'Simulate payment'."""
    u = urlsplit(pay_url)
    mock = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.mock_app), base_url="http://mock"
    )
    try:
        page = await mock.get("/mockpay/submit.php", params=dict(parse_qsl(u.query)))
        assert page.status_code == 200, page.text
        assert "ZPay Simulator — Alipay" in page.text
        nonce = re.search(r'name=nonce value="([^"]+)"', page.text).group(1)
        order_no = dict(parse_qsl(u.query))["out_trade_no"]
        return await mock.post(
            "/mockpay/pay", data={"out_trade_no": order_no, "nonce": nonce, "action": "pay"}
        )
    finally:
        await mock.aclose()


async def solve_pow(client: httpx.AsyncClient, scope: str) -> dict:
    """Fetch a challenge for ``scope`` and brute-force it (test difficulty is ~8 bits)."""
    from app.core.pow import solve

    r = await client.get("/api/v1/pow/challenge", params={"scope": scope})
    assert r.status_code == 200, r.text
    ch = r.json()
    return {"challenge": ch["challenge"], "nonce": solve(ch["challenge"], ch["bits"])}


async def open_conversation(client: httpx.AsyncClient, product_id=None) -> dict:
    body = {"product_id": str(product_id)} if product_id else {}
    r = await client.post("/api/v1/conversations", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def send_quote(staff: httpx.AsyncClient, conv_id: str, amount: str = "15.00", **over) -> dict:
    body = {"title": "Special deal", "amount": amount, "qty": 1, "expires_in_minutes": 30}
    body.update(over)
    r = await staff.post(f"/api/v1/admin/conversations/{conv_id}/quotes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def buy(client: httpx.AsyncClient, product, qty: int = 1, **extra) -> dict:
    """POST /orders (buy now). The response carries the one-time ``access_code``."""
    r = await client.post(
        "/api/v1/orders", json={"product_id": str(product.id), "qty": qty, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def pay_order(env, client: httpx.AsyncClient, order: dict) -> None:
    r = await client.post(f"/api/v1/orders/{order['id']}/pay", json={"channel": "zpay_alipay"})
    assert r.status_code == 200, r.text
    resp = await simulate_gateway_payment(env, r.json()["pay_url"])
    assert resp.status_code == 302, resp.text


async def delivered_order(
    env, client: httpx.AsyncClient, product, qty: int = 1, staff=None
) -> dict:
    """Buy, pay and deliver (auto_card via the worker; manual via ``staff`` console)."""
    order = await buy(client, product, qty)
    code = order["access_code"]
    await pay_order(env, client, order)
    if product.delivery_mode == "auto_card":
        assert await env.drain() >= 1
    else:
        r = await staff.post(
            f"/api/v1/admin/orders/{order['id']}/deliver", json={"text": "account: foo / bar"}
        )
        assert r.status_code == 200, r.text
    fresh = (await client.get(f"/api/v1/orders/{order['id']}")).json()
    assert fresh["status"] == "delivered", fresh
    fresh["access_code"] = code
    return fresh
