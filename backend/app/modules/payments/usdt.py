"""USDT pay attempts (self-hosted BEpusdt, docs/review-v5.2.md s7).

An *attempt* is what BEpusdt returned for one order/checkout: trade_id, receiving address,
exact USDT amount and expiry. It is display state for our own pay page (and the trade_id the
cancel-time re-query uses), kept in Redis with a TTL; fulfilment NEVER trusts it: the notify
pipeline re-queries the gateway and matches order, channel, money and trade_id itself.
"""

from __future__ import annotations

import io
import json
import secrets
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import segno
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox
from app.core.audit import audit
from app.core.errors import InvalidState, Unavailable
from app.core.resources import Resources
from app.models import Checkout, Order, PaymentAttempt
from app.modules.payments.channels import LABELS, USDT_CHANNELS
from app.payments.bepusdt import BepusdtClient, BepusdtConfig, BepusdtError

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


async def load_attempt(
    res: Resources, no: str, *, db: AsyncSession | None = None, tid: uuid.UUID | None = None
) -> dict[str, Any] | None:
    if db is not None and tid is not None:
        row = await db.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_(None),
            )
            .order_by(PaymentAttempt.created_at.desc())
            .limit(1)
        )
        if row is not None:
            return dict(row.data) if row.status == "ready" else None
    return await _cached_attempt(res, no)


async def _cached_attempt(res: Resources, no: str) -> dict[str, Any] | None:
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
    db: AsyncSession,
    tid: uuid.UUID,
) -> None:
    """Create (or reuse a still-valid) BEpusdt transaction for ``no``. The caller has committed
    ``order.channel`` and holds NO row lock (network call)."""
    if channel not in USDT_CHANNELS:
        raise Unavailable("Payment channel is not available")
    if currency != "CNY" or amount_minor <= 0:
        raise Unavailable("Payment channel is not available for this order")
    now = int(time.time())
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": str(tid) + ":usdt:" + no},
    )
    prior = list(
        await db.scalars(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_(None),
            )
            .order_by(PaymentAttempt.created_at.desc())
            .limit(11)
            .with_for_update()
        )
    )
    if len(prior) > 10 or any(a.status in ("preparing", "unknown") for a in prior):
        raise Unavailable("Payment outcome is unknown; reconciliation is required")
    model, col = (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
    target = await db.scalar(
        select(model).where(col == no, model.tenant_id == tid).with_for_update()
    )
    if (
        target is None
        or target.status != "pending_payment"
        or target.channel != channel
        or target.amount_minor != amount_minor
    ):
        raise InvalidState("Order changed before payment reservation")
    version = str(target.updated_at)
    cur = prior[0] if prior else None
    if (
        cur
        and cur.status == "ready"
        and cur.channel == channel
        and cur.amount_minor == amount_minor
        and cur.expires_at
        and cur.expires_at.timestamp() > now + REUSE_MARGIN_S
    ):
        await db.commit()
        return
    for old in prior:
        if old.status == "ready":
            old.status = "superseded"
    row = PaymentAttempt(
        tenant_id=tid,
        local_ref=no,
        gateway_ref=no + "-u-" + secrets.token_hex(6),
        channel=channel,
        amount_minor=amount_minor,
        currency=currency,
        status="preparing",
        data={},
    )
    db.add(row)
    await db.flush()
    ref = row.id
    cfg = res.bepusdt.config
    row.data = {
        "provider_url": cfg.base_url,
        "provider_key": secretbox.seal(
            res.settings.settings_secret(), tid, "usdt:" + str(ref), cfg.token
        ).hex(),
        "provider_timeout": cfg.timeout_s,
    }
    await audit(
        db, tenant_id=tid, action="payment.usdt_reserved", target="payment_attempt:" + str(ref)
    )
    await db.commit()
    try:
        att = await res.bepusdt.create_transaction(
            order_id=no,
            amount=Decimal(amount_minor) / 100,
            channel=channel,
            name=title,
            redirect_url=_redirect_url(res, no),
        )
    except BepusdtError:
        row = await db.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == ref, PaymentAttempt.tenant_id == tid)
            .with_for_update()
        )
        row.status = "unknown"
        await audit(
            db,
            tenant_id=tid,
            action="payment.usdt_unknown",
            target="payment_attempt:" + str(ref),
            severity="warning",
        )
        await db.commit()
        raise Unavailable("USDT creation outcome is unknown; reconciliation is required") from None
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
    row = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == ref, PaymentAttempt.tenant_id == tid)
        .with_for_update()
    )
    target = await db.scalar(
        select(model)
        .where(col == no, model.tenant_id == tid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    changed = (
        target is None
        or target.status != "pending_payment"
        or target.channel != channel
        or target.amount_minor != amount_minor
        or str(target.updated_at) != version
    )
    row.data = {**row.data, **data}
    row.expires_at = datetime.fromtimestamp(data["expires_at"], timezone.utc)
    row.status = "superseded" if changed else "ready"
    await audit(
        db,
        tenant_id=tid,
        action="payment.usdt_prepared",
        target="payment_attempt:" + str(ref),
        detail={"changed": changed},
    )
    await db.commit()
    if changed:
        raise InvalidState("Order changed while payment was prepared")
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


def original_client(res: Resources, row: PaymentAttempt) -> BepusdtClient:
    data = row.data
    if not data.get("provider_key"):
        return res.bepusdt
    token = secretbox.unseal(
        res.settings.settings_secret(),
        row.tenant_id,
        "usdt:" + str(row.id),
        bytes.fromhex(data["provider_key"]),
    )
    if not token:
        raise Unavailable("Original USDT credentials unavailable")
    return BepusdtClient(
        BepusdtConfig(
            data["provider_url"],
            token,
            res.bepusdt.config.notify_url,
            data.get("provider_timeout", 600),
        ),
        res.bepusdt._http,
    )


async def routed_resources(
    db: AsyncSession, res: Resources, tid: uuid.UUID, params: dict[str, Any]
) -> Resources:
    no, trade = params.get("order_id"), params.get("trade_id")
    if not isinstance(no, str) or not isinstance(trade, str):
        return res
    row = await db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.tenant_id == tid,
            PaymentAttempt.local_ref == no,
            PaymentAttempt.provider_id.is_(None),
            PaymentAttempt.data["trade_id"].as_string() == trade,
        )
        .limit(1)
    )
    if row:
        return replace(res, bepusdt=original_client(res, row))
    unresolved = await db.scalars(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.tenant_id == tid,
            PaymentAttempt.local_ref == no,
            PaymentAttempt.provider_id.is_(None),
            PaymentAttempt.status.in_(("preparing", "unknown")),
        )
        .limit(10)
    )
    for candidate in unresolved:
        client = original_client(res, candidate)
        if client.verify_signature(params):
            return replace(res, bepusdt=client)
    return res
