"""BEpusdt notify pipeline (docs/research/bepusdt.md, AGENTS.md invariant 2).

signature (pinned MD5, constant time) -> order_id maps to OUR order/checkout of this tenant
-> ``amount`` (fiat CNY) exact Decimal == order amount -> status == 2 (1 waiting / 3 expired:
reply ``ok``, nothing changes) -> Redis replay guard -> server-to-server re-query
``/api/v1/pay/info`` (status 2, same order_id, same money, trade_type == the channel the order
was paid with) -> the SAME locked settle pipeline as ZPay (state machine, UNIQUE(channel,
trade_id), ledger ``gateway_clearing:bepusdt``, outbox).

The notify body is only a hint. ``actual_amount`` / ``block_transaction_id`` are kept in
``payments.raw`` and the audit trail but are not trusted for money.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import Checkout, Order
from app.modules.payments.channels import is_usdt
from app.modules.payments.notify import (
    CHECKOUT_PREFIX,
    INFLIGHT_TTL_S,
    REPLAY_TTL_S,
    _security_event,
)
from app.modules.payments.settle import (
    FAIL,
    PAID_STATES,
    SUCCESS,
    NotifyOutcome,
    apply_checkout_payment,
    apply_order_payment,
    checkout_to_manual_review,
    safe_params,
    to_manual_review,
)
from app.payments import bepusdt
from app.payments.bepusdt import BepusdtError

log = logging.getLogger("app.payments.notify_bepusdt")

NOT_PAID_REPLY = "ok"  # status 1 / 3: acknowledged, no state change


def _str_params(params: dict[str, Any]) -> dict[str, str]:
    return {str(k)[:32]: str(v)[:256] for k, v in params.items() if v is not None}


def replay_key(params: dict[str, Any]) -> str:
    canon = bepusdt.canonical_string(params) + "&signature=" + str(params.get("signature", ""))
    return "notify:bepusdt:" + hashlib.sha256(canon.encode()).hexdigest()


async def handle_bepusdt_notify(
    db: AsyncSession,
    res: Resources,
    tenant_id: uuid.UUID,
    params: dict[str, Any],
    ip: str | None,
) -> NotifyOutcome:
    client = res.bepusdt
    order_id = params.get("order_id")
    order_id = order_id if isinstance(order_id, str) else ""

    if not client.configured or not client.verify_signature(params):
        await _security_event(
            res.redis,
            db,
            tenant_id,
            "payment.notify_bad_signature",
            ip,
            {"order_id": order_id[:32], "gateway": "bepusdt"},
        )
        return NotifyOutcome(FAIL)

    is_checkout = order_id.startswith(CHECKOUT_PREFIX)
    order: Order | None = None
    checkout: Checkout | None = None
    # the DB session is pinned to this tenant (RLS) AND filtered explicitly: another tenant's
    # order number is simply unknown here
    if is_checkout:
        checkout = await db.scalar(
            select(Checkout).where(
                Checkout.checkout_no == order_id, Checkout.tenant_id == tenant_id
            )
        )
        known = checkout is not None
    else:
        order = await db.scalar(
            select(Order).where(Order.order_no == order_id, Order.tenant_id == tenant_id)
        )
        known = order is not None
    if not known:
        await _security_event(
            res.redis,
            db,
            tenant_id,
            "payment.notify_unknown_order",
            ip,
            {"order_id": order_id[:32], "gateway": "bepusdt"},
        )
        return NotifyOutcome(FAIL)

    if checkout is not None:
        target_id = checkout.id
        amount_minor, order_channel = checkout.amount_minor, checkout.channel
    else:
        assert order is not None
        target_id = order.id
        amount_minor, order_channel = order.amount_minor, order.channel
    expected = to_decimal(amount_minor)
    detail = {"params": safe_params(_str_params(params)), "gateway": "bepusdt"}

    async def review(reason: str, extra: dict | None = None) -> NotifyOutcome:
        d = {"reason": reason, **detail, **(extra or {})}
        if is_checkout:
            return await checkout_to_manual_review(db, target_id, reason, ip, d)
        return await to_manual_review(db, target_id, reason, ip, d)

    status = params.get("status")
    if isinstance(status, bool) or not isinstance(status, int):
        return await review("bad_status")
    if status != bepusdt.STATUS_PAID:
        # waiting (every minute) / expired: nothing to settle and nothing to log per ping
        await db.rollback()
        return NotifyOutcome(NOT_PAID_REPLY, order)

    paid = bepusdt.decimal_of(params.get("amount"))
    if paid is None:
        return await review("bad_amount")
    if paid != expected:
        return await review("amount_mismatch", {"paid": str(paid)})
    trade_id = params.get("trade_id")
    if not isinstance(trade_id, str) or not trade_id:
        return await review("missing_trade_no")
    if not is_usdt(order_channel):
        # genuine signed notify for an order that was never opened on a USDT channel
        return await review("channel_mismatch")

    rkey = replay_key(params)
    first = await res.redis.set(rkey, b"inflight", nx=True, ex=INFLIGHT_TTL_S)
    if not first:
        await db.rollback()
        model_status = (
            select(Checkout.status).where(Checkout.id == target_id)
            if is_checkout
            else select(Order.status).where(Order.id == target_id)
        )
        current = await db.scalar(model_status)
        return NotifyOutcome(
            SUCCESS if current in PAID_STATES | {"paid", "manual_review", "refunded"} else FAIL
        )

    try:
        # never hold row locks across the network call below
        await db.rollback()
        try:
            info = await client.query_info(trade_id)
        except BepusdtError:
            await res.redis.delete(rkey)  # let the gateway's retry be processed
            return NotifyOutcome(FAIL)
        bad = _requery_mismatch(info, trade_id, order_id, expected, order_channel)
        if bad is not None:
            outcome = await review("requery_mismatch", {"requery": bad})
        else:
            raw = {**_str_params(params), "token": info.address}
            if is_checkout:
                outcome = await apply_checkout_payment(
                    db, res.settings, target_id, trade_id, expected, raw, ip, order_channel or ""
                )
            else:
                outcome = await apply_order_payment(
                    db, res.settings, target_id, trade_id, expected, raw, ip, order_channel or ""
                )
        await res.redis.set(rkey, b"done", ex=REPLAY_TTL_S)
        return outcome
    except Exception:
        await res.redis.delete(rkey)
        raise


def _requery_mismatch(
    info: bepusdt.TradeInfo,
    trade_id: str,
    order_id: str,
    expected: Decimal,
    order_channel: str | None,
) -> dict | None:
    """``None`` when the gateway confirms exactly this payment, else the audit detail."""
    ok = (
        info.found
        and info.status == bepusdt.STATUS_PAID
        and info.trade_id == trade_id
        and info.order_id == order_id
        and info.money is not None
        and info.money == expected
        and info.fiat == bepusdt.FIAT
        and bepusdt.TRADE_TYPE_TO_CHANNEL.get(info.trade_type) == order_channel
    )
    if ok:
        return None
    return {
        "found": info.found,
        "status": info.status,
        "trade_id": info.trade_id[:64],
        "order_id": info.order_id[:32],
        "money": str(info.money),
        "fiat": info.fiat[:8],
        "trade_type": info.trade_type[:32],
    }
