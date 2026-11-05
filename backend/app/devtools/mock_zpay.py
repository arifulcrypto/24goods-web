"""ZPay simulator (dev/demo only): ``python -m app.devtools.mock_zpay`` on :8002.

* ``/submit.php`` (GET/POST): verifies the merchant signature, stores the order in Redis
  and renders a self-contained page (no external assets, EN + 中文).
* ``/pay``: "Simulate successful payment" -> marks paid, sends a signed GET notify to the
  merchant (server-side, retried until the body is ``success``), then 302s the browser to
  ``return_url`` with signed params. "Cancel" returns without paying.
* ``/api.php?act=order&pid&key&out_trade_no``: ZPay-style order query (JSON).

Routes are served both at ``/`` and under ``/mockpay`` so Caddy may or may not strip the
prefix. Refuses to start when ``APP_ENV=prod``.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.middleware import SECURITY_HEADERS
from app.payments import epay

log = logging.getLogger("app.mockpay")

ORDER_TTL_S = 24 * 3600
NOTIFY_ATTEMPTS = 5
REQUIRED = ("pid", "type", "out_trade_no", "notify_url", "return_url", "name", "money")
PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'{extra}; base-uri 'none'"
)


def _key(out_trade_no: str) -> str:
    return f"mockpay:order:{out_trade_no}"


def rebase(url: str, base: str) -> str:
    """Replace scheme+host of ``url`` with ``base`` (docker: reach the API at http://api:8000)."""
    if not base:
        return url
    u, b = urlsplit(url), urlsplit(base)
    return urlunsplit((b.scheme, b.netloc, u.path, u.query, ""))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _origin(url: str) -> str:
    u = urlsplit(url)
    if u.scheme in ("http", "https") and u.netloc and all(c not in u.netloc for c in " ;'\""):
        return f"{u.scheme}://{u.netloc}"
    return ""


def _page(title: str, body: str, status: int = 200, form_origin: str = "") -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;
background:#f2f4f7;color:#1d2129}}main{{max-width:420px;margin:0 auto;padding:24px 16px}}
.card{{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h1{{font-size:18px;margin:0 0 4px}}.sub{{color:#86909c;font-size:13px;margin-bottom:16px}}
.amt{{font-size:32px;font-weight:700;color:#1677ff;margin:12px 0}}.row{{display:flex;
justify-content:space-between;font-size:14px;padding:6px 0;border-bottom:1px solid #f0f0f0}}
button{{width:100%;min-height:48px;border:0;border-radius:24px;font-size:16px;margin-top:12px;
cursor:pointer}}.pay{{background:#1677ff;color:#fff}}.cancel{{background:#f2f3f5;color:#4e5969}}
.warn{{font-size:12px;color:#f53f3f;margin-top:16px;text-align:center}}</style></head>
<body><main>{body}</main></body></html>"""
    csp = PAGE_CSP.format(extra=f" {form_origin}" if form_origin else "")
    headers = {**SECURITY_HEADERS, "Content-Security-Policy": csp, "Cache-Control": "no-store"}
    return HTMLResponse(doc, status_code=status, headers=headers)


def create_mock_app(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    router = APIRouter()
    pid, key = settings.zpay_pid, settings.zpay_key

    async def _params(request: Request) -> dict[str, str]:
        params: dict[str, str] = {}
        if request.method == "POST":
            form = await request.form(max_fields=40, max_files=0)
            params.update({k: v for k, v in form.multi_items() if isinstance(v, str)})
        for k, v in request.query_params.multi_items():
            params.setdefault(k, v)
        return params

    @router.api_route("/submit.php", methods=["GET", "POST"])
    async def submit(request: Request) -> HTMLResponse:
        p = await _params(request)
        if any(not p.get(f) for f in REQUIRED) or p.get("pid") != pid:
            return _page(
                "ZPay Simulator — error",
                "<div class=card><h1>Invalid request / 参数错误</h1></div>",
                400,
            )
        if not epay.verify_signature(p, key):
            return _page(
                "ZPay Simulator — error",
                "<div class=card><h1>Bad signature / 签名错误</h1></div>",
                400,
            )
        try:
            money = Decimal(p["money"])
        except InvalidOperation:
            money = Decimal(0)
        if money <= 0 or money.as_tuple().exponent < -2:  # type: ignore[operator]
            return _page(
                "ZPay Simulator — error",
                "<div class=card><h1>Bad amount / 金额错误</h1></div>",
                400,
            )
        if urlsplit(p["notify_url"]).scheme not in ("http", "https"):
            return _page(
                "ZPay Simulator — error", "<div class=card><h1>Bad notify_url</h1></div>", 400
            )
        rkey = _key(p["out_trade_no"])
        existing = await redis.get(rkey)
        record = json.loads(existing) if existing else {}
        if record.get("status") == 1:
            nonce = record["nonce"]
        else:
            nonce = secrets.token_urlsafe(16)
            record = {
                "params": {k: p[k] for k in REQUIRED},
                "status": 0,
                "trade_no": "",
                "nonce": nonce,
                "addtime": _now(),
                "endtime": "",
            }
            await redis.set(rkey, json.dumps(record), ex=ORDER_TTL_S)
        e = html.escape
        body = f"""<div class=card><h1>ZPay Simulator — Alipay</h1>
<div class=sub>支付宝模拟收银台 · Demo only, no real money</div>
<div class=amt>¥ {e(p["money"])}</div>
<div class=row><span>Item / 商品</span><span>{e(p["name"])}</span></div>
<div class=row><span>Order / 订单号</span><span>{e(p["out_trade_no"])}</span></div>
<form method=post action="pay">
<input type=hidden name=out_trade_no value="{e(p["out_trade_no"])}">
<input type=hidden name=nonce value="{e(nonce)}">
<button class=pay name=action value=pay type=submit>
Simulate successful payment / 模拟支付成功</button>
<button class=cancel name=action value=cancel type=submit>Cancel / 取消</button>
</form><div class=warn>Simulator · 模拟器 — not a real payment</div></div>"""
        # Chrome applies form-action to the post-submit redirect to return_url.
        return _page("ZPay Simulator — Alipay", body, form_origin=_origin(p["return_url"]))

    def _signed_result(record: dict) -> dict[str, str]:
        p = record["params"]
        out = {
            "pid": pid,
            "trade_no": record["trade_no"],
            "out_trade_no": p["out_trade_no"],
            "type": p["type"],
            "name": p["name"],
            "money": p["money"],
            "trade_status": "TRADE_SUCCESS",
        }
        out["sign"] = epay.sign(out, key)
        out["sign_type"] = "MD5"
        return out

    async def _notify(record: dict) -> bool:
        url = rebase(record["params"]["notify_url"], settings.mockpay_notify_base)
        params = _signed_result(record)
        for attempt in range(NOTIFY_ATTEMPTS):
            try:
                resp = await http.get(url, params=params, timeout=10.0)
                if resp.text.strip() == "success":
                    return True
            except httpx.HTTPError as exc:
                log.warning("notify attempt %d failed error=%s", attempt + 1, type(exc).__name__)
            await asyncio.sleep(0.3 * (attempt + 1))
        return False

    @router.post("/pay", response_model=None)
    async def pay(request: Request) -> RedirectResponse | HTMLResponse:
        form = await request.form(max_fields=10, max_files=0)
        out_trade_no = str(form.get("out_trade_no", ""))
        nonce = str(form.get("nonce", ""))
        action = str(form.get("action", ""))
        raw = await redis.get(_key(out_trade_no))
        if not raw:
            return _page(
                "ZPay Simulator — error",
                "<div class=card><h1>Unknown order / 订单不存在</h1></div>",
                404,
            )
        record = json.loads(raw)
        if not hmac.compare_digest(nonce.encode(), str(record.get("nonce", "")).encode()):
            return _page(
                "ZPay Simulator — error", "<div class=card><h1>Invalid session</h1></div>", 400
            )
        return_url = record["params"]["return_url"]
        if action != "pay":
            return RedirectResponse(
                f"{return_url}?{urlencode({'out_trade_no': out_trade_no})}", status_code=302
            )
        if record["status"] != 1:
            record["status"] = 1
            record["trade_no"] = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "".join(
                secrets.choice("0123456789") for _ in range(10)
            )
            record["endtime"] = _now()
            await redis.set(_key(out_trade_no), json.dumps(record), ex=ORDER_TTL_S)
        delivered = await _notify(record)
        if not delivered:
            log.warning("merchant did not acknowledge notify for %s", out_trade_no)
        return RedirectResponse(
            f"{return_url}?{urlencode(_signed_result(record))}", status_code=302
        )

    @router.get("/api.php")
    async def api(request: Request) -> JSONResponse:
        q = request.query_params
        if q.get("act") != "order":
            return JSONResponse({"code": -1, "msg": "unsupported act"})
        if q.get("pid") != pid or not hmac.compare_digest(
            str(q.get("key", "")).encode(), key.encode()
        ):
            return JSONResponse({"code": -2, "msg": "merchant authentication failed"})
        raw = await redis.get(_key(str(q.get("out_trade_no", ""))))
        if not raw:
            return JSONResponse({"code": -1, "msg": "order not found"})
        record = json.loads(raw)
        p = record["params"]
        return JSONResponse(
            {
                "code": 1,
                "msg": "succ",
                "trade_no": record["trade_no"],
                "out_trade_no": p["out_trade_no"],
                "type": p["type"],
                "pid": pid,
                "addtime": record["addtime"],
                "endtime": record["endtime"],
                "name": p["name"],
                "money": p["money"],
                "status": record["status"],
            }
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(router, prefix="/mockpay")
    return app


async def _main() -> None:
    import uvicorn

    from app.core.logging import configure_logging
    from app.core.redis import make_redis

    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.is_prod:
        raise SystemExit("mock_zpay refuses to run with APP_ENV=prod")
    if not settings.zpay_pid or not settings.zpay_key:
        raise SystemExit("APP_ZPAY_PID and APP_ZPAY_KEY are required")
    redis = make_redis(settings.redis_url)
    http = httpx.AsyncClient(follow_redirects=False)
    app = create_mock_app(settings, redis, http)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.mockpay_host,
            port=settings.mockpay_port,
            access_log=False,  # api.php query strings carry the merchant key
            log_config=None,
            server_header=False,
            proxy_headers=False,
            lifespan="off",
        )
    )
    try:
        await server.serve()
    finally:
        await http.aclose()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
