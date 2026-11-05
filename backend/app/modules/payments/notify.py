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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.money import percent_of, to_decimal
from app.core.resources import Resources
from app.models import Message, Order, Outbox, Payment, Product
from app.modules.chat.messages import post_system
from app.modules.orders.events import add_event, snapshot
from app.modules.payments.ledger import Line, post_txn
from app.payments import epay
from app.payments.zpay import CHANNEL, ZPayQueryError

log = logging.getLogger("app.payments.notify")

SUCCESS, FAIL = "success", "fail"
REPLAY_TTL_S = 24 * 3600
# In-flight lock: if the process dies mid-settle the lock lapses and ZPay's next retry
# (+15 s/+30 s/+180 s ...) is processed instead of being rejected for 24 h.
INFLIGHT_TTL_S = 60
PAID_STATES = frozenset({"paid", "delivered", "completed"})
SECURITY_REJECTS = frozenset({"bad_signature", "merchant_mismatch", "order_mismatch"})


@dataclass
class NotifyOutcome:
    reply: str
    order: Order | None = None
    messages: list[Message | None] = field(default_factory=list)


def replay_key(params: Mapping[str, str]) -> str:
    canon = epay.canonical_string(params) + "&sign=" + str(params.get("sign", ""))
    return "notify:zpay:" + hashlib.sha256(canon.encode()).hexdigest()


def safe_params(params: Mapping[str, str]) -> dict[str, str]:
    """Notify params as stored for audit (no secrets in them, but bound the size)."""
    return {str(k)[:32]: str(v)[:256] for k, v in list(params.items())[:30]}


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
    await db.commit()


async def _to_manual_review(
    db: AsyncSession, order_id: uuid.UUID, reason: str, ip: str | None, detail: dict
) -> NotifyOutcome:
    await db.rollback()
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None
    msgs: list[Message | None] = []
    if order.status in ("pending_payment", "expired", "cancelled"):
        before = snapshot(order)
        prev = order.status
        order.status = "manual_review"
        order.version += 1
        await add_event(db, order, prev, "manual_review", actor_kind="gateway", reason=reason)
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.manual_review",
            actor_role="gateway",
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="manual_review",
                order_id=str(order.id),
            )
        )
    else:
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.anomaly",
            actor_role="gateway",
            target=f"order:{order.id}",
            ip=ip,
            detail={"reason": reason, **detail},
            severity="warning",
        )
    await db.commit()
    return NotifyOutcome(SUCCESS, order, msgs)


async def handle_notify(
    db: AsyncSession, res: Resources, tenant_id: uuid.UUID, params: dict[str, str], ip: str | None
) -> NotifyOutcome:
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

    order_no = params.get("out_trade_no", "")
    order = await db.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        await _security_event(
            res.redis,
            db,
            tenant_id,
            "payment.notify_unknown_order",
            ip,
            {"out_trade_no": order_no[:32]},
        )
        return NotifyOutcome(FAIL)

    expected = epay.ExpectedOrder(
        out_trade_no=order.order_no,
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
                target=f"order:{order.id}",
                ip=ip,
                detail=detail,
            )
            await db.commit()
            return NotifyOutcome(SUCCESS, order)
        return await _to_manual_review(db, order.id, rej.reason, ip, detail)

    # Replay guard: identical notifies are processed at most once. The key starts as a
    # short in-flight lock and becomes a 24 h "done" marker only after settling.
    rkey = replay_key(params)
    first = await res.redis.set(rkey, b"inflight", nx=True, ex=INFLIGHT_TTL_S)
    if not first:
        order_id = order.id
        await db.rollback()
        current = await db.scalar(select(Order.status).where(Order.id == order_id))
        # Acknowledge once the order is settled; otherwise ask the gateway to retry later.
        return NotifyOutcome(
            SUCCESS if current in PAID_STATES | {"manual_review", "refunded"} else FAIL
        )

    try:
        outcome = await _settle(db, res, order.id, trade_no, params, ip)
        await res.redis.set(rkey, b"done", ex=REPLAY_TTL_S)
        return outcome
    except ZPayQueryError:
        await res.redis.delete(rkey)  # allow the gateway's retry to be processed
        await db.rollback()
        return NotifyOutcome(FAIL)
    except Exception:
        await res.redis.delete(rkey)
        raise


