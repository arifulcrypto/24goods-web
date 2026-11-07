"""USDT pay attempts (self-hosted BEpusdt, docs/review-v5.2.md s7).

An *attempt* is what BEpusdt returned for one order/checkout: trade_id, receiving address,
exact USDT amount and expiry. It is display state for our own pay page (and the trade_id the
cancel-time re-query uses), kept in Redis with a TTL; fulfilment NEVER trusts it: the notify
pipeline re-queries the gateway and matches order, channel, money and trade_id itself.
"""

from __future__ import annotations

import io
import json
import time
from decimal import Decimal
from typing import Any

import segno

from app.core.errors import Unavailable
from app.core.resources import Resources
from app.modules.payments.channels import LABELS, USDT_CHANNELS
from app.payments.bepusdt import BepusdtError

ATTEMPT_KEEP_S = 7 * 24 * 3600
REUSE_MARGIN_S = 30
LOCK_TTL_S = 15

NETWORKS = {
    "usdt_arbitrum": "Arbitrum One",
    "usdt_xlayer": "X Layer",
    "usdt_trc20": "TRON (TRC20)",
}


def attempt_key(no: str) -> str:
    return f"usdt:attempt:{no}"


def qr_svg(text: str) -> str:
    """Server-side QR as an inline-safe SVG (no scripts; generated from our own data only)."""
    buf = io.BytesIO()
    segno.make(text, error="m").save(
        buf, kind="svg", scale=4, border=2, xmldecl=False, svgns=True, nl=False
    )
    return buf.getvalue().decode()


async def load_attempt(res: Resources, no: str) -> dict[str, Any] | None:
    raw = await res.redis.get(attempt_key(no))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def drop_attempt(res: Resources, no: str) -> None:
    """Forget the stored attempt (the buyer switched channel / the amount changed): the page
    stops showing the old address and a fresh one is created for the new amount. A transfer
    already sent to the OLD address still arrives as a notify and goes to manual review."""
    await res.redis.delete(attempt_key(no))


async def start_attempt(
    res: Resources,
    *,
    no: str,
    title: str,
    amount_minor: int,
    currency: str,
    channel: str,
) -> None:
    """Create (or reuse a still-valid) BEpusdt transaction for ``no``. The caller has committed
    ``order.channel`` and holds NO row lock (network call)."""
    if channel not in USDT_CHANNELS:
        raise Unavailable("Payment channel is not available")
    if currency != "CNY" or amount_minor <= 0:
        raise Unavailable("Payment channel is not available for this order")
    now = int(time.time())
    cur = await load_attempt(res, no)
    if (
        cur
        and cur.get("channel") == channel
        and cur.get("amount_minor") == amount_minor
        and int(cur.get("expires_at", 0)) > now + REUSE_MARGIN_S
    ):
        return
    got = await res.redis.set(f"usdt:lock:{no}", b"1", nx=True, ex=LOCK_TTL_S)
    if not got:
        raise Unavailable("Payment is being prepared; please try again")
    try:
        att = await res.bepusdt.create_transaction(
            order_id=no,
            amount=Decimal(amount_minor) / 100,
            channel=channel,
            name=title,
            redirect_url=_redirect_url(res, no),
        )
    except BepusdtError:
        raise Unavailable("Could not create the USDT payment; please try again") from None
    finally:
        await res.redis.delete(f"usdt:lock:{no}")
    data = {
        "trade_id": att.trade_id,
        "channel": channel,
        "address": att.address,
        "actual_amount": format(att.actual_amount, "f"),
        "amount_minor": amount_minor,
        "currency": currency,
        "expires_at": now + att.expiration_s,
        "created_at": now,
    }
    await res.redis.set(attempt_key(no), json.dumps(data), ex=ATTEMPT_KEEP_S)


def _redirect_url(res: Resources, no: str) -> str:
    base = res.settings.public_base_url.rstrip("/")
    return f"{base}/orders/{no}" if not no.startswith("C") else f"{base}/orders"


def attempt_view(res: Resources, att: dict[str, Any], status: str) -> dict[str, Any]:
    """Buyer-facing view of an attempt (+ the order/checkout status the page polls)."""
    channel = str(att.get("channel"))
    left = max(0, int(att.get("expires_at", 0)) - int(time.time()))
    return {
        "channel": channel,
        "label": LABELS.get(channel, {}).get("en", channel),
        "network": NETWORKS.get(channel, ""),
        "address": att["address"],
        "usdt_amount": att["actual_amount"],
        "fiat_amount_minor": att["amount_minor"],
        "currency": att["currency"],
        "expires_at": att["expires_at"],
        "seconds_left": left,
        "expired": left == 0,
        "status": status,
        "qr_svg": qr_svg(str(att["address"])),
        "sim_url": _sim_url(res, str(att.get("trade_id", ""))),
    }


def _sim_url(res: Resources, trade_id: str) -> str | None:
    """Demo/dev only: link to the simulator's "pay" page. Never set in production."""
    base = res.settings.bepusdt_sim_url.rstrip("/")
    if not base or res.settings.is_prod or not trade_id:
        return None
    return f"{base}/{trade_id}"
