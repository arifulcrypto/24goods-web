"""Outbox consumer: ``order.paid`` -> claim stock (FOR UPDATE SKIP LOCKED) -> delivered."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.db import set_tenant, tenant_session
from app.core.money import fmt
from app.core.scoped import get_scoped
from app.delivery.context import DeliveryContext
from app.models import (
    AfterSales,
    Attachment,
    CardStock,
    Delivery,
    Message,
    Order,
    Outbox,
    Product,
    Tenant,
    User,
)
from app.modules.chat.messages import order_card_messages, post_system
from app.modules.common.orderviews import order_views
from app.modules.common.sitedefaults import (
    AFTER_SALES_REOPEN_EXTENSION,
    delivery_deadlines,
    order_terms,
)
from app.modules.common.views import after_sales_view, message_view
from app.modules.orders.events import add_event, snapshot
from app.modules.realtime.publish import publish_after_sales, publish_message, publish_order

log = logging.getLogger("app.delivery.worker")

TOPIC = "order.paid"
REPLACE_TOPIC = "delivery.replace"
TOPICS = (TOPIC, REPLACE_TOPIC)
MAX_ATTEMPTS = 8


@dataclass
class JobResult:
    order: Order | None = None
    messages: list[Message] = field(default_factory=list)
    cards: list[Message] = field(default_factory=list)  # order_card messages to re-publish
    ticket: AfterSales | None = None


def _variant(order: Order) -> tuple:  # type: ignore[type-arg]
    """A variant order claims only that variant's cards; a variant-less order only product-level
    cards (disjoint pools, v11 audit M1 — the same rule as the stock checks at order time)."""
    if order.variant_id is not None:
        return (CardStock.variant_id == order.variant_id,)
    return (CardStock.variant_id.is_(None),)


def _claimable():  # type: ignore[no-untyped-def]
    """Stock a buyer can still be delivered: available and not expired."""
    return (
        CardStock.status == "available",
        or_(CardStock.expires_at.is_(None), CardStock.expires_at > func.now()),
    )


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
                .where(CardStock.product_id == order.product_id, *_claimable(), *_variant(order))
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
        now = datetime.now(timezone.utc)
        product = (
            await get_scoped(db, Product, order.product_id, order.tenant_id)
            if order.product_id
            else None
        )
        # Tenant has no tenant_id: a primary-key lookup is the scoped lookup
        tenant = await db.get(Tenant, order.tenant_id)  # nosemgrep: tenant-scoped-get
        t_hours, t_months = order_terms(order, product)  # purchase-time snapshot (v8 F)
        order.after_sales_until, order.auto_confirm_at = delivery_deadlines(
            now,
            mode="auto_card",
            product_hours=t_hours,
            product_months=t_months,
            tenant_settings=tenant.settings if tenant else None,
        )
        order.status = "delivered"
        order.delivered_at = now
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


async def handle_replace(db: AsyncSession, payload: dict) -> JobResult:
    """after-sales ``replace`` on an auto_card order: void the reported card(s) with reason
    ``defective``, claim fresh stock for the SAME order, deliver, close the ticket."""
    order = await db.scalar(
        select(Order).where(Order.id == uuid.UUID(str(payload["order_id"]))).with_for_update()
    )
    ticket = await db.scalar(
        select(AfterSales)
        .where(AfterSales.id == uuid.UUID(str(payload["after_sales_id"])))
        .with_for_update()
    )
    if (
        order is None
        or ticket is None
        or ticket.order_id != order.id
        or ticket.status != "processing"  # already handled, or staff closed it meanwhile
        or order.delivery_mode != "auto_card"
        or order.status not in ("delivered", "completed")
    ):
        return JobResult()
    wanted = {uuid.UUID(str(c)) for c in payload.get("card_ids") or []}
    stmt = (
        select(CardStock.id)
        .join(Delivery, Delivery.card_stock_id == CardStock.id)
        .where(Delivery.order_id == order.id, CardStock.order_id == order.id)
        .where(CardStock.status != "void")
        .order_by(Delivery.created_at, CardStock.id)
        .with_for_update(of=CardStock)
    )
    old_ids = [cid for cid in await db.scalars(stmt) if not wanted or cid in wanted]
    result = JobResult(order, ticket=ticket)
    actor_id = uuid.UUID(str(payload["actor_id"])) if payload.get("actor_id") else None
    actor_role = str(payload.get("actor_role") or "system")
    if not old_ids:  # nothing replaceable (all void already, or ids don't match): tell staff
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="delivery.replace_no_cards",
            actor_id=actor_id,
            actor_role=actor_role,
            target=f"after_sales:{ticket.id}",
            detail={"order_id": str(order.id)},
            severity="warning",
        )
        msg = await post_system(
            db,
            tenant_id=order.tenant_id,
            conversation_id=order.conversation_id,
            event="after_sales_replace_failed",
            order_id=str(order.id),
            after_sales_id=str(ticket.id),
        )
        result.messages = [msg] if msg else []
        return result
    new_ids = list(
        await db.scalars(
            select(CardStock.id)
            .where(CardStock.product_id == order.product_id, *_claimable(), *_variant(order))
            .order_by(CardStock.created_at, CardStock.id)
            .limit(len(old_ids))
            .with_for_update(skip_locked=True)
        )
    )
    if len(new_ids) < len(old_ids):  # not enough stock: leave the ticket open for staff
        await audit(
            db,
            tenant_id=order.tenant_id,
            action="delivery.replace_out_of_stock",
            target=f"order:{order.id}",
            detail={"needed": len(old_ids), "available": len(new_ids)},
            severity="warning",
        )
        msg = await post_system(
            db,
            tenant_id=order.tenant_id,
            conversation_id=order.conversation_id,
            event="after_sales_replace_failed",
            order_id=str(order.id),
            after_sales_id=str(ticket.id),
        )
        result.messages = [msg] if msg else []
        return result
    now = datetime.now(timezone.utc)
    for old, new in zip(old_ids, new_ids, strict=True):
        await db.execute(
            update(CardStock)
            .where(CardStock.id == old)
            .values(status="void", void_reason="defective", voided_at=now, replaced_by=new)
        )
    await db.execute(
        update(CardStock).where(CardStock.id.in_(new_ids)).values(status="sold", order_id=order.id)
    )
    db.add_all(
        Delivery(tenant_id=order.tenant_id, order_id=order.id, card_stock_id=cid, kind="card")
        for cid in new_ids
    )
    if order.status == "delivered":
        order.auto_confirm_at = max(
            order.auto_confirm_at or now, now + AFTER_SALES_REOPEN_EXTENSION
        )
    order.version += 1
    ticket.status = "replaced"
    ticket.resolved_at = now
    ticket.resolved_by = actor_id
    ticket.updated_at = now
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="aftersales.replaced",
        actor_id=actor_id,
        actor_role=actor_role,
        target=f"after_sales:{ticket.id}",
        before={"status": "processing"},
        after={"status": "replaced"},
        detail={"order_id": str(order.id), "cards": len(new_ids), "mode": "auto_card"},
    )
    msg = await post_system(
        db,
        tenant_id=order.tenant_id,
        conversation_id=order.conversation_id,
        event="after_sales_replaced",
        order_id=str(order.id),
        after_sales_id=str(ticket.id),
    )
    result.messages = [msg] if msg else []
    return result


async def _dispatch(db: AsyncSession, row: Outbox) -> JobResult:
    if row.topic == REPLACE_TOPIC:
        return await handle_replace(db, row.payload)
    return await handle_order_paid(db, uuid.UUID(str(row.payload["order_id"])))


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 2**attempts))


async def process_one(ctx: DeliveryContext) -> bool:
    """Process at most one outbox row. Returns False when the queue is empty."""
    async with tenant_session(ctx.maker, None) as db:
        row = await db.scalar(
            select(Outbox)
            .where(
                Outbox.processed_at.is_(None),
                Outbox.topic.in_(TOPICS),
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
                result = await _dispatch(db, row)
            failed: str | None = None
        except Exception as exc:  # job failure must not kill the loop; retried with backoff
            failed = type(exc).__name__
            log.error("outbox job failed id=%s error=%s", outbox_id, failed)
        row = await get_scoped(db, Outbox, outbox_id, tenant_id, populate_existing=True)
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
    async with tenant_session(ctx.maker, order.tenant_id) as db:
        buyer_locale = await db.scalar(select(User.locale).where(User.id == order.buyer_id))
        (bview,) = await order_views(
            db, [order], owner_id=order.buyer_id, lang=buyer_locale or "en"
        )
        (sview,) = await order_views(db, [order], owner_id=None, lang="en")
        ticket_view = None
        if result.ticket is not None:
            att_ids = list(result.ticket.attachment_ids)
            atts = (
                list(await db.scalars(select(Attachment).where(Attachment.id.in_(att_ids))))
                if att_ids
                else []
            )
            ticket_view = after_sales_view(result.ticket, atts, order.order_no)
    await publish_order(ctx.redis, order.tenant_id, order.buyer_id, bview, sview)
    if ticket_view is not None:
        await publish_after_sales(
            ctx.redis, order.tenant_id, order.buyer_id, ticket_view, ticket_view
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
