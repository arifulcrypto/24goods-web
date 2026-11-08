"""Tenant-scoped operational events and manual service periods; no payment credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class OperationEvent(Base):
    __tablename__ = "operation_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    family: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="info")
    environment: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    detail: Mapped[dict[str, Any]]
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at()


class ServiceEntitlement(Base):
    __tablename__ = "service_entitlements"
    __table_args__ = (UniqueConstraint("tenant_id", "order_id", name="uq_service_source_order"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    label: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    period_policy: Mapped[str] = mapped_column(String(16), default="calendar_month")
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    grace_days: Mapped[int] = mapped_column(Integer, default=0)
    reminder_scheduled_at: Mapped[datetime | None]
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    version: Mapped[int] = mapped_column(Integer, default=1)
    email_reminders: Mapped[bool] = mapped_column(default=False)
    telegram_reminders: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ServiceRenewal(Base):
    __tablename__ = "service_renewals"
    __table_args__ = (UniqueConstraint("tenant_id", "order_id", name="uq_service_renewal_order"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    entitlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_entitlements.id"))
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    confirmed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    period_version: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    created_at: Mapped[datetime] = created_at()


class ServiceReminder(Base):
    __tablename__ = "service_reminders"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entitlement_id",
            "period_version",
            "kind",
            "scheduled_at",
            name="uq_service_reminder_schedule",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    entitlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_entitlements.id"))
    period_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    scheduled_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = created_at()
