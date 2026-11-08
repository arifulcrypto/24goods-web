"""Bounded query-based recovery; never holds row locks during external I/O."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

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


async def requery_attempts(
    db: AsyncSession, res: Resources, tid: uuid.UUID, no: str
) -> tuple[NotifyOutcome | None, bool]:
    """Returns (settled outcome, has durable EPay history). Failure never implies unpaid."""
    refs = list(
        await db.scalars(
            select(PaymentAttempt.gateway_ref)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_not(None),
            )
            .order_by(PaymentAttempt.created_at)
            .limit(11)
        )
    )
    if len(refs) > 10:
        raise Unavailable("Too many attempts; staff reconciliation is required")
    outcomes = []
    failed = False
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
                (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
            )
            target_id = await db.scalar(select(model.id).where(col == no, model.tenant_id == tid))
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
            result = await apply(db, res.settings, target_id, trade, q.money, raw, None)
            att = await db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.gateway_ref == ref, PaymentAttempt.tenant_id == tid
                )
            )
            if att:
                att.status = "settled"
                att.data = {**att.data, "confirmed_trade_no": q.trade_no[:256]}
                att.checked_at = datetime.now(timezone.utc)
                await db.commit()
            outcomes.append(result)
    if failed:
        raise Unavailable("Some original payment queries are unavailable; cancellation is blocked")
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
                try:
                    outcome = await asyncio.wait_for(
                        requery_all(db, res, tid, no), timeout=min(10, remaining)
                    )
                    if outcome is not None:
                        from app.core import notify_tg
                        from app.modules.orders.service import broadcast_order

                        for order in outcome.orders:
                            await broadcast_order(
                                res,
                                db,
                                order,
                                [
                                    m
                                    for m in outcome.messages
                                    if m and m.conversation_id == order.conversation_id
                                ],
                            )
                        notify_tg.outcome_events(res, outcome.orders, outcome.dup_trade_no)
                        total += 1
                except (Unavailable, asyncio.TimeoutError):
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
    duplicate = None
    for result in outcomes:
        for obj in result.orders + ([result.order] if result.order is not None else []):
            order_ids.add(inspect(obj).identity[0])
        for obj in result.messages:
            if obj is not None:
                message_ids.add(inspect(obj).identity[0])
        duplicate = result.dup_trade_no or duplicate
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
    return NotifyOutcome("success", orders[0] if orders else None, messages, orders, duplicate)


async def requery_usdt_attempts(
    db: AsyncSession, res: Resources, tid: uuid.UUID, no: str
) -> tuple[NotifyOutcome | None, bool]:
    rows = list(
        await db.scalars(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_(None),
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
    outcomes = []
    unresolved = False
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
                (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
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
            outcomes.append(
                await apply(
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
            )
            att = await db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.id == ident, PaymentAttempt.tenant_id == tid
                )
            )
            att.status = "settled"
            att.checked_at = datetime.now(timezone.utc)
            await db.commit()
        elif info.status not in (bepusdt.STATUS_WAITING, bepusdt.STATUS_EXPIRED):
            unresolved = True
    if unresolved:
        raise Unavailable("Original USDT evidence is unresolved; cancellation is blocked")
    return await merge_outcomes(db, tid, outcomes), bool(rows)


async def requery_all(
    db: AsyncSession, res: Resources, tid: uuid.UUID, no: str
) -> NotifyOutcome | None:
    results = []
    failed = False
    for checker in (requery_attempts, requery_usdt_attempts):
        try:
            result, _ = await checker(db, res, tid, no)
            if result is not None:
                results.append(result)
        except Unavailable:
            await db.rollback()
            failed = True
    if failed:
        raise Unavailable("Some original payment evidence is unresolved")
    return await merge_outcomes(db, tid, results)
