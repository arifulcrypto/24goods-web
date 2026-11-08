"""Bounded query-based recovery; never holds row locks during external I/O."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import tenant_session
from app.core.errors import Unavailable
from app.core.resources import Resources
from app.models import Checkout, Order, PaymentAttempt, Tenant
from app.modules.payments.gateways import routed_resources
from app.modules.payments.settle import NotifyOutcome, apply_checkout_payment, apply_order_payment
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
    for ref in refs:
        routed, _ = await routed_resources(db, res, tid, ref)
        await db.rollback()
        try:
            q = await routed.zpay.query_order(ref)
        except ZPayQueryError:
            raise Unavailable("Payment query is unavailable; please retry") from None
        if q.found and q.paid:
            if q.out_trade_no != ref or not q.trade_no or q.money is None:
                raise Unavailable("Payment query returned inconsistent evidence")
            trade = routed.zpay.config.instance_id + ":" + q.trade_no
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
                att.checked_at = datetime.now(timezone.utc)
                await db.commit()
            return result, True
    return None, bool(refs)


async def run(res: Resources) -> int:
    """At most 20 invoice groups per tenant/tick, backed off per durable attempt."""
    async with tenant_session(res.maker, None) as db:
        tids = list(await db.scalars(select(Tenant.id)))
    total = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    for tid in tids:
        async with tenant_session(res.maker, tid) as db:
            refs = list(
                await db.scalars(
                    select(PaymentAttempt.local_ref)
                    .where(
                        PaymentAttempt.tenant_id == tid,
                        PaymentAttempt.provider_id.is_not(None),
                        PaymentAttempt.status.in_(("ready", "preparing", "unknown", "superseded")),
                        or_(
                            PaymentAttempt.checked_at.is_(None), PaymentAttempt.checked_at < cutoff
                        ),
                    )
                    .order_by(PaymentAttempt.created_at)
                    .limit(20)
                )
            )
            for no in dict.fromkeys(refs):
                try:
                    outcome, _ = await requery_attempts(db, res, tid, no)
                    if outcome is not None:
                        total += 1
                except Unavailable:
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
