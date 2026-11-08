"""ZPay (易支付-compatible) adapter on top of :mod:`app.payments.epay`.

* ``build_pay_url`` -> signed ``{gateway}/submit.php?...`` the browser navigates to.
* ``query_order``  -> server-to-server re-query
  ``GET {api}/api.php?act=order&pid&key&out_trade_no``.
  That URL contains the merchant key: it is never logged, and transport errors are
  re-raised without the URL (``from None``).
* ``verify_notify`` -> hardened notify verification (pinned MD5, pid, order, status, amount).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.payments import epay
from app.payments.bounded import bounded_decimal, bounded_integer, response_bytes

log = logging.getLogger("app.payments.zpay")

CHANNEL = "zpay_alipay"
PAY_TYPE = "alipay"


class ZPayQueryError(Exception):
    """Re-query failed (transport / protocol). Message never contains the key or URL."""


@dataclass(frozen=True)
class ZPayConfig:
    pid: str
    key: str
    gateway: str  # browser-facing base, e.g. https://zpayz.cn
    api_base: str  # server-to-server base for api.php
    notify_url: str
    return_url: str
    safe_network: bool = False
    instance_id: str = ""


@dataclass(frozen=True)
class QueryResult:
    found: bool
    paid: bool
    money: Decimal | None
    trade_no: str
    out_trade_no: str


def _first_key(data: Mapping[str, Any], *names: str) -> Any:
    for n in names:
        if n in data and data[n] not in (None, ""):
            return data[n]
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_query_response(data: Any) -> QueryResult:
    """Tolerant parser: ``code``/``status`` may be int or str; money may be str or number."""
    if not isinstance(data, Mapping):
        raise ZPayQueryError("unexpected query response shape")
    code = _as_int(data.get("code"))
    if code != 1:
        message = str(data.get("msg", "")).strip().lower().rstrip(".!！。")
        if code == -1 and message in {"order not found", "not found", "订单不存在"}:
            return QueryResult(False, False, None, "", str(data.get("out_trade_no") or ""))
        raise ZPayQueryError("query response did not confirm an order status")
    status = _as_int(_first_key(data, "status", "trade_status"))
    if status is None and str(data.get("trade_status", "")).upper() == "TRADE_SUCCESS":
        status = 1
    if status not in (0, 1):
        raise ZPayQueryError("query returned an unknown payment status")
    money_raw = _first_key(data, "money", "total_amount")
    money: Decimal | None
    try:
        money = bounded_decimal(str(money_raw)) if money_raw is not None else None
    except (InvalidOperation, ValueError):
        money = None
    if money is not None and not money.is_finite():
        money = None
    return QueryResult(
        found=True,
        paid=status == 1,
        money=money,
        trade_no=str(_first_key(data, "trade_no", "api_trade_no") or ""),
        out_trade_no=str(data.get("out_trade_no") or ""),
    )


QUERY_TIMEOUT_S = 3.0


class ZPayClient:
    def __init__(self, config: ZPayConfig, http: httpx.AsyncClient):
        self.config = config
        self._http = http

    def pay_params(self, order_no: str, name: str, amount: Decimal) -> dict[str, str]:
        c = self.config
        params = {
            "pid": c.pid,
            "type": PAY_TYPE,
            "out_trade_no": order_no,
            "notify_url": c.notify_url,
            "return_url": c.return_url,
            "name": name[:60],
            "money": f"{amount:.2f}",
        }
        params["sign"] = epay.sign(params, c.key)
        params["sign_type"] = "MD5"
        return params

    def build_pay_url(self, order_no: str, name: str, amount: Decimal) -> str:
        base = self.config.gateway.rstrip("/")
        return f"{base}/submit.php?{urlencode(self.pay_params(order_no, name, amount))}"

    def verify_notify(self, params: Mapping[str, str], expected: epay.ExpectedOrder) -> str:
        return epay.verify_notify(params, self.config.key, expected)

    def verify_signature(self, params: Mapping[str, str]) -> bool:
        return epay.verify_signature(params, self.config.key)

    async def _json(self, endpoint: str, params: dict[str, str], *, post: bool = False) -> Any:
        """Bounded transport. Database-configured gateways use DNS-pinned public HTTPS only."""
        from app.core import safefetch

        url = self.config.api_base.rstrip("/") + endpoint
        try:
            if self.config.safe_network:
                target = await safefetch.vet_url(url)
                if target.scheme != "https":
                    raise ZPayQueryError("HTTPS gateway required")
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                    async with client.stream(
                        "POST" if post else "GET",
                        safefetch._pinned_url(target),
                        data=params if post else None,
                        params=None if post else params,
                        headers={"Host": target.host, "Accept-Encoding": "identity"},
                        extensions={"sni_hostname": target.host},
                        timeout=QUERY_TIMEOUT_S,
                    ) as response:
                        raw = await response_bytes(response, 256 * 1024)
                        return json.loads(
                            raw, parse_float=bounded_decimal, parse_int=bounded_integer
                        )
            async with self._http.stream(
                "POST" if post else "GET",
                url,
                data=params if post else None,
                params=None if post else params,
                headers={"Accept-Encoding": "identity"},
                timeout=QUERY_TIMEOUT_S,
            ) as response:
                raw = await response_bytes(response, 256 * 1024)
                return json.loads(raw, parse_float=bounded_decimal, parse_int=bounded_integer)
        except (httpx.HTTPError, ValueError, safefetch.FetchError):
            raise ZPayQueryError("gateway request failed") from None

    async def create_payment(
        self, order_no: str, name: str, amount: Decimal, client_ip: str
    ) -> dict[str, str]:
        params = self.pay_params(order_no, name, amount)
        params["clientip"] = client_ip
        params["device"] = "pc"
        params["sign"] = epay.sign(params, self.config.key)
        data = await asyncio.wait_for(self._json("/mapi.php", params, post=True), QUERY_TIMEOUT_S)
        if not isinstance(data, dict) or str(data.get("code")) != "1":
            raise ZPayQueryError("gateway refused payment")
        qr, redirect = str(data.get("qrcode") or ""), str(data.get("payurl") or "")
        value, kind = (qr, "qr") if qr else (redirect, "redirect")
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        approved = {
            urlsplit(self.config.gateway).hostname,
            "qr.alipay.com",
            "www.alipay.com",
            "pay.weixin.qq.com",
        }
        if (
            not value
            or len(value) > 2048
            or any(ord(c) < 33 for c in value)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not (
                (parsed.scheme == "https" and host in approved)
                or (kind == "qr" and parsed.scheme in ("weixin", "alipays"))
            )
        ):
            raise ZPayQueryError("unsupported payment presentation")
        return {"kind": kind, "payload": value, "trade_no": str(data.get("trade_no") or "")[:128]}

    async def query_order(self, order_no: str) -> QueryResult:
        c = self.config
        query = {"act": "order", "pid": c.pid, "key": c.key, "out_trade_no": order_no}
        try:
            # ZPay waits 5 s for our reply: cap the whole re-query at ~3 s.
            data = await asyncio.wait_for(self._json("/api.php", query), timeout=QUERY_TIMEOUT_S)
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            name = type(exc).__name__
            log.warning("zpay re-query failed order=%s error=%s", order_no, name)
            raise ZPayQueryError(f"query failed: {name}") from None
        if self.config.instance_id and isinstance(data, dict) and str(data.get("code")) == "1":
            if str(data.get("pid", "")) != self.config.pid or data.get("type") != PAY_TYPE:
                raise ZPayQueryError("query merchant or method mismatch")
        parsed = parse_query_response(data)
        if parsed.found and parsed.out_trade_no != order_no:
            raise ZPayQueryError("query order binding mismatch")
        return parsed
