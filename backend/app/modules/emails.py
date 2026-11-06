"""Order emails (outbox topic ``email.order``), processed by the API jobs loop.

Best effort and never inside the payment transaction: the job scans recently paid/delivered
orders and enqueues one outbox row per (event, order) -- ``key = "<event>:<order_id>"`` -- then
sends pending rows. Only real (non-instant) buyers with a verified address are mailed.
The payload carries ids only; the mail contains order number, title, amount and a link,
NEVER card secrets or delivery content.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import mailer
from app.core.config import Settings
from app.core.db import tenant_session
from app.core.email_templates import render_template, site_name
from app.core.money import fmt
from app.core.settings_v5 import section
from app.models import Order, Outbox, Tenant, User
from app.modules.content.site import tenant_settings

log = logging.getLogger("app.emails")

TOPIC = "email.order"
LOOKBACK = timedelta(days=2)
MAX_ATTEMPTS = 5
BATCH = 50


def _eligible(u: User | None) -> bool:
    return bool(
        u is not None
        and u.status == "active"
        and u.role == "buyer"
        and not u.is_instant
        and u.email_verified
        and not u.email.endswith(".invalid")
    )


async def enqueue_order_emails(maker: async_sessionmaker[AsyncSession]) -> int:
    """One ``email.order`` outbox row per (event, order) for orders paid/delivered recently."""
    total = 0
    since = datetime.now(timezone.utc) - LOOKBACK
    async with tenant_session(maker, None) as s:
        tids = list(await s.scalars(select(Tenant.id)))
    for tid in tids:
        async with tenant_session(maker, tid) as db:
            em = section(await tenant_settings(db, tid), "email")
            if not em["enabled"]:
                continue
            for event, enabled, col in (
                ("paid", em["notify_order_paid"], Order.paid_at),
                ("delivered", em["notify_order_delivered"], Order.delivered_at),
            ):
                if not enabled:
                    continue
                sent = exists().where(
                    Outbox.topic == TOPIC,
                    Outbox.tenant_id == tid,
                    Outbox.key == func.concat(event + ":", cast(Order.id, String)),
                )
                rows = list(
                    await db.scalars(
                        select(Order)
                        .join(User, User.id == Order.buyer_id)
                        .where(
                            Order.tenant_id == tid,
                            col.is_not(None),
                            col > since,
                            User.is_instant.is_(False),
                            User.email_verified.is_(True),
                            ~sent,
                        )
                        .order_by(col)
                        .limit(BATCH)
                    )
                )
                for o in rows:
                    db.add(
                        Outbox(
                            tenant_id=tid,
                            topic=TOPIC,
                            key=f"{event}:{o.id}",
                            payload={"order_id": str(o.id), "event": event},
                        )
                    )
                total += len(rows)
            await db.commit()
    return total


async def _send_one(
    maker: async_sessionmaker[AsyncSession], settings: Settings, row_id: int, tid: uuid.UUID
) -> bool:
    """Send (or give up on) one outbox row. Returns True when the row was handled."""
    async with tenant_session(maker, tid) as db:
        row = await db.scalar(
            select(Outbox).where(Outbox.id == row_id).with_for_update(skip_locked=True)
        )
        if row is None or row.processed_at is not None:
            return False
        ts = await tenant_settings(db, tid)
        order = await db.scalar(
            select(Order).where(
                Order.id == uuid.UUID(str(row.payload["order_id"])), Order.tenant_id == tid
            )
        )
        user = await db.get(User, order.buyer_id) if order else None
        cfg = await mailer.load_config(db, settings, tid, ts, require_enabled=True)
        err: str | None = None
        if order is None or not _eligible(user) or cfg is None:
            err = "skipped"
        else:
            event = str(row.payload.get("event"))
            lang = user.locale if user and user.locale in ("en", "zh-CN") else "en"
            subject, text, html = render_template(
                ts,
                "order_paid" if event == "paid" else "order_delivered",
                lang,
                {
                    "site_name": site_name(ts, lang, settings.brand_name),
                    "order_no": order.order_no,
                    "title": order.title,
                    "amount": f"{order.currency} {fmt(order.amount_minor)}",
                    "order_url": f"{settings.public_base_url.rstrip('/')}/orders/{order.order_no}",
                },
            )
            try:
                await mailer.send_mail(cfg, user.email, subject, text, html)  # type: ignore[union-attr]
            except mailer.MailError as exc:
                err = str(exc)
        row.attempts += 1
        if err is None or err == "skipped" or row.attempts >= MAX_ATTEMPTS:
            row.processed_at = datetime.now(timezone.utc)
            row.last_error = err
        else:
            row.last_error = err
            row.available_at = datetime.now(timezone.utc) + timedelta(minutes=2**row.attempts)
        await db.commit()
        return True


async def process_email_outbox(maker: async_sessionmaker[AsyncSession], settings: Settings) -> int:
    sent = 0
    async with tenant_session(maker, None) as s:
        tids = list(await s.scalars(select(Tenant.id)))
    for tid in tids:
        async with tenant_session(maker, tid) as db:
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
        for rid in ids:
            if await _send_one(maker, settings, rid, tid):
                sent += 1
    return sent
