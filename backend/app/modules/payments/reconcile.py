"""Bounded query-based recovery; never holds row locks during external I/O."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import tenant_session
from app.core.errors import Unavailable
from app.core.resources import Resources
from app.models import Checkout, Message, Order, PaymentAttempt, Tenant
from app.modules.payments import usdt
from app.modules.payments.gateways import routed_resources
from app.modules.payments.settle import NotifyOutcome, apply_checkout_payment, apply_order_payment
from app.payments import bepusdt, epay
from app.payments.zpay import ZPayQueryError

log = logging.getLogger(__name__)


class UnresolvedEvidence(Unavailable):
    """Some original payment evidence could not be resolved.

    ``settled`` carries the outcomes this pass already committed durably, so a caller can still
    broadcast them and raise their staff alerts while failing the request itself closed.
    """

    def __init__(self, message: str = "", settled: NotifyOutcome | None = None) -> None:
        super().__init__(message)
        self.settled = settled


async def _carrying(
    db: AsyncSession, tid: uuid.UUID, exc: Exception, outcomes: list[NotifyOutcome]
) -> Exception:
    """Re-shape ANY failure so it carries whatever this pass already committed.

    Not only Unavailable: an IntegrityError, a Decimal error or a dropped connection raised after
    a settlement committed used to escape uncarried, and since that attempt is already 'settled'
    no later tick revisits it, so its broadcast and duplicate-payment alert were lost for good.
    An unexpected error becomes UnresolvedEvidence (503, fail closed), logged with its traceback.
    """
    if not outcomes:
        return exc
    if not isinstance(exc, Unavailable):
        log.error("reconciliation failed after a committed settlement", exc_info=exc)
    try:
        await db.rollback()  # committed work stays committed; this clears the aborted statement
        merged = await merge_outcomes(db, tid, outcomes)
    except Exception:
        # The session itself is unusable (e.g. the connection died). Nothing more can be done
        # here; keep the original error rather than masking it with this one.
        log.error("could not carry committed settlements after %s", type(exc).__name__)
        return exc
    message = (
        exc.message
        if isinstance(exc, Unavailable)
        else "Payment evidence could not be fully resolved"
    )
    return UnresolvedEvidence(message, merged)


async def emit_outcome(res: Resources, db: AsyncSession, outcome: NotifyOutcome) -> list[Order]:
    """Broadcast a committed settlement and raise its staff alerts (duplicate trade included)."""
    from app.core import notify_tg
    from app.modules.orders.service import broadcast_order

    touched = outcome.orders or ([outcome.order] if outcome.order is not None else [])
    for order in touched:
        await broadcast_order(
            res,
            db,
            order,
            [m for m in outcome.messages if m and m.conversation_id == order.conversation_id],
        )
    notify_tg.outcome_events(res, touched, outcome.dup_trade_nos or outcome.dup_trade_no)
    return touched


async def _mark_settled(
    db: AsyncSession, tid: uuid.UUID, where: Any, trade_no: str | None = None
) -> None:
    """Mark one original attempt settled, from a FRESH row.

    A fresh select matters: apply_* may roll back, which expires every instance, and touching an
    expired attribute on an async session triggers implicit IO (MissingGreenlet)."""
    att = await db.scalar(select(PaymentAttempt).where(where, PaymentAttempt.tenant_id == tid))
    if att is None:
        return
    att.status = "settled"
    if trade_no:
        att.data = {**att.data, "confirmed_trade_no": trade_no[:256]}
    att.checked_at = datetime.now(timezone.utc)


async def requery_attempts(
    db: AsyncSession,
    res: Resources,
    tid: uuid.UUID,
    no: str,
    sink: list[NotifyOutcome] | None = None,
) -> tuple[NotifyOutcome | None, bool]:
    """Returns (settled outcome, has durable EPay history). Failure never implies unpaid.

    ``sink``, when given, receives each outcome the moment it commits, so a caller that cancels
    this coroutine (asyncio.wait_for) still holds what was already settled (#33)."""
    refs = list(
        await db.scalars(
            select(PaymentAttempt.gateway_ref)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_not(None),
                # An already-settled original is decided: re-querying it on every retry only
                # replays the duplicate-payment audit trail and re-broadcasts the same order.
                PaymentAttempt.status != "settled",
            )
            .order_by(PaymentAttempt.created_at)
            .limit(11)
        )
    )
    if len(refs) > 10:
        raise Unavailable("Too many attempts; staff reconciliation is required")
    outcomes: list[NotifyOutcome] = []
    failed = False
    # Any failure raised after a settlement has committed must still hand that outcome back,
    # otherwise its broadcast and duplicate-payment alert are lost for good: the attempt is
    # already 'settled', so no later tick revisits it.
    try:
        for ref in refs:
            routed, _ = await routed_resources(db, res, tid, ref)
            await db.rollback()
            try:
                q = await routed.zpay.query_order(ref)
            except ZPayQueryError:
                failed = True
                continue
            if q.found and q.paid:
                if q.out_trade_no != ref or not q.trade_no or q.money is None:
                    raise Unavailable("Payment query returned inconsistent evidence")
                trade = epay.gateway_trade_key(routed.zpay.config.instance_id, q.trade_no)
                model, col = (
                    (Checkout, Checkout.checkout_no)
                    if no.startswith("C")
                    else (Order, Order.order_no)
                )
                target_id = await db.scalar(
                    select(model.id).where(col == no, model.tenant_id == tid)
                )
                if target_id is None:
                    raise Unavailable("Payment target unavailable")
                raw = {
                    "out_trade_no": ref,
                    "trade_no": q.trade_no,
                    "money": str(q.money),
                    "provider_id": routed.zpay.config.instance_id,
                    "via": "reconciliation",
                }
                apply = apply_checkout_payment if no.startswith("C") else apply_order_payment
                where = PaymentAttempt.gateway_ref == ref
                # Mark first so the mark rides in apply's own commit. Marking only afterwards left
                # a window in which a crash or timeout kept the attempt 'ready', and the next tick
                # re-applied it, replaying the duplicate-payment audit row and staff alert (#33).
                await _mark_settled(db, tid, where, q.trade_no)
                result = await apply(db, res.settings, target_id, trade, q.money, raw, None)
                outcomes.append(result)
                if sink is not None:
                    sink.append(result)
                # apply rolls back -- and still returns -- on a no-op replay, discarding the early
                # mark; re-mark from a fresh row so that path behaves exactly as it always did.
                await _mark_settled(db, tid, where, q.trade_no)
                await db.commit()
    except Exception as exc:
        carried = await _carrying(db, tid, exc, outcomes)
        if carried is exc:
            raise
        raise carried from exc
    if failed:
        raise UnresolvedEvidence(
            "Some original payment queries are unavailable; cancellation is blocked",
            await merge_outcomes(db, tid, outcomes),
        )
    return await merge_outcomes(db, tid, outcomes), bool(refs)


