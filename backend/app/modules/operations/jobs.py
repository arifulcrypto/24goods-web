"""API-owned durable service.reminder jobs. External delivery is at least once.

Claim/attempt metadata commits before sending. A crash after sending but before the result
commit can repeat the stable notification ID; bounded retries never imply exactly once.
The delivery worker must not consume this topic.
"""

from __future__ import annotations

import asyncio
import logging
import time as monotonic_time
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core import mailer, notify_tg, secretbox, suspend
from app.core.db import tenant_session
from app.core.resources import Resources
from app.core.security import STAFF_ROLES
from app.core.settings_v5 import section
from app.models import Order, OrderEvent, Outbox, Product, Tenant, User
from app.models.operations import (
    OperationEvent,
    ServiceEntitlement,
    ServiceReminder,
    ServiceRenewal,
)
from app.modules.content.site import tenant_settings
from app.modules.operations.service import (
    periods_end,
    record_event,
    snapshot,
    system_changed,
    utcnow,
    zone,
)

log = logging.getLogger("app.operations")

TOPIC = "service.reminder"
SUBSCRIPTION_WINDOW = timedelta(days=45)
SUBSCRIPTION_BATCH = 50
AUTO = "auto_subscription"
BATCH = 20
MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=5)
SEND_TIMEOUT_S = 10.0
RUN_BUDGET_S = 20.0


def schedule(row: ServiceEntitlement, now: datetime) -> list[tuple[str, datetime]]:
    if row.status != "active" or row.ends_at is None:
        return []
    tz = zone(row.timezone)
    due = row.ends_at.astimezone(tz)
    dates = [
        (f"before_{days}", datetime.combine(due.date() - timedelta(days=days), time(9), tz))
        for days in (7, 3, 1)
    ]
    dates.append(("expiry", row.ends_at))
    local = now.astimezone(tz)
    if local.date() > due.date():
        dates.append(("overdue", datetime.combine(local.date(), time(9), tz)))
    channels = [
        c
        for c, enabled in (("email", row.email_reminders), ("telegram", row.telegram_reminders))
        if enabled
    ]
    return [
        (f"{kind}:{channel}", when.astimezone(timezone.utc))
        for kind, when in dates
        for channel in channels
    ]


async def tenant_ids(res: Resources) -> list[uuid.UUID]:
    async with tenant_session(res.maker, None) as db:
        return list(await db.scalars(select(Tenant.id)))


