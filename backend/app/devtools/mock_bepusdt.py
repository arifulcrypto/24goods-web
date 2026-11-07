"""BEpusdt simulator (dev/demo/CI only): ``python -m app.devtools.mock_bepusdt`` on :8003.

Implements the subset we use, with the real signing scheme (app.payments.bepusdt):

* ``POST /api/v1/order/create-transaction`` (signed) -> trade_id, address, actual_amount ...
* ``POST /api/v1/pay/info`` (unauthenticated, like upstream)
* ``POST|GET /pay/{trade_id}`` -> "Simulate payment": marks the trade paid and sends the signed
  JSON notify (status 2) to ``notify_url`` (retried until the body is ``ok``), then redirects.
  Served at ``/`` and under ``/mockusdt`` so Caddy may or may not strip the prefix.

Refuses to start unless ``APP_ENV`` is dev or test.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.middleware import SECURITY_HEADERS
from app.devtools.mock_zpay import rebase
from app.payments import bepusdt

log = logging.getLogger("app.mockusdt")

TRADE_TTL_S = 24 * 3600
NOTIFY_ATTEMPTS = 5
RATE = Decimal("7.20")  # CNY per USDT
ADDRESSES = {
    "usdt.arbitrum": "0xMOCK0000000000000000000000000000000a4b1",
    "usdt.xlayer": "0xMOCK000000000000000000000000000000000196",
    "usdt.trc20": "TMockAddressForDemoOnly000000000000",
}


def _key(trade_id: str) -> str:
    return f"mockpay:usdt:{trade_id}"


def _okey(order_id: str) -> str:
    return f"mockpay:usdt:o:{order_id}"


def ok(data: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"status_code": 200, "message": "success", "data": data, "request_id": ""})


def fail(msg: str) -> JSONResponse:
    return JSONResponse({"status_code": 400, "message": msg, "data": None, "request_id": ""})


def notify_body(trade: dict[str, Any], token: str, status: int = 2) -> dict[str, Any]:
    """The JSON body upstream posts to ``notify_url`` (signed). Used by tests too."""
    body: dict[str, Any] = {
        "trade_id": trade["trade_id"],
        "order_id": trade["order_id"],
        "amount": trade["amount"],
        "actual_amount": trade["actual_amount"],
        "token": trade["address"],
        "block_transaction_id": trade.get("block_tx") or "0x" + "ab" * 32,
        "status": status,
        "created_at": trade["created_at"],
        "expired_at": trade["expires_at"],
    }
    body["signature"] = bepusdt.sign(body, token)
    return body


def create_mock_app(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    router = APIRouter()
    token = settings.bepusdt_api_token

    async def _load(trade_id: str) -> dict[str, Any] | None:
        raw = await redis.get(_key(trade_id))
        return json.loads(raw) if raw else None

    async def _save(trade: dict[str, Any]) -> None:
        await redis.set(_key(trade["trade_id"]), json.dumps(trade), ex=TRADE_TTL_S)

    @router.post("/api/v1/order/create-transaction")
    async def create(request: Request) -> JSONResponse:
        try:
            p = bepusdt.parse_json(await request.body())
        except ValueError:
            return fail("bad json")
        if not bepusdt.verify_signature(p, token):
            return fail("signature error")
        amount = bepusdt.decimal_of(p.get("amount"))
        order_id = p.get("order_id")
        trade_type = p.get("trade_type")
        if amount is None or amount <= 0:  # 0 would be upstream's "exclusive address" mode
            return fail("amount must be > 0")
        if not isinstance(order_id, str) or not order_id or trade_type not in ADDRESSES:
            return fail("bad params")
        for f in ("notify_url", "redirect_url"):
            if not isinstance(p.get(f), str) or not p[f].startswith(("http://", "https://")):
                return fail(f"bad {f}")
        timeout = int(p.get("timeout") or 600)
        now = int(time.time())
        trade = {
            "trade_id": str(uuid.uuid4()),
            "order_id": order_id,
            "amount": str(amount),
            "fiat": p.get("fiat") or "CNY",
            "trade_type": trade_type,
            "actual_amount": str((amount / RATE).quantize(Decimal("0.01"), ROUND_HALF_UP)),
            "address": ADDRESSES[trade_type],
            "status": 1,
            "notify_url": p["notify_url"],
            "redirect_url": p["redirect_url"],
            "created_at": now,
            "expires_at": now + timeout,
        }
        await _save(trade)
        await redis.set(_okey(order_id), trade["trade_id"], ex=TRADE_TTL_S)
        return ok(
            {
                "fiat": trade["fiat"],
                "trade_id": trade["trade_id"],
                "order_id": order_id,
                "amount": trade["amount"],
                "actual_amount": trade["actual_amount"],
                "token": trade["address"],
                "expiration_time": timeout,
                "status": 1,
                "payment_url": f"/mockusdt/pay/{trade['trade_id']}",
            }
        )

    @router.post("/api/v1/pay/info")
    async def info(request: Request) -> JSONResponse:
        try:
            p = bepusdt.parse_json(await request.body())
        except ValueError:
            return fail("bad json")
        trade = await _load(str(p.get("trade_id", "")))
        if trade is None:
            return fail("order not found")
        return ok(
            {
                "trade_id": trade["trade_id"],
                "order_id": trade["order_id"],
                "trade_type": trade["trade_type"],
                "status": trade["status"],
                "money": float(Decimal(trade["amount"])),
                "actual_amount": trade["actual_amount"],
                "token": trade["address"],
                "fiat": trade["fiat"],
                "expired_at": trade["expires_at"],
                "created_at": trade["created_at"],
            }
        )

    async def _notify(trade: dict[str, Any]) -> bool:
        url = rebase(trade["notify_url"], settings.mockpay_notify_base)
        body = notify_body(trade, token)
        for attempt in range(NOTIFY_ATTEMPTS):
            try:
                resp = await http.post(url, json=body, timeout=10.0)
                if resp.text.strip().lower() == "ok":
                    return True
            except httpx.HTTPError as exc:
                log.warning("notify attempt %d failed error=%s", attempt + 1, type(exc).__name__)
            await asyncio.sleep(0.3 * (attempt + 1))
        return False

    def _html(body: str, status: int = 200) -> HTMLResponse:
        doc = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>BEpusdt Simulator</title></head>"
            '<body style="font-family:system-ui;max-width:420px;margin:24px auto;padding:0 16px">'
            f"{body}</body></html>"
        )
        csp = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
        headers = {**SECURITY_HEADERS, "Content-Security-Policy": csp, "Cache-Control": "no-store"}
        return HTMLResponse(doc, status_code=status, headers=headers)

    @router.get("/pay/{trade_id}")
    async def page(trade_id: str) -> HTMLResponse:
        trade = await _load(trade_id)
        if trade is None:
            return _html("<h1>Unknown trade</h1>", 404)
        e = html.escape
        return _html(
            f"<h1>BEpusdt Simulator</h1><p>Demo only, no real money.</p>"
            f"<p>{e(trade['trade_type'])}<br><b>{e(trade['actual_amount'])} USDT</b><br>"
            f"{e(trade['address'])}</p>"
            f'<form method=post action=""><button style="min-height:48px;width:100%">'
            "Simulate successful payment / 模拟支付成功</button></form>"
        )

    @router.post("/pay/{trade_id}", response_model=None)
    async def pay(trade_id: str) -> RedirectResponse | HTMLResponse:
        trade = await _load(trade_id)
        if trade is None:
            return _html("<h1>Unknown trade</h1>", 404)
        if trade["status"] != 2:
            trade["status"] = 2
            trade["block_tx"] = "0x" + secrets.token_hex(32)
            await _save(trade)
        if not await _notify(trade):
            log.warning("merchant did not acknowledge notify for %s", trade["order_id"])
        return RedirectResponse(trade["redirect_url"], status_code=302)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(router, prefix="/mockusdt")
    return app


async def _main() -> None:
    import uvicorn

    from app.core.logging import configure_logging
    from app.core.redis import make_redis

    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.env not in ("dev", "test"):
        raise SystemExit("mock_bepusdt only runs with APP_ENV=dev|test")
    if not settings.bepusdt_api_token:
        raise SystemExit("APP_BEPUSDT_API_TOKEN is required")
    redis = make_redis(settings.redis_url)
    http = httpx.AsyncClient(follow_redirects=False)
    app = create_mock_app(settings, redis, http)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.mockusdt_host,
            port=settings.mockusdt_port,
            access_log=False,
            log_config=None,
            server_header=False,
            proxy_headers=False,
            lifespan="off",
            loop="asyncio",
            http="h11",
        )
    )
    try:
        await server.serve()
    finally:
        await http.aclose()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