async def _settle(
    db: AsyncSession,
    res: Resources,
    order_id: uuid.UUID,
    trade_no: str,
    params: dict[str, str],
    ip: str | None,
) -> NotifyOutcome:
    try:
        paid_amount = Decimal(params["money"])
    except (KeyError, InvalidOperation):  # pragma: no cover - verify_notify checked it
        return await _to_manual_review(db, order_id, "bad_amount", ip, {})
    await db.rollback()  # never hold row locks across the network call below
    q = await res.zpay.query_order(params["out_trade_no"])
    if (
        not q.found
        or not q.paid
        or q.money != paid_amount
        or q.trade_no != trade_no
        or q.out_trade_no != params["out_trade_no"]
    ):
        return await _to_manual_review(
            db,
            order_id,
            "requery_mismatch",
            ip,
            {
                "requery": {
                    "found": q.found,
                    "paid": q.paid,
                    "money": str(q.money),
                    "trade_no": q.trade_no[:64],
                    "out_trade_no": q.out_trade_no[:32],
                },
                "params": safe_params(params),
            },
        )

    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None
    amount_minor = int(paid_amount * 100)

    if order.status in PAID_STATES or order.status == "manual_review":
        existing = await db.scalar(select(Payment).where(Payment.order_id == order.id))
        if existing is None or existing.gateway_trade_no != trade_no:
            await audit(
                db,
                tenant_id=order.tenant_id,
                action="payment.duplicate",
                actor_role="gateway",
                target=f"order:{order.id}",
                ip=ip,
                detail={"trade_no": trade_no[:64]},
                severity="warning",
            )
            await db.commit()
        else:
            await db.rollback()
        return NotifyOutcome(SUCCESS, order)

    # Late payments (order expired/cancelled, however late) are still RECORDED
    # (Payment + ledger) and held in manual_review: received money must never vanish.
    late = order.status in ("expired", "cancelled")
    if order.status == "refunded":
        existing = await db.scalar(select(Payment).where(Payment.order_id == order.id))
        if existing is not None and existing.gateway_trade_no == trade_no:
            await db.rollback()  # benign duplicate of the notify that was refunded later
            return NotifyOutcome(SUCCESS, order)
    if order.status not in ("pending_payment", "expired", "cancelled"):
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="payment.unexpected_state",
            actor_role="gateway",
            target=f"order:{order.id}",
            ip=ip,
            detail={"trade_no": trade_no[:64], "status": order.status},
            severity="critical",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS, order)
    if amount_minor != order.amount_minor:  # price changed after the pay URL was issued
        return await _to_manual_review(
            db, order.id, "amount_changed", ip, {"paid_minor": amount_minor}
        )

    db.add(
        Payment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            channel=CHANNEL,
            gateway_trade_no=trade_no[:64],
            amount_minor=amount_minor,
            currency=order.currency,
            raw=safe_params({k: v for k, v in params.items() if k != "sign"}),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(channel, trade_no) / UNIQUE(order_id): someone else settled it first.
        await db.rollback()
        return NotifyOutcome(SUCCESS)

    before = snapshot(order)
    prev = order.status
    order.status = "manual_review" if late else "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.channel = CHANNEL
    order.version += 1
    await add_event(
        db,
        order,
        prev,
        order.status,
        actor_kind="gateway",
        reason="late_payment" if late else f"zpay:{trade_no[:40]}",
    )
    await post_txn(
        db,
        tenant_id=order.tenant_id,
        currency=order.currency,
        order_id=order.id,
        memo=f"zpay payment {order.order_no}",
        lines=[Line("gateway_clearing:zpay", amount_minor), Line("sales_revenue", -amount_minor)],
    )
    fee = percent_of(amount_minor, res.settings.zpay_fee_permille)
    if fee > 0:
        await post_txn(
            db,
            tenant_id=order.tenant_id,
            currency=order.currency,
            order_id=order.id,
            memo=f"zpay fee {order.order_no}",
            lines=[Line("fees:zpay", fee), Line("gateway_clearing:zpay", -fee)],
        )
    msgs: list[Message | None] = []
    if late:
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="manual_review",
                order_id=str(order.id),
            )
        )
    else:
        db.add(
            Outbox(
                tenant_id=order.tenant_id,
                topic="order.paid",
                key=str(order.id),
                payload={"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
            )
        )
        if order.product_id:
            await db.execute(
                update(Product)
                .where(Product.id == order.product_id)
                .values(sold=Product.sold + order.qty)
            )
        msgs.append(
            await post_system(
                db,
                tenant_id=order.tenant_id,
                conversation_id=order.conversation_id,
                event="paid",
                text=(
                    "Payment received. An agent will deliver your order shortly."
                    if order.delivery_mode == "manual"
                    else None
                ),
                order_id=str(order.id),
                awaiting_delivery=order.delivery_mode == "manual",
            )
        )
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="payment.settled",
        actor_role="gateway",
        target=f"order:{order.id}",
        before=before,
        after=snapshot(order),
        ip=ip,
        detail={"trade_no": trade_no[:64], "late": late},
    )
    await db.commit()
    return NotifyOutcome(SUCCESS, order, msgs)
