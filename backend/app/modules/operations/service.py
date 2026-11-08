"""Manual service periods; verified payment and a staff confirmation are both required."""

from __future__ import annotations

import calendar
import hashlib
import html
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, InvalidState, NotFound, Validation
from app.core.security import STAFF_ROLES
from app.models import Order, Payment, User
from app.models.operations import OperationEvent, ServiceEntitlement, ServiceRenewal
from app.modules.common.views import iso

SAFE_CODE = re.compile(r"^[a-zA-Z0-9_.:-]{1,64}$")
SENSITIVE = re.compile(
    r"(?i)(password|secret|token|cookie|authorization|code|key)\s*[:=]\s*[^\s;,&]+"
)
EVENT_FIELDS = frozenset({"duration_ms", "attempt", "version", "task_id", "changed_fields"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise Validation("Unknown billing timezone") from None


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise Validation("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def period_end(start: datetime, policy: str, days: int, tz: str) -> datetime:
    """Calendar periods keep local wall time with month-end clamping; fixed days = 24h.
    A nonexistent DST wall time normalizes forward; ambiguous time uses fold=0."""
    start = aware(start)
    if policy == "fixed_days":
        return start + timedelta(days=days)
    if policy != "calendar_month":
        raise Validation("Unknown period policy")
    local = start.astimezone(zone(tz))
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    day = min(local.day, calendar.monthrange(year, month)[1])
    return local.replace(year=year, month=month, day=day, fold=0).astimezone(timezone.utc)


def snapshot(row: ServiceEntitlement) -> dict[str, Any]:
    return {
        "status": row.status,
        "owner_id": str(row.owner_id),
        "version": row.version,
        "starts_at": iso(row.starts_at),
        "ends_at": iso(row.ends_at),
        "period_policy": row.period_policy,
        "period_days": row.period_days,
        "timezone": row.timezone,
        "grace_days": row.grace_days,
        "email_reminders": row.email_reminders,
        "telegram_reminders": row.telegram_reminders,
    }


def view(row: ServiceEntitlement) -> dict[str, Any]:
    due = row.ends_at
    now = utcnow()
    return {
        "id": str(row.id),
        "buyer_id": str(row.buyer_id),
        "order_id": str(row.order_id),
        "label": row.label,
        **snapshot(row),
        "created_at": iso(row.created_at),
        "overdue": row.status == "active" and due is not None and due <= now,
        "in_grace": bool(
            row.status == "active" and due and due <= now < due + timedelta(days=row.grace_days)
        ),
    }


async def owned(
    db: AsyncSession, tenant: uuid.UUID, ident: uuid.UUID, *, lock: bool = False
) -> ServiceEntitlement:
    query = select(ServiceEntitlement).where(
        ServiceEntitlement.id == ident, ServiceEntitlement.tenant_id == tenant
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query)
    if row is None:
        raise NotFound("Service not found")
    return row


async def staff_owner(db: AsyncSession, tenant: uuid.UUID, owner_id: uuid.UUID) -> User:
    user = await db.scalar(select(User).where(User.id == owner_id, User.tenant_id == tenant))
    if user is None or user.role not in STAFF_ROLES or user.status != "active":
        raise NotFound("Staff owner not found")
    return user


async def paid_order(
    db: AsyncSession, tenant: uuid.UUID, buyer: uuid.UUID, ident: uuid.UUID
) -> Order:
    row = await db.scalar(
        select(Order).where(Order.id == ident, Order.tenant_id == tenant).with_for_update()
    )
    if row is None or row.buyer_id != buyer:
        raise NotFound("Order not found")
    payment = await db.scalar(
        select(Payment).where(
            Payment.tenant_id == tenant,
            Payment.status == "succeeded",
            or_(
                Payment.order_id == row.id,
                Payment.checkout_id == row.checkout_id if row.checkout_id else False,
            ),
        )
    )
    if (
        row.status not in ("paid", "delivered", "completed")
        or row.paid_at is None
        or payment is None
    ):
        raise InvalidState("A verified paid order is required")
    if payment.currency != row.currency or payment.amount_minor < row.amount_minor:
        raise InvalidState("Payment does not cover the service order")
    return row


async def changed(
    db: AsyncSession,
    row: ServiceEntitlement,
    actor: User,
    action: str,
    before: dict[str, Any] | None,
    reason: str,
    ip: str | None,
) -> None:
    await audit(
        db,
        tenant_id=row.tenant_id,
        action=action,
        actor_id=actor.id,
        actor_role=actor.role,
        target=f"service:{row.id}",
        before=before,
        after=snapshot(row),
        detail={"reason": SENSITIVE.sub("[redacted]", reason)[:200]},
        ip=ip,
    )
    await record_event(
        db,
        tenant_id=row.tenant_id,
        environment=os.environ.get("APP_ENV", "unknown"),
        family="operation",
        action=action,
        outcome="success",
        reason="staff_confirmed",
        actor_id=actor.id,
        resource_id=str(row.id),
        ip=ip,
        detail={"version": row.version},
    )


async def create(db: AsyncSession, actor: User, body: Any, ip: str | None) -> ServiceEntitlement:
    await staff_owner(db, actor.tenant_id, body.owner_id)
    buyer = await db.scalar(
        select(User).where(
            User.id == body.buyer_id, User.tenant_id == actor.tenant_id, User.role == "buyer"
        )
    )
    if buyer is None:
        raise NotFound("Buyer not found")
    await paid_order(db, actor.tenant_id, body.buyer_id, body.order_id)
    if await db.scalar(
        select(ServiceEntitlement.id).where(
            ServiceEntitlement.tenant_id == actor.tenant_id,
            ServiceEntitlement.order_id == body.order_id,
        )
    ):
        raise Conflict("This order already has a service")
    if await db.scalar(
        select(ServiceRenewal.id).where(
            ServiceRenewal.tenant_id == actor.tenant_id, ServiceRenewal.order_id == body.order_id
        )
    ):
        raise Conflict("This order was already used for renewal")
    zone(body.timezone)
    row = ServiceEntitlement(
        tenant_id=actor.tenant_id, **body.model_dump(), status="pending", version=1
    )
    db.add(row)
    await db.flush()
    await changed(db, row, actor, "service.created", None, "staff_created", ip)
    await db.commit()
    return row


async def activate(
    db: AsyncSession, actor: User, ident: uuid.UUID, when: datetime | None, ip: str | None
) -> ServiceEntitlement:
    row = await owned(db, actor.tenant_id, ident, lock=True)
    if row.status == "active":
        return row
    if row.status != "pending":
        raise InvalidState("Only a pending service can be activated")
    await paid_order(db, row.tenant_id, row.buyer_id, row.order_id)
    start = aware(when) if when else utcnow()
    if start > utcnow() + timedelta(minutes=5):
        raise Validation("Activation cannot be in the future")
    before = snapshot(row)
    row.starts_at, row.ends_at = (
        start,
        period_end(start, row.period_policy, row.period_days, row.timezone),
    )
    row.status, row.version = "active", row.version + 1
    await changed(db, row, actor, "service.activated", before, "staff_confirmed", ip)
    await db.commit()
    return row


async def patch(
    db: AsyncSession, actor: User, ident: uuid.UUID, body: Any, ip: str | None
) -> ServiceEntitlement:
    row = await owned(db, actor.tenant_id, ident, lock=True)
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    reason = data.pop("reason")
    expected = data.pop("expected_version", None)
    if expected is not None and expected != row.version:
        raise Conflict("Service was changed; refresh first")
    if "owner_id" in data:
        await staff_owner(db, row.tenant_id, data["owner_id"])
    if "timezone" in data:
        zone(data["timezone"])
    if "ends_at" in data:
        data["ends_at"] = aware(data["ends_at"])
        if row.starts_at is None or data["ends_at"] <= row.starts_at:
            raise Validation("Due time must be after activation")
    if data.get("status") == "active" and row.status != "active":
        raise InvalidState("Use activation or a paid renewal to start service")
    before = snapshot(row)
    for key, value in data.items():
        setattr(row, key, value)
    row.version += 1
    await changed(db, row, actor, "service.updated", before, reason, ip)
    await db.commit()
    return row


async def renew(
    db: AsyncSession, actor: User, ident: uuid.UUID, body: Any, ip: str | None
) -> ServiceEntitlement:
    row = await owned(db, actor.tenant_id, ident, lock=True)
    if row.starts_at is None or row.ends_at is None:
        raise InvalidState("Activate the service before renewal")
    order = await paid_order(db, row.tenant_id, row.buyer_id, body.order_id)
    previous = await db.scalar(
        select(ServiceRenewal).where(
            ServiceRenewal.tenant_id == row.tenant_id, ServiceRenewal.order_id == order.id
        )
    )
    if previous is not None:
        if previous.entitlement_id != row.id:
            raise Conflict("Renewal order was already used")
        return row
    used_source = await db.scalar(
        select(ServiceEntitlement.id).where(
            ServiceEntitlement.tenant_id == row.tenant_id, ServiceEntitlement.order_id == order.id
        )
    )
    if used_source:
        raise Conflict("An activation order cannot be reused for renewal")
    confirmed = aware(body.confirmed_at) if body.confirmed_at else utcnow()
    if confirmed > utcnow() + timedelta(minutes=5):
        raise Validation("Confirmation cannot be in the future")
    start = confirmed if row.status == "cancelled" else max(row.ends_at, confirmed)
    before = snapshot(row)
    row.ends_at = period_end(start, row.period_policy, row.period_days, row.timezone)
    row.version += 1
    row.status = "active"
    db.add(
        ServiceRenewal(
            tenant_id=row.tenant_id,
            entitlement_id=row.id,
            order_id=order.id,
            confirmed_by=actor.id,
            period_version=row.version,
            starts_at=start,
            ends_at=row.ends_at,
        )
    )
    await changed(db, row, actor, "service.renewed", before, body.reason, ip)
    await db.commit()
    return row


def sanitized_ua(raw: str | None) -> str | None:
    if not raw:
        return None
    safe = "".join(c for c in raw[:1024] if ord(c) >= 32 and ord(c) != 127)
    return html.escape(SENSITIVE.sub("[redacted]", safe))[:256]


async def record_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment: str,
    family: str,
    action: str,
    outcome: str,
    reason: str,
    actor_id: uuid.UUID | None = None,
    resource_id: str | None = None,
    request_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Caller commits with its operation. Only typed metadata is accepted, never raw errors/body."""
    if family not in ("operation", "login", "security", "system", "task", "payment"):
        raise ValueError("unknown event family")
    if (
        outcome not in ("success", "denied", "failed", "unknown")
        or not SAFE_CODE.fullmatch(action)
        or not SAFE_CODE.fullmatch(reason)
    ):
        raise ValueError("invalid event code")
    safe: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if key in EVENT_FIELDS and isinstance(value, (int, bool)):
            safe[key] = value
        elif key == "task_id":
            safe[key] = hashlib.sha256(str(value).encode()).hexdigest()[:24]
        elif key == "changed_fields" and isinstance(value, list):
            safe[key] = [str(v) for v in value[:30] if SAFE_CODE.fullmatch(str(v))]
    db.add(
        OperationEvent(
            tenant_id=tenant_id,
            environment=environment[:16],
            family=family,
            action=action,
            outcome=outcome,
            reason=reason,
            actor_id=actor_id,
            resource_id=str(resource_id)[:128] if resource_id else None,
            request_id=request_id[:64] if request_id and SAFE_CODE.fullmatch(request_id) else None,
            ip=ip[:64] if ip else None,
            user_agent=sanitized_ua(user_agent),
            detail=safe,
            severity="warning" if outcome in ("failed", "denied") else "info",
            occurred_at=utcnow(),
        )
    )