async def run(res: Resources) -> int:
    """At most 20 invoice groups per tenant/tick, backed off per durable attempt."""
    async with tenant_session(res.maker, None) as db:
        tids = list(await db.scalars(select(Tenant.id)))
    total = 0
    started = time.monotonic()
    if tids:
        offset = int(await res.redis.incr("sec:reconcile-tenant")) % len(tids)
        tids = tids[offset:] + tids[:offset]
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    for tid in tids:
        if time.monotonic() - started >= 20:
            break
        async with tenant_session(res.maker, tid) as db:
            refs = list(
                await db.scalars(
                    select(PaymentAttempt.local_ref)
                    .where(
                        PaymentAttempt.tenant_id == tid,
                        PaymentAttempt.status.in_(("ready", "preparing", "unknown", "superseded")),
                        or_(
                            PaymentAttempt.checked_at.is_(None), PaymentAttempt.checked_at < cutoff
                        ),
                    )
                    .order_by(
                        PaymentAttempt.checked_at.asc().nullsfirst(), PaymentAttempt.created_at
                    )
                    .limit(8)
                )
            )
            for no in dict.fromkeys(refs):
                remaining = 20 - (time.monotonic() - started)
                if remaining <= 0:
                    break
                # Collects every outcome the moment it commits. wait_for CANCELS the coroutine on
                # timeout, so nothing it was about to return survives -- only what is already in
                # here. Without it a timeout lost the broadcast and duplicate-payment alert of a
                # settlement that had committed, permanently, since settled attempts are skipped.
                sink: list[NotifyOutcome] = []
                outcome: NotifyOutcome | None = None
                try:
                    outcome = await asyncio.wait_for(
                        requery_all(db, res, tid, no, sink), timeout=min(10, remaining)
                    )
                except Exception as exc:
                    # Any exception: a non-Unavailable one used to escape and abort the tick for
                    # every remaining tenant. Expected ones are routine; others get a traceback.
                    if not isinstance(exc, (Unavailable, asyncio.TimeoutError)):
                        log.error("reconciliation failed for %s", no, exc_info=exc)
                    await db.rollback()
                    if sink:
                        try:
                            outcome = await merge_outcomes(db, tid, sink)
                        except Exception:
                            log.warning("committed settlement could not be reloaded for %s", no)
                            await db.rollback()
                if outcome is not None:
                    # best effort: the bookkeeping below must still run for this ref
                    try:
                        await emit_outcome(res, db, outcome)
                        total += 1
                    except Exception:
                        log.warning("committed settlement broadcast skipped for %s", no)
                        await db.rollback()
                rows = await db.scalars(
                    select(PaymentAttempt).where(
                        PaymentAttempt.tenant_id == tid, PaymentAttempt.local_ref == no
                    )
                )
                for row in rows:
                    row.checked_at = datetime.now(timezone.utc)
                    row.attempts += 1
                await db.commit()
    return total


