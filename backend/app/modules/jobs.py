"""Periodic jobs run from the API's expiry loop (one process, idempotent, per-tenant)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import audit
from app.core.config import Settings
from app.core.db import tenant_session
from app.core.resources import Resources
from app.models import AfterSales, Order, Tenant, User
from app.modules.chat.messages import post_system
from app.modules.inventory.service import void_expired
from app.modules.me.service import anonymize, remove_files
from app.modules.orders.events import add_event, snapshot
from app.modules.orders.service import broadcast_order
from app.modules.uploads.storage import FileStore

log = logging.getLogger("app.jobs")

BATCH = 200


async def _tenant_ids(maker: async_sessionmaker[AsyncSession]) -> list:
    async with tenant_session(maker, None) as s:
        return list(await s.scalars(select(Tenant.id)))


async def auto_confirm(maker: async_sessionmaker[AsyncSession], res: Resources | None) -> int:
    """delivered -> completed once ``auto_confirm_at`` has passed, unless an after-sales
    ticket is open (the timer is paused; closing the ticket extends it by at least 24 h)."""
    total = 0
    for tid in await _tenant_ids(maker):
        async with tenant_session(maker, tid) as db:
            open_ticket = exists().where(
                AfterSales.order_id == Order.id, AfterSales.status.in_(("open", "processing"))
            )
            rows = list(
                await db.scalars(
                    select(Order)
                    .where(
                        Order.status == "delivered",
                        Order.auto_confirm_at.is_not(None),
                        Order.auto_confirm_at <= func.now(),
                        ~open_ticket,
                    )
                    .order_by(Order.auto_confirm_at)
                    .with_for_update(skip_locked=True)
                    .limit(BATCH)
                )
            )
            msgs = {}
            done: list[Order] = []
            for order in rows:
                # re-check under the order lock: a ticket may have been opened since the select
                if await db.scalar(
                    select(
                        exists().where(
                            AfterSales.order_id == order.id,
                            AfterSales.status.in_(("open", "processing")),
                        )
                    )
                ):
                    continue
                done.append(order)
                before = snapshot(order)
                order.status = "completed"
                order.completed_at = datetime.now(timezone.utc)
                order.version += 1
                await add_event(
                    db,
                    order,
                    "delivered",
                    "completed",
                    actor_kind="system",
                    reason="auto_confirm",
                )
                await audit(
                    db,
                    tenant_id=tid,
                    action="order.completed",
                    target=f"order:{order.id}",
                    before=before,
                    after=snapshot(order),
                    detail={"auto": True},
                )
                msgs[order.id] = await post_system(
                    db,
                    tenant_id=tid,
                    conversation_id=order.conversation_id,
                    event="completed",
                    order_id=str(order.id),
                    auto=True,
                )
            await db.commit()
            total += len(done)
            if res is not None:
                for order in done:
                    await broadcast_order(res, db, order, [msgs.get(order.id)])
    return total


async def purge_instant_accounts(
    maker: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    """Instant accounts that never paid anything are anonymised after N days (default 7)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.instant_purge_days)
    total = 0
    for tid in await _tenant_ids(maker):
        async with tenant_session(maker, tid) as db:
            paid = exists().where(Order.buyer_id == User.id, Order.paid_at.is_not(None))
            purged_keys: list[str] = []
            users = list(
                await db.scalars(
                    select(User)
                    .where(
                        User.is_instant.is_(True),
                        User.status == "active",
                        User.role == "buyer",
                        User.created_at < cutoff,
                        ~paid,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            )
            for u in users:
                _, keys = await anonymize(db, u)
                purged_keys.extend(keys)
                await audit(
                    db,
                    tenant_id=tid,
                    action="auth.instant_purged",
                    target=f"user:{u.id}",
                )
            await db.commit()
            await remove_files(FileStore(settings.upload_dir), purged_keys)
            total += len(users)
    return total


async def void_expired_cards(maker: async_sessionmaker[AsyncSession]) -> int:
    total = 0
    for tid in await _tenant_ids(maker):
        async with tenant_session(maker, tid) as db:
            total += await void_expired(db, tid)
    return total