async def schedule_reminders(res: Resources) -> int:
    count = 0
    now = utcnow()
    for tid in await tenant_ids(res):
        async with tenant_session(res.maker, tid) as db:
            rows = list(
                await db.scalars(
                    select(ServiceEntitlement)
                    .where(
                        ServiceEntitlement.tenant_id == tid, ServiceEntitlement.status == "active"
                    )
                    .order_by(
                        ServiceEntitlement.reminder_scheduled_at.asc().nullsfirst(),
                        ServiceEntitlement.id,
                    )
                    .limit(200)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                for kind, when in schedule(row, now):
                    rid = await db.scalar(
                        insert(ServiceReminder)
                        .values(
                            id=uuid.uuid4(),
                            tenant_id=tid,
                            entitlement_id=row.id,
                            period_version=row.version,
                            kind=kind,
                            scheduled_at=when,
                            status="pending",
                        )
                        .on_conflict_do_nothing(constraint="uq_service_reminder_schedule")
                        .returning(ServiceReminder.id)
                    )
                    if rid:
                        await db.execute(
                            insert(Outbox)
                            .values(
                                tenant_id=tid,
                                topic=TOPIC,
                                key=str(rid),
                                payload={"reminder_id": str(rid)},
                                available_at=when,
                            )
                            .on_conflict_do_nothing(
                                index_elements=["tenant_id", "topic", "key"],
                                index_where=text("topic='service.reminder'"),
                            )
                        )
                        count += 1
                row.reminder_scheduled_at = now
            await db.commit()
    return count


class ReminderSendError(Exception):
    pass


async def send_reminder(
    res: Resources, db, service: ServiceEntitlement, reminder: ServiceReminder, owner: User
) -> None:  # type: ignore[no-untyped-def]
    """Only the active assigned staff owner or the explicitly configured staff TG chats."""
    ts = await tenant_settings(db, service.tenant_id)
    link = notify_tg.admin_link(res, f"services/{service.id}")
    labels = {
        "en": {
            "title": "Service expiry reminder",
            "id": "Notification",
            "service": "Service",
            "buyer": "Buyer reference",
            "due": "Due",
        },
        "zh-CN": {
            "title": "服务到期提醒",
            "id": "通知编号",
            "service": "服务",
            "buyer": "买家编号",
            "due": "到期时间",
        },
    }
    words = labels.get(owner.locale, labels["en"])
    title = f"[{res.settings.env}] {words['title']}"
    lines = [
        title,
        f"{words['id']} {reminder.id}",
        f"{words['service']} {service.label}",
        f"{words['buyer']} {str(service.buyer_id)[:8]}…",
        f"{words['due']} {service.ends_at.isoformat() if service.ends_at else 'unknown'}",
        link,
    ]
    body = "\n".join(lines)
    if reminder.kind.endswith(":email"):
        cfg = await mailer.load_config(
            db, res.settings, service.tenant_id, ts, require_enabled=True
        )
        if cfg is None or owner.email.endswith(".invalid") or not owner.email:
            raise ReminderSendError("invalid_configuration")
        try:
            await mailer.send_mail(cfg, owner.email, title, body)
        except mailer.MailError:
            raise ReminderSendError("send_outcome_unknown") from None
    else:
        cfg = section(ts, "notifications")
        token = await secretbox.get_secret(
            db, res.settings, service.tenant_id, "telegram_bot_token"
        )
        if not cfg["telegram_enabled"] or not cfg["telegram_chat_ids"] or not token:
            raise ReminderSendError("invalid_configuration")
        message = notify_tg.build_text(words["title"], lines)
        results = [
            await notify_tg._post(res, token, chat, message) for chat in cfg["telegram_chat_ids"]
        ]
        if not all(results):
            raise ReminderSendError("send_outcome_unknown")


async def process_one(res: Resources, tenant: uuid.UUID, ident: int) -> bool:
    async with tenant_session(res.maker, tenant) as db:
        out = await db.scalar(
            select(Outbox)
            .where(
                Outbox.id == ident,
                Outbox.tenant_id == tenant,
                Outbox.topic == TOPIC,
                Outbox.processed_at.is_(None),
                Outbox.available_at <= utcnow(),
            )
            .with_for_update(skip_locked=True)
        )
        if out is None:
            return False
        out.attempts += 1
        out.last_error = "send_outcome_unknown"
        out.available_at = utcnow() + LEASE
        await db.commit()  # persistent uncertain state precedes all external effects
        out = await db.scalar(
            select(Outbox)
            .where(Outbox.id == ident, Outbox.tenant_id == tenant, Outbox.topic == TOPIC)
            .with_for_update()
        )
        reminder = await db.scalar(
            select(ServiceReminder).where(
                ServiceReminder.id == uuid.UUID(out.key), ServiceReminder.tenant_id == tenant
            )
        )
        service = (
            await db.scalar(
                select(ServiceEntitlement)
                .where(
                    ServiceEntitlement.id == reminder.entitlement_id,
                    ServiceEntitlement.tenant_id == tenant,
                )
                .with_for_update()
            )
            if reminder
            else None
        )
        owner = (
            await db.scalar(
                select(User).where(User.id == service.owner_id, User.tenant_id == tenant)
            )
            if service
            else None
        )
        stale = (
            reminder is None
            or service is None
            or service.status != "active"
            or reminder.period_version != service.version
            or (reminder.kind.endswith(":email") and not service.email_reminders)
            or (reminder.kind.endswith(":telegram") and not service.telegram_reminders)
        )
        if stale:
            if reminder:
                reminder.status = "cancelled"
            out.processed_at, out.last_error = utcnow(), "obsolete_version"
            await db.commit()
            return True
        error = None
        if (
            owner is None
            or owner.status != "active"
            or owner.role not in ("admin", "agent")
            or suspend.is_suspended(owner)
        ):
            error = "invalid_owner"
        elif out.attempts > MAX_ATTEMPTS:
            error = "send_outcome_unknown"
        else:
            try:
                await asyncio.wait_for(
                    send_reminder(res, db, service, reminder, owner), SEND_TIMEOUT_S
                )
            except ReminderSendError as exc:
                error = str(exc)
            except Exception:
                error = "send_outcome_unknown"
        if error is None or out.attempts >= MAX_ATTEMPTS:
            out.processed_at = utcnow()
            reminder.status = "sent" if error is None else "failed"
        else:
            out.available_at = utcnow() + timedelta(minutes=2**out.attempts)
        out.last_error = error
        await record_event(
            db,
            tenant_id=tenant,
            environment=res.settings.env,
            family="task",
            action="service.reminder",
            outcome="success" if error is None else "failed",
            reason=error or "sent",
            resource_id=str(service.id),
            detail={
                "task_id": str(reminder.id),
                "attempt": out.attempts,
                "version": service.version,
            },
        )
        await db.commit()
        return True


async def process_reminders(res: Resources) -> int:
    count = 0
    deadline = monotonic_time.monotonic() + RUN_BUDGET_S
    for tid in await tenant_ids(res):
        async with tenant_session(res.maker, tid) as db:
            ids = list(
                await db.scalars(
                    select(Outbox.id)
                    .where(
                        Outbox.tenant_id == tid,
                        Outbox.topic == TOPIC,
                        Outbox.processed_at.is_(None),
                        Outbox.available_at <= func.now(),
                    )
                    .order_by(Outbox.id)
                    .limit(BATCH)
                )
            )
        for ident in ids:
            if monotonic_time.monotonic() >= deadline:
                return count
            if await process_one(res, tid, ident):
                count += 1
    return count


async def purge_events(res: Resources) -> int:
    count = 0
    for tid in await tenant_ids(res):
        async with tenant_session(res.maker, tid) as db:
            ids = (
                select(OperationEvent.id)
                .where(
                    OperationEvent.tenant_id == tid,
                    OperationEvent.created_at < utcnow() - timedelta(days=90),
                )
                .order_by(OperationEvent.id)
                .limit(1000)
            )
            result = await db.execute(
                delete(OperationEvent).where(
                    OperationEvent.tenant_id == tid, OperationEvent.id.in_(ids)
                )
            )
            count += result.rowcount or 0
            await db.commit()
    return count


async def _delivery_owner(db, tid: uuid.UUID, order: Order) -> uuid.UUID | None:  # type: ignore[no-untyped-def]
    """The staff user who delivered the order (if recorded and still active staff), else the
    tenant's earliest active admin."""
    actor = await db.scalar(
        select(OrderEvent.actor_id)
        .join(User, User.id == OrderEvent.actor_id)
        .where(
            OrderEvent.tenant_id == tid,
            OrderEvent.order_id == order.id,
            OrderEvent.to_status == "delivered",
            OrderEvent.actor_kind == "user",
            User.tenant_id == tid,
            User.status == "active",
            User.role.in_(STAFF_ROLES),
        )
        .order_by(OrderEvent.id.desc())
        .limit(1)
    )
    if actor is not None:
        return actor
    return await db.scalar(
        select(User.id)
        .where(User.tenant_id == tid, User.role == "admin", User.status == "active")
        .order_by(User.created_at, User.id)
        .limit(1)
    )


async def _apply_subscription(db, tid: uuid.UUID, order: Order, product: Product) -> str | None:  # type: ignore[no-untyped-def]
    """Create or extend the service for one delivered subscription order (caller holds a
    savepoint). Returns ``renewed``/``created`` or None when it must be skipped."""
    cfg = product.subscription or {}
    qty = max(1, order.qty)
    start = order.delivered_at
    existing = list(
        await db.scalars(
            select(ServiceEntitlement)
            .where(
                ServiceEntitlement.tenant_id == tid,
                ServiceEntitlement.buyer_id == order.buyer_id,
                ServiceEntitlement.product_id == product.id,
                ServiceEntitlement.status != "cancelled",
            )
            .with_for_update()
        )
    )
    if len(existing) > 1:
        # Ambiguous (e.g. a manual and an auto service for the same product): never guess which
        # one to extend and never add a third. Staff link it from the Services page.
        n = len(existing)
        log.warning("subscription order %s skipped: %d open services match", order.id, n)
        return None
    if len(existing) == 1:
        row = existing[0]
        before = snapshot(row)
        base = max(row.ends_at, start) if row.ends_at else start
        if row.starts_at is None:
            row.starts_at = start
        row.ends_at = periods_end(base, row.period_policy, row.period_days, row.timezone, qty)
        row.version += 1
        row.status = "active"
        db.add(
            ServiceRenewal(
                tenant_id=tid,
                entitlement_id=row.id,
                order_id=order.id,
                confirmed_by=row.owner_id,
                period_version=row.version,
                starts_at=base,
                ends_at=row.ends_at,
            )
        )
        await db.flush()
        await system_changed(db, row, "service.renewed", before, AUTO)
        return "renewed"
    owner = await _delivery_owner(db, tid, order)
    if owner is None:
        log.warning("subscription order skipped: no active staff owner order=%s", order.id)
        return None
    policy = cfg.get("period_policy", "calendar_month")
    days = int(cfg.get("period_days", 30))
    row = ServiceEntitlement(
        tenant_id=tid,
        buyer_id=order.buyer_id,
        order_id=order.id,
        owner_id=owner,
        product_id=product.id,
        source="auto",
        label=((product.title_i18n.get("en") or order.title or "Subscription").strip())[:120],
        status="active",
        timezone="UTC",
        period_policy=policy,
        period_days=days,
        grace_days=int(cfg.get("grace_days", 0)),
        email_reminders=bool(cfg.get("email_reminders", True)),
        telegram_reminders=bool(cfg.get("telegram_reminders", True)),
        starts_at=start,
        ends_at=periods_end(start, policy, days, "UTC", qty),
        version=1,
    )
    db.add(row)
    await db.flush()
    await system_changed(db, row, "service.created", None, AUTO)
    return "created"


async def sync_subscriptions(res: Resources) -> int:
    """Turn delivered subscription orders into services (new or renewed) and cancel auto
    services whose source order was refunded/cancelled. Idempotent: the unique
    (tenant, order) constraints on services/renewals back every step."""
    count = 0
    now = utcnow()
    for tid in await tenant_ids(res):
        async with tenant_session(res.maker, tid) as db:
            used_src = exists().where(
                ServiceEntitlement.tenant_id == tid, ServiceEntitlement.order_id == Order.id
            )
            used_ren = exists().where(
                ServiceRenewal.tenant_id == tid, ServiceRenewal.order_id == Order.id
            )
            pairs = (
                await db.execute(
                    select(Order, Product)
                    .join(Product, Product.id == Order.product_id)
                    .where(
                        Order.tenant_id == tid,
                        Product.tenant_id == tid,
                        Product.subscription.is_not(None),
                        Order.status.in_(("delivered", "completed")),
                        Order.delivered_at >= now - SUBSCRIPTION_WINDOW,
                        ~used_src,
                        ~used_ren,
                    )
                    .order_by(Order.delivered_at, Order.id)
                    .limit(SUBSCRIPTION_BATCH)
                )
            ).all()
            for order, product in pairs:
                try:
                    async with db.begin_nested():
                        if await _apply_subscription(db, tid, order, product):
                            count += 1
                except IntegrityError:
                    log.info("subscription order already applied order=%s", order.id)
            doomed = (
                await db.execute(
                    select(ServiceEntitlement)
                    .join(Order, Order.id == ServiceEntitlement.order_id)
                    .where(
                        ServiceEntitlement.tenant_id == tid,
                        Order.tenant_id == tid,
                        ServiceEntitlement.source == "auto",
                        ServiceEntitlement.status != "cancelled",
                        Order.status.in_(("refunded", "cancelled")),
                    )
                    .order_by(ServiceEntitlement.id)
                    .limit(SUBSCRIPTION_BATCH)
                    .with_for_update(of=ServiceEntitlement)
                )
            ).scalars()
            for row in list(doomed):
                before = snapshot(row)
                row.status = "cancelled"
                row.version += 1
                await system_changed(db, row, "service.cancelled", before, "source_order_void")
                count += 1
            await db.commit()
    return count