async def merge_outcomes(
    db: AsyncSession, tid: uuid.UUID, outcomes: list[NotifyOutcome]
) -> NotifyOutcome | None:
    if not outcomes:
        return None
    order_ids, message_ids = set(), set()
    # Every distinct duplicate is a separate refund owed: keeping only the last one silently
    # dropped the staff alert for the others when three or more originals were paid.
    duplicates: dict[str, None] = {}
    for result in outcomes:
        for obj in result.orders + ([result.order] if result.order is not None else []):
            order_ids.add(inspect(obj).identity[0])
        for obj in result.messages:
            if obj is not None:
                message_ids.add(inspect(obj).identity[0])
        for dup in (*result.dup_trade_nos, result.dup_trade_no):
            if dup:
                duplicates[dup] = None
    orders = list(
        await db.scalars(
            select(Order)
            .where(Order.tenant_id == tid, Order.id.in_(order_ids))
            .execution_options(populate_existing=True)
        )
    )
    messages = (
        list(
            await db.scalars(
                select(Message)
                .where(Message.tenant_id == tid, Message.id.in_(message_ids))
                .execution_options(populate_existing=True)
            )
        )
        if message_ids
        else []
    )
    dups = tuple(duplicates)
    return NotifyOutcome(
        "success",
        orders[0] if orders else None,
        messages,
        orders,
        dups[0] if dups else None,
        dups,
    )


