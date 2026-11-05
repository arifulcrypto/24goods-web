"""Outbox consumer: ``order.paid`` -> claim stock (FOR UPDATE SKIP LOCKED) -> delivered."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import set_tenant, tenant_session
from app.core.money import fmt
from app.delivery.context import DeliveryContext
from app.models import CardStock, Delivery, Message, Order, Outbox
from app.modules.chat.messages import order_card_messages, post_system
from app.modules.common.views import message_view, order_view
from app.modules.orders.events import add_event, snapshot
from app.modules.realtime.publish import publish_message, publish_order

log = logging.getLogger("app.delivery.worker")

TOPIC = "order.paid"
MAX_ATTEMPTS = 8


@dataclass
class JobResult:
    order: Order | None = None
    messages: list[Message] = field(default_factory=list)
    cards: list[Message] = field(default_factory=list)  # order_card messages to re-publish


async def handle_order_paid(db: AsyncSession, order_id: uuid.UUID) -> JobResult:
    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None or order.status != "paid":
        return JobResult(order)  # already handled, refunded, or under review: never re-deliver
    result = JobResult(order)
    if order.delivery_mode == "manual":
        # The notify pipeline already posted `paid` {awaiting_delivery: true}; staff deliver.
        result.order = None  # nothing changed: nothing to publish
        return result

    card_ids: list[uuid.UUID] = []
    if order.product_id is not None:
        card_ids = list(
            await db.scalars(
                select(CardStock.id)
                .where(CardStock.product_id == order.product_id, CardStock.status == "available")
                .order_by(CardStock.created_at, CardStock.id)
                .limit(order.qty)
                .with_for_update(skip_locked=True)
            )
        )
    before = snapshot(order)
    if len(card_ids) < order.qty:
        order.status = "manual_review"
        order.version += 1
        await add_event(
            db, order, "paid", "manual_review", actor_kind="system", reason="out_of_stock"
        )
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="delivery.out_of_stock",
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            detail={"needed": order.qty, "available": len(card_ids)},
            severity="warning",
        )
        event = "manual_review"
    else:
        await db.execute(
            update(CardStock)
            .where(CardStock.id.in_(card_ids))
            .values(status="sold", order_id=order.id)
        )
        db.add_all(
            Delivery(tenant_id=order.tenant_id, order_id=order.id, card_stock_id=cid, kind="card")
            for cid in card_ids
        )
        order.status = "delivered"
        order.delivered_at = datetime.now(timezone.utc)
        order.version += 1
        await add_event(db, order, "paid", "delivered", actor_kind="system", reason="auto_card")
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="order.delivered",
            target=f"order:{order.id}",
            before=before,
            after=snapshot(order),
            detail={"mode": "auto_card", "cards": len(card_ids)},
        )
        event = "delivered"
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event=event,
        order_id=str(order.id),
    )
    result.messages = [msg] if msg else []
    result.cards = await order_card_messages(db, order.conversation_id, order.id)
    return result


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 2**attempts))


async def process_one(ctx: DeliveryContext) -> bool:
    """Process at most one outbox row. Returns False when the queue is empty."""
    async with tenant_session(ctx.maker, None) as db:
        row = await db.scalar(
            select(Outbox)
            .where(
                Outbox.processed_at.is_(None),
                Outbox.topic == TOPIC,
                Outbox.available_at <= func.now(),
            )
            .order_by(Outbox.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            await db.rollback()
            return False
        outbox_id, tenant_id = row.id, row.tenant_id
        await set_tenant(db, tenant_id)
        result = JobResult()
        try:
            async with db.begin_nested():
                result = await handle_order_paid(db, uuid.UUID(str(row.payload["order_id"])))
            failed: str | None = None
        except Exception as exc:  # job failure must not kill the loop; retried with backoff
            failed = type(exc).__name__
            log.error("outbox job failed id=%s error=%s", outbox_id, failed)
        row = await db.get(Outbox, outbox_id, populate_existing=True)
        assert row is not None
        now = datetime.now(timezone.utc)
        if failed is None:
            row.processed_at = now
        else:
            row.attempts += 1
            row.last_error = failed[:200]
            row.available_at = now + _backoff(row.attempts)
            if row.attempts >= MAX_ATTEMPTS:
                row.processed_at = now
                await audit(
                    db,
                    tenant_id=tenant_id,
                    action="delivery.job_dead",
                    target=f"outbox:{outbox_id}",
                    detail={"error": failed},
                    severity="critical",
                )
            result = JobResult()
        await db.commit()
    await _publish(ctx, result)
    return True


async def _publish(ctx: DeliveryContext, result: JobResult) -> None:
    order = result.order
    if order is None:
        return
    await publish_order(
        ctx.redis,
        order.tenant_id,
        order.buyer_id,
        order_view(order, viewer_is_owner=True),
        order_view(order, viewer_is_owner=False),
    )
    overlay = {"status": order.status, "amount": fmt(order.amount_minor)}
    for card in result.cards:  # same message id, fresh status (client replaces by id)
        view = message_view(card, sender_name=None, payload_overlay=overlay)
        await publish_message(ctx.redis, order.tenant_id, order.buyer_id, view)
    for msg in result.messages:
        await publish_message(
            ctx.redis, order.tenant_id, order.buyer_id, message_view(msg, sender_name=None)
        )


async def drain(ctx: DeliveryContext, limit: int = 1000) -> int:
    n = 0
    while n < limit and await process_one(ctx):
        n += 1
    return n


async def run_worker(ctx: DeliveryContext, stop: asyncio.Event) -> None:
    log.info("outbox consumer started")
    while not stop.is_set():
        try:
            await drain(ctx, limit=100)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # DB/Redis outage: keep polling
            log.error("outbox poll failed error=%s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=ctx.settings.outbox_poll_s)
        except asyncio.TimeoutError:
            pass
