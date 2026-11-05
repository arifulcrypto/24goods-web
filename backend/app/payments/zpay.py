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
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx

from app.payments import epay

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
        return QueryResult(False, False, None, "", str(data.get("out_trade_no") or ""))
    status = _as_int(_first_key(data, "status", "trade_status"))
    if status is None and str(data.get("trade_status", "")).upper() == "TRADE_SUCCESS":
        status = 1
    money_raw = _first_key(data, "money", "total_amount")
    money: Decimal | None
    try:
        money = Decimal(str(money_raw)) if money_raw is not None else None
    except InvalidOperation:
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

    async def query_order(self, order_no: str) -> QueryResult:
        c = self.config
        query = {"act": "order", "pid": c.pid, "key": c.key, "out_trade_no": order_no}
        try:
            # ZPay waits 5 s for our reply: cap the whole re-query at ~3 s.
            resp = await asyncio.wait_for(
                self._http.get(
                    c.api_base.rstrip("/") + "/api.php",
                    params=query,
                    timeout=httpx.Timeout(QUERY_TIMEOUT_S, connect=1.5),
                ),
                timeout=QUERY_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            name = type(exc).__name__
            log.warning("zpay re-query failed order=%s error=%s", order_no, name)
            raise ZPayQueryError(f"query failed: {name}") from None
        return parse_query_response(data)