async def requery_usdt_attempts(
    db: AsyncSession,
    res: Resources,
    tid: uuid.UUID,
    no: str,
    sink: list[NotifyOutcome] | None = None,
) -> tuple[NotifyOutcome | None, bool]:
    rows = list(
        await db.scalars(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_(None),
                PaymentAttempt.status != "settled",
            )
            .order_by(PaymentAttempt.checked_at.asc().nullsfirst(), PaymentAttempt.created_at)
            .limit(11)
        )
    )
    if len(rows) > 10:
        raise Unavailable("Too many USDT attempts; manual reconciliation is required")
    # Capture immutable values before any network/settlement rollback expires ORM rows.
    snapshots = [
        (row.id, dict(row.data), row.channel, usdt.original_client(res, row)) for row in rows
    ]
    outcomes: list[NotifyOutcome] = []
    unresolved = False
    try:
        for ident, data, channel, client in snapshots:
            if not data.get("trade_id"):
                unresolved = True
                continue
            await db.rollback()
            try:
                info = await client.query_info(data["trade_id"])
            except bepusdt.BepusdtError:
                unresolved = True
                continue
            if not info.found:
                # A known issued trade disappearing is not evidence that no transfer occurred.
                unresolved = True
                continue
            if (
                info.trade_id != data["trade_id"]
                or info.order_id != no
                or info.fiat != bepusdt.FIAT
                or info.money is None
                or bepusdt.TRADE_TYPE_TO_CHANNEL.get(info.trade_type) != channel
            ):
                unresolved = True
                continue
            if info.status == bepusdt.STATUS_PAID:
                model, col = (
                    (Checkout, Checkout.checkout_no)
                    if no.startswith("C")
                    else (Order, Order.order_no)
                )
                target = await db.scalar(select(model).where(col == no, model.tenant_id == tid))
                target_id = target.id if target else None
                if target_id is None:
                    raise Unavailable("Payment target unavailable")
                hold = "channel_mismatch" if target.channel not in (None, channel) else None
                if info.address != data.get("address") or info.actual_amount != bepusdt.decimal_of(
                    data.get("actual_amount")
                ):
                    hold = "receiver_mismatch"
                raw = {
                    "order_id": no,
                    "trade_id": info.trade_id,
                    "amount": str(info.money),
                    "token": info.address,
                    "via": "original_attempt_requery",
                }
                apply = apply_checkout_payment if no.startswith("C") else apply_order_payment
                where = PaymentAttempt.id == ident
                await _mark_settled(db, tid, where)  # rides in apply's commit; see EPay checker
                result = await apply(
                    db,
                    res.settings,
                    target_id,
                    info.trade_id,
                    info.money,
                    raw,
                    None,
                    channel,
                    hold=hold,
                )
                outcomes.append(result)
                if sink is not None:
                    sink.append(result)
                await _mark_settled(db, tid, where)
                await db.commit()
            elif info.status not in (bepusdt.STATUS_WAITING, bepusdt.STATUS_EXPIRED):
                unresolved = True
    except Exception as exc:
        carried = await _carrying(db, tid, exc, outcomes)
        if carried is exc:
            raise
        raise carried from exc
    if unresolved:
        raise UnresolvedEvidence(
            "Original USDT evidence is unresolved; cancellation is blocked",
            await merge_outcomes(db, tid, outcomes),
        )
    return await merge_outcomes(db, tid, outcomes), bool(rows)


async def requery_all(
    db: AsyncSession,
    res: Resources,
    tid: uuid.UUID,
    no: str,
    sink: list[NotifyOutcome] | None = None,
) -> NotifyOutcome | None:
    results = []
    failed = False
    for checker in (requery_attempts, requery_usdt_attempts):
        try:
            result, _ = await checker(db, res, tid, no, sink)
            if result is not None:
                results.append(result)
        except Exception as exc:
            # Any failure, not only Unavailable: an unexpected error in the EPay checker used to
            # skip the USDT checker entirely and drop what the EPay one had committed.
            if not isinstance(exc, Unavailable):
                log.error("payment re-query failed for %s", no, exc_info=exc)
            # A settlement one checker already committed survives another's unresolved evidence.
            settled = getattr(exc, "settled", None)
            if settled is not None:
                results.append(settled)
            await db.rollback()
            failed = True
    if failed:
        raise UnresolvedEvidence(
            "Some original payment evidence is unresolved", await merge_outcomes(db, tid, results)
        )
    return await merge_outcomes(db, tid, results)
