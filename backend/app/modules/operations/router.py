"""Session-only operational administration and staff-managed service entitlements."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, client_ip, get_db, get_page, get_res, require_admin, require_staff
from app.core.errors import Forbidden
from app.core.resources import Resources
from app.models import Tenant, User
from app.models.operations import OperationEvent, ServiceEntitlement
from app.modules.operations import service
from app.modules.operations.overview import overview

router = APIRouter(tags=["operations"])


async def session_staff(request: Request, user: User = Depends(require_staff)) -> User:
    if getattr(request.state, "api_token", None) is not None:
        raise Forbidden("A staff session is required")
    return user


async def session_admin(
    request: Request, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> User:
    if getattr(request.state, "api_token", None) is not None:
        raise Forbidden("An administrator session is required")
    kind = await db.scalar(select(Tenant.kind).where(Tenant.id == user.tenant_id))
    if kind != "platform":
        raise Forbidden("Server administrator only")
    return user


class ServiceIn(BaseModel):
    buyer_id: uuid.UUID
    order_id: uuid.UUID
    owner_id: uuid.UUID
    label: str = Field(min_length=1, max_length=120)
    timezone: str = Field("UTC", max_length=64)
    period_policy: Literal["calendar_month", "fixed_days"] = "calendar_month"
    period_days: int = Field(30, ge=1, le=366)
    grace_days: int = Field(0, ge=0, le=30)
    email_reminders: bool = False
    telegram_reminders: bool = False

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("A plain service label is required")
        return value


class ServicePatch(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    expected_version: int | None = Field(None, ge=1)
    label: str | None = Field(None, min_length=1, max_length=120)
    owner_id: uuid.UUID | None = None
    status: Literal["active", "cancelled"] | None = None
    timezone: str | None = Field(None, max_length=64)
    period_policy: Literal["calendar_month", "fixed_days"] | None = None
    period_days: int | None = Field(None, ge=1, le=366)
    grace_days: int | None = Field(None, ge=0, le=30)
    ends_at: datetime | None = None
    email_reminders: bool | None = None
    telegram_reminders: bool | None = None

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str | None) -> str | None:
        return ServiceIn.clean_label(value) if value is not None else None


class ActivationIn(BaseModel):
    activated_at: datetime | None = None


class RenewalIn(BaseModel):
    order_id: uuid.UUID
    confirmed_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=200)


@router.get("/admin/operations")
async def operations(
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    return await overview(db, res, admin.tenant_id)


@router.get("/admin/operations/events")
async def events(
    family: Literal["operation", "login", "security", "system", "task", "payment"] | None = None,
    outcome: Literal["success", "denied", "failed", "unknown"] | None = None,
    page: Page = Depends(get_page),
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(OperationEvent).where(OperationEvent.tenant_id == admin.tenant_id)
    if family:
        query = query.where(OperationEvent.family == family)
    if outcome:
        query = query.where(OperationEvent.outcome == outcome)
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        await db.scalars(
            query.order_by(OperationEvent.id.desc()).offset(page.offset).limit(page.page_size)
        )
    )
    return page.wrap(
        [
            {
                "id": r.id,
                "family": r.family,
                "action": r.action,
                "outcome": r.outcome,
                "reason": r.reason,
                "severity": r.severity,
                "environment": r.environment,
                "actor_id": str(r.actor_id) if r.actor_id else None,
                "resource_id": r.resource_id,
                "request_id": r.request_id,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "detail": r.detail,
                "occurred_at": r.occurred_at.isoformat(),
                "created_at": r.created_at.isoformat(),
                "location": {"status": "unknown"},
            }
            for r in rows
        ],
        total,
    )


@router.get("/admin/services")
async def services(
    status: Literal["pending", "active", "cancelled"] | None = None,
    due: Literal["upcoming", "overdue"] | None = None,
    mine: bool = False,
    page: Page = Depends(get_page),
    staff: User = Depends(session_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    query = select(ServiceEntitlement).where(ServiceEntitlement.tenant_id == staff.tenant_id)
    if status:
        query = query.where(ServiceEntitlement.status == status)
    if mine:
        query = query.where(ServiceEntitlement.owner_id == staff.id)
    now = service.utcnow()
    if due == "overdue":
        query = query.where(
            ServiceEntitlement.status == "active", ServiceEntitlement.ends_at <= now
        )
    elif due == "upcoming":
        query = query.where(
            ServiceEntitlement.status == "active",
            ServiceEntitlement.ends_at > now,
            ServiceEntitlement.ends_at <= now + timedelta(days=7),
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await db.scalars(
        query.order_by(ServiceEntitlement.ends_at.asc().nullsfirst(), ServiceEntitlement.id)
        .offset(page.offset)
        .limit(page.page_size)
    )
    return page.wrap([service.view(row) for row in rows], total)


@router.post("/admin/services", status_code=201)
async def create(
    body: ServiceIn,
    request: Request,
    staff: User = Depends(session_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return service.view(await service.create(db, staff, body, client_ip(request)))


@router.get("/admin/services/{ident}")
async def detail(
    ident: uuid.UUID, staff: User = Depends(session_staff), db: AsyncSession = Depends(get_db)
) -> dict:
    return service.view(await service.owned(db, staff.tenant_id, ident))


@router.patch("/admin/services/{ident}")
async def update(
    ident: uuid.UUID,
    body: ServicePatch,
    request: Request,
    staff: User = Depends(session_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return service.view(await service.patch(db, staff, ident, body, client_ip(request)))


@router.post("/admin/services/{ident}/activate")
async def activate(
    ident: uuid.UUID,
    body: ActivationIn,
    request: Request,
    staff: User = Depends(session_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return service.view(
        await service.activate(db, staff, ident, body.activated_at, client_ip(request))
    )


@router.post("/admin/services/{ident}/renewals")
async def renew(
    ident: uuid.UUID,
    body: RenewalIn,
    request: Request,
    staff: User = Depends(session_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return service.view(await service.renew(db, staff, ident, body, client_ip(request)))
