"""BEpusdt (self-hosted USDT gateway) adapter. See docs/research/bepusdt.md.

* Requests and notifies are signed with the upstream's scheme: every non-empty param except
  ``signature``, sorted by key, ``k=v`` joined by ``&``, API token appended, MD5 lowercase.
  MD5 is the gateway's protocol, not our choice: we pin it, compare in constant time and never
  trust the notify alone (the caller re-queries ``/api/v1/pay/info`` server-to-server).
* BEpusdt is reachable on the internal docker network only. The API token never appears in a
  log line or an exception message (transport errors are re-raised ``from None``).
* Money: the order amount is CNY (fiat). Amounts are parsed as ``Decimal`` (JSON floats are
  read with ``parse_float=Decimal``); the fiat amount is sent as the exact decimal literal.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.modules.payments.channels import USDT_CHANNELS
from app.payments.bounded import bounded_decimal, bounded_integer, reject_constant, response_bytes

log = logging.getLogger("app.payments.bepusdt")

TRADE_TYPE_TO_CHANNEL = {v: k for k, v in USDT_CHANNELS.items()}
STATUS_WAITING, STATUS_PAID, STATUS_EXPIRED = 1, 2, 3
FIAT = "CNY"
TIMEOUT_S = 4.0
MAX_BODY = 8192


class BepusdtError(Exception):
    """Gateway call failed (transport / protocol). Message never contains the token."""


class BepusdtRefused(BepusdtError):
    """The gateway answered but said no (``status_code != 200``, e.g. unknown trade_id)."""


def _scalar(v: Any, normalize: bool) -> str | None:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        bounded_decimal(v)
        return format(v.normalize(), "f") if normalize else str(v)
    if isinstance(v, (str, int)):
        return str(v)
    return None


def canonical_string(params: Mapping[str, Any], normalize: bool = False) -> str:
    """``normalize`` renders JSON floats without trailing zeros (``100.00`` -> ``100``), which is
    how a Go ``float64`` is stringified upstream."""
    items = sorted(
        (str(k), s)
        for k, v in params.items()
        if k != "signature"
        and v not in (None, "")
        and (s := _scalar(v, normalize)) not in (None, "")
    )
    return "&".join(f"{k}={v}" for k, v in items)


def sign(params: Mapping[str, Any], token: str, normalize: bool = False) -> str:
    # usedforsecurity=False: MD5 is mandated by the external BEpusdt protocol.
    canon = canonical_string(params, normalize) + token
    return hashlib.md5(canon.encode(), usedforsecurity=False).hexdigest()


def verify_signature(params: Mapping[str, Any], token: str) -> bool:
    given = params.get("signature")
    if not token or not isinstance(given, str) or not given:
        return False
    given = given.lower()
    if len(given) != 32 or any(c not in "0123456789abcdef" for c in given):
        return False
    try:
        ok_raw = hmac.compare_digest(given, sign(params, token))
        ok_norm = hmac.compare_digest(given, sign(params, token, normalize=True))
    except ValueError:
        return False
    return ok_raw or ok_norm


def parse_json(raw: bytes) -> dict[str, Any]:
    """JSON object with floats as ``Decimal`` (never ``float``). Raises ValueError."""
    if len(raw) > MAX_BODY:
        raise ValueError("body too large")
    data = json.loads(
        raw,
        parse_float=bounded_decimal,
        parse_int=bounded_integer,
        parse_constant=reject_constant,
    )
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def decimal_of(v: Any) -> Decimal | None:
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        d = bounded_decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def amount_literal(amount: Decimal) -> str:
    """Exact decimal text used both in the signature and the JSON body (28.80 -> ``28.8``)."""
    return format(amount.normalize(), "f")


@dataclass(frozen=True)
class Attempt:
    trade_id: str
    order_id: str
    amount: Decimal  # fiat (CNY) as echoed by the gateway
    actual_amount: Decimal  # USDT to send
    address: str
    expiration_s: int
    payment_url: str


@dataclass(frozen=True)
class TradeInfo:
    found: bool
    status: int | None
    trade_id: str
    order_id: str
    money: Decimal | None
    fiat: str
    trade_type: str
    actual_amount: Decimal | None
    address: str


@dataclass(frozen=True)
class BepusdtConfig:
    base_url: str
    token: str
    notify_url: str
    timeout_s: int


class BepusdtClient:
    def __init__(self, config: BepusdtConfig, http: httpx.AsyncClient):
        self.config = config
        self._http = http

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url and self.config.token)

    def verify_signature(self, params: Mapping[str, Any]) -> bool:
        return verify_signature(params, self.config.token)

    async def _post(self, path: str, body: str) -> dict[str, Any]:
        try:

            async def fetch() -> dict[str, Any]:
                async with self._http.stream(
                    "POST",
                    self.config.base_url.rstrip("/") + path,
                    content=body.encode(),
                    headers={"Content-Type": "application/json", "Accept-Encoding": "identity"},
                    timeout=httpx.Timeout(TIMEOUT_S, connect=1.5),
                ) as response:
                    return parse_json(await response_bytes(response, MAX_BODY))

            data = await asyncio.wait_for(fetch(), timeout=TIMEOUT_S + 1)
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            name = type(exc).__name__
            log.warning("bepusdt call failed path=%s error=%s", path, name)
            raise BepusdtError(f"request failed: {name}") from None
        code = data.get("status_code")
        if isinstance(code, int) and not isinstance(code, bool) and code != 200:
            # a well-formed refusal: nothing was created (callers may fail the attempt)
            raise BepusdtRefused("gateway refused the request")
        if code != 200 or not isinstance(data.get("data"), dict):
            # malformed success: the outcome is unknown, never treated as a refusal (#72 audit M2)
            raise BepusdtError("malformed gateway response")
        return data["data"]

    async def create_transaction(
        self,
        *,
        order_id: str,
        amount: Decimal,
        channel: str,
        name: str,
        redirect_url: str,
    ) -> Attempt:
        """Fixed-amount mode only: ``amount`` must be > 0 (0 would be 'exclusive address' mode)."""
        if amount <= 0 or not amount.is_finite():
            raise BepusdtError("amount must be positive")
        c = self.config
        lit = amount_literal(amount)
        params: dict[str, Any] = {
            "order_id": order_id,
            "amount": lit,
            "fiat": FIAT,
            "trade_type": USDT_CHANNELS[channel],
            "name": name[:60],
            "notify_url": c.notify_url,
            "redirect_url": redirect_url,
            "timeout": c.timeout_s,
        }
        params["signature"] = sign(params, c.token)
        # `amount` goes out as a JSON number with the exact literal that was signed
        body = json.dumps({**params, "amount": "@@AMOUNT@@"}).replace('"@@AMOUNT@@"', lit)
        d = await self._post("/api/v1/order/create-transaction", body)
        try:
            actual = decimal_of(d.get("actual_amount"))
            fiat_amt = decimal_of(d.get("amount"))
            if (
                actual is None
                or actual <= 0
                or fiat_amt != amount
                or d.get("order_id") != order_id
                or not d.get("trade_id")
                or not d.get("token")
                # an existing trade of another network would show the wrong chain's address
                or d.get("trade_type", params["trade_type"]) != params["trade_type"]
            ):
                raise BepusdtError("unexpected create-transaction response")
            return Attempt(
                trade_id=str(d["trade_id"])[:64],
                order_id=order_id,
                amount=fiat_amt,
                actual_amount=actual,
                address=str(d["token"])[:128],
                expiration_s=int(d.get("expiration_time") or c.timeout_s),
                payment_url=str(d.get("payment_url") or ""),
            )
        except (TypeError, ValueError):
            raise BepusdtError("unexpected create-transaction response") from None

    async def query_info(self, trade_id: str) -> TradeInfo:
        """Server-to-server re-query (``/api/v1/pay/info`` is unauthenticated upstream)."""
        try:
            d = await self._post("/api/v1/pay/info", json.dumps({"trade_id": trade_id}))
        except BepusdtRefused:  # unknown trade_id
            return TradeInfo(False, None, trade_id, "", None, "", "", None, "")
        status = d.get("status")
        return TradeInfo(
            found=True,
            status=status if isinstance(status, int) and not isinstance(status, bool) else None,
            trade_id=str(d.get("trade_id") or ""),
            order_id=str(d.get("order_id") or ""),
            money=decimal_of(d.get("money")),
            fiat=str(d.get("fiat") or ""),
            trade_type=str(d.get("trade_type") or ""),
            actual_amount=decimal_of(d.get("actual_amount")),
            address=str(d.get("token") or ""),
        )
