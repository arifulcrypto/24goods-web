"""ZPay notify pipeline (docs/architecture.md §7, AGENTS.md invariant 2).

signature (pinned MD5, constant time) -> pid / order / status / exact amount
-> Redis SET NX replay guard -> server-to-server re-query -> one DB transaction
(order FOR UPDATE, state machine, UNIQUE payment, ledger, outbox, chat message).
The notify itself is only a hint; ``return_url`` never fulfils.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import Checkout, Order
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
from app.payments import epay
from app.payments.zpay import ZPayQueryError

log = logging.getLogger("app.payments.notify")

CHECKOUT_PREFIX = "C"  # checkout_no = "C" + 12 base32; order_no = "G" + 12 base32
REPLAY_TTL_S = 24 * 3600
# In-flight lock: if the process dies mid-settle the lock lapses and ZPay's next retry
# (+15 s/+30 s/+180 s ...) is processed instead of being rejected for 24 h.
INFLIGHT_TTL_S = 60
SECURITY_REJECTS = frozenset({"bad_signature", "merchant_mismatch", "order_mismatch"})


def replay_key(params: Mapping[str, str]) -> str:
    canon = epay.canonical_string(params) + "&sign=" + str(params.get("sign", ""))
    return "notify:zpay:" + hashlib.sha256(canon.encode()).hexdigest()


AUDIT_SAMPLE_WINDOW_S = 60


async def _security_event(
    redis: Redis,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    action: str,
    ip: str | None,
    detail: dict,
) -> None:
    """Security audit, sampled: at most one row per (action, IP) per minute so a flood of
    forged notifies cannot grow audit_log without bound. Every event is still counted in
    Redis (``sec:count:<action>:<yyyymmdd>``) for monitoring/alerting."""
    await db.rollback()
    minute = int(time.time() // AUDIT_SAMPLE_WINDOW_S)
    day = time.strftime("%Y%m%d", time.gmtime())
    async with redis.pipeline(transaction=False) as pipe:
        pipe.incr(f"sec:sample:{action}:{ip or '-'}:{minute}")
        pipe.expire(f"sec:sample:{action}:{ip or '-'}:{minute}", AUDIT_SAMPLE_WINDOW_S * 2)
        pipe.incr(f"sec:count:{action}:{day}")
        pipe.expire(f"sec:count:{action}:{day}", 8 * 24 * 3600)
        in_minute, _, _, _ = await pipe.execute()
    if int(in_minute) > 1:
        return
    await audit(
        db,
        tenant_id=tenant_id,
        action=action,
        actor_role="gateway",
        ip=ip,
        detail=detail,
        severity="critical",
    )
    from app.modules.operations.service import record_event

    await record_event(
        db,
        tenant_id=tenant_id,
        environment="unknown",
        family="security",
        action=action,
        outcome="denied",
        reason="callback_rejected",
        ip=ip,
    )
    await db.commit()


async def handle_notify(
    db: AsyncSession, res: Resources, tenant_id: uuid.UUID, params: dict[str, str], ip: str | None
) -> NotifyOutcome:
    from app.modules.payments.gateways import routed_resources

    res, local_ref = await routed_resources(db, res, tenant_id, params.get("out_trade_no", ""))
    zpay = res.zpay
    if not zpay.config.key or not zpay.verify_signature(params):
        await _security_event(
            res.redis,
            db,
            tenant_id,
            "payment.notify_bad_signature",
            ip,
            {"out_trade_no": params.get("out_trade_no", "")[:32]},
        )
        return NotifyOutcome(FAIL)

    out_trade_no = params.get("out_trade_no", "")
    is_checkout = local_ref.startswith(CHECKOUT_PREFIX)
    order: Order | None = None
    checkout: Checkout | None = None
    if is_checkout:
        checkout = await db.scalar(
            select(Checkout).where(
                Checkout.checkout_no == local_ref, Checkout.tenant_id == tenant_id
            )
        )
        known = checkout is not None
    else:
        order = await db.scalar(
            select(Order).where(Order.order_no == local_ref, Order.tenant_id == tenant_id)
        )
        known = order is not None
    if not known:
        await _security_event(
            res.redis,
            db,
            tenant_id,
            "payment.notify_unknown_order",
            ip,
            {"out_trade_no": out_trade_no[:32]},
        )
        return NotifyOutcome(FAIL)

    if checkout is not None:
        target_id, target = checkout.id, f"checkout:{checkout.id}"
        expected = epay.ExpectedOrder(
            out_trade_no=out_trade_no,
            merchant_id=zpay.config.pid,
            amount=to_decimal(checkout.amount_minor),
        )
    else:
        assert order is not None
        target_id, target = order.id, f"order:{order.id}"
        expected = epay.ExpectedOrder(
            out_trade_no=out_trade_no,
            merchant_id=zpay.config.pid,
            amount=to_decimal(order.amount_minor),
        )
    try:
        trade_no = zpay.verify_notify(params, expected)
    except epay.NotifyRejected as rej:
        detail = {"reason": rej.reason, "params": safe_params(params)}
        if rej.reason in SECURITY_REJECTS:
            await _security_event(res.redis, db, tenant_id, "payment.notify_rejected", ip, detail)
            return NotifyOutcome(FAIL)
        if rej.reason == "not_paid":
            await audit(
                db,
                tenant_id=tenant_id,
                action="payment.notify_not_paid",
                actor_role="gateway",
                target=target,
                ip=ip,
                detail=detail,
            )
            await db.commit()
            return NotifyOutcome(SUCCESS, order)
        if rej.reason != "amount_mismatch" or not params.get("trade_no"):
            if checkout is not None:
                return await checkout_to_manual_review(db, target_id, rej.reason, ip, detail)
            return await to_manual_review(db, target_id, rej.reason, ip, detail)
        # Signature, merchant, order and status are fine; only the amount differs (typically the
        # buyer paid an old pay URL after switching channel). Do NOT drop it: the re-query below
        # must confirm the gateway really holds this payment, then it is RECORDED and held.
        trade_no = params["trade_no"]

    # Replay guard: identical notifies are processed at most once. The key starts as a
    # short in-flight lock and becomes a 24 h "done" marker only after settling.
    rkey = replay_key(params)
    first = await res.redis.set(rkey, b"inflight", nx=True, ex=INFLIGHT_TTL_S)
    if not first:
        await db.rollback()
        if is_checkout:
            current = await db.scalar(select(Checkout.status).where(Checkout.id == target_id))
        else:
            current = await db.scalar(select(Order.status).where(Order.id == target_id))
        # Acknowledge once settled; otherwise ask the gateway to retry later.
        return NotifyOutcome(
            SUCCESS if current in PAID_STATES | {"paid", "manual_review", "refunded"} else FAIL
        )

    try:
        if is_checkout:
            outcome = await _settle_checkout(db, res, target_id, trade_no, params, ip)
        else:
            outcome = await _settle(db, res, target_id, trade_no, params, ip)
        await res.redis.set(rkey, b"done", ex=REPLAY_TTL_S)
        return outcome
    except ZPayQueryError:
        await res.redis.delete(rkey)  # allow the gateway's retry to be processed
        await db.rollback()
        return NotifyOutcome(FAIL)
    except Exception:
        await res.redis.delete(rkey)
        raise


async def requery_mismatch(
    res: Resources, params: dict[str, str], trade_no: str, paid_amount: Decimal
) -> dict | None:
    """Server-to-server re-query. ``None`` when the gateway confirms exactly this payment,
    else the audit detail. Raises ZPayQueryError on transport/protocol failure. The caller
    must NOT hold row locks (network call)."""
    q = await res.zpay.query_order(params["out_trade_no"])
    if (
        not q.found
        or not q.paid
        or q.money != paid_amount
        or q.trade_no != trade_no
        or q.out_trade_no != params["out_trade_no"]
    ):
        return {
            "requery": {
                "found": q.found,
                "paid": q.paid,
                "money": str(q.money),
                "trade_no": q.trade_no[:64],
                "out_trade_no": q.out_trade_no[:32],
            },
            "params": safe_params(params),
        }
    return None


def _paid_amount(params: dict[str, str]) -> Decimal | None:
    try:
        return Decimal(params["money"])
    except (KeyError, InvalidOperation):  # pragma: no cover - verify_notify checked it
        return None


async def _settle(
    db: AsyncSession,
    res: Resources,
    order_id: uuid.UUID,
    trade_no: str,
    params: dict[str, str],
    ip: str | None,
) -> NotifyOutcome:
    paid_amount = _paid_amount(params)
    if paid_amount is None:  # pragma: no cover
        return await to_manual_review(db, order_id, "bad_amount", ip, {})
    await db.rollback()  # never hold row locks across the network call below
    bad = await requery_mismatch(res, params, trade_no, paid_amount)
    if bad is not None:
        return await to_manual_review(db, order_id, "requery_mismatch", ip, bad)
    recorded = (
        (res.zpay.config.instance_id + ":" + trade_no) if res.zpay.config.instance_id else trade_no
    )
    return await apply_order_payment(db, res.settings, order_id, recorded, paid_amount, params, ip)


async def _settle_checkout(
    db: AsyncSession,
    res: Resources,
    checkout_id: uuid.UUID,
    trade_no: str,
    params: dict[str, str],
    ip: str | None,
) -> NotifyOutcome:
    paid_amount = _paid_amount(params)
    if paid_amount is None:  # pragma: no cover
        return await checkout_to_manual_review(db, checkout_id, "bad_amount", ip, {})
    await db.rollback()
    bad = await requery_mismatch(res, params, trade_no, paid_amount)
    if bad is not None:
        return await checkout_to_manual_review(db, checkout_id, "requery_mismatch", ip, bad)
    return await apply_checkout_payment(
        db,
        res.settings,
        checkout_id,
        (res.zpay.config.instance_id + ":" + trade_no) if res.zpay.config.instance_id else trade_no,
        paid_amount,
        params,
        ip,
    )
