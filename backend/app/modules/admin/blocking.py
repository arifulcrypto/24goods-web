"""User suspension + blocked IPs (docs/review-v5.1.md §5): /admin/users, /admin/blocked-ips."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ipblock
from app.core.audit import audit
from app.core.deps import Page, client_ip, get_db, get_page, get_res, require_admin, require_staff
from app.core.errors import Conflict, Forbidden, NotFound, Validation
from app.core.resources import Resources
from app.core.settings_v5 import ip_in_nets
from app.core.suspend import is_suspended
from app.models import BlockedIP, User
from app.modules.catalog.service import escape_like
from app.modules.common.views import iso

router = APIRouter(prefix="/admin", tags=["admin-blocking"])


class SuspendIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    until: datetime | None = None

    @field_validator("until")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps need a timezone offset")
        return v


class BlockIn(BaseModel):
    cidr: str = Field(min_length=1, max_length=64)
    note: str | None = Field(None, max_length=200)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps need a timezone offset")
        return v


def user_row(u: User) -> dict:
    email = "" if u.email.endswith("@instant.invalid") else u.email
    return {
        "id": str(u.id),
        "email": email,
        "handle": u.handle,
        "display_name": u.display_name,
        "role": u.role,
        "status": "suspended" if is_suspended(u) else "active",
        "suspended_until": iso(u.suspended_until) if is_suspended(u) else None,
        "suspend_reason": u.suspend_reason if is_suspended(u) else None,
        "created_at": iso(u.created_at),
        "last_login_at": iso(u.last_login_at),
    }


def _state(u: User) -> dict:
    return {
        "suspended_at": iso(u.suspended_at),
        "suspended_until": iso(u.suspended_until),
        "reason": u.suspend_reason,
    }


@router.get("/users")
async def list_users(
    q: str | None = Query(None, max_length=100),
    role: Literal["buyer", "agent", "admin"] | None = None,
    status: Literal["active", "suspended"] | None = None,
    page: Page = Depends(get_page),
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base = select(User).where(User.tenant_id == staff.tenant_id)
    if staff.role != "admin":
        base = base.where(User.role == "buyer")  # agents only ever see buyers
    elif role:
        base = base.where(User.role == role)
    if q and q.strip():
        like = f"%{escape_like(q.strip().lower())}%"
        base = base.where(
            func.lower(User.email).like(like, escape="\\")
            | func.lower(func.coalesce(User.handle, "")).like(like, escape="\\")
            | func.lower(User.display_name).like(like, escape="\\")
        )
    if status:
        now = datetime.now(timezone.utc)
        active_susp = User.suspended_at.is_not(None) & (
            User.suspended_until.is_(None) | (User.suspended_until > now)
        )
        base = base.where(active_susp if status == "suspended" else ~active_susp)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.scalars(
        base.order_by(User.created_at.desc(), User.id).offset(page.offset).limit(page.page_size)
    )
    return page.wrap([user_row(u) for u in rows], int(total))


async def _target(db: AsyncSession, staff: User, user_id: uuid.UUID) -> User:
    user = await db.scalar(
        select(User).where(User.id == user_id, User.tenant_id == staff.tenant_id).with_for_update()
    )
    if user is None:
        raise NotFound("User not found")
    if user.id == staff.id:
        raise Forbidden("You cannot suspend yourself")
    if user.role == "admin":
        raise Forbidden("Admins cannot be suspended")
    if staff.role != "admin" and user.role != "buyer":
        raise Forbidden("Agents can only suspend buyers")
    return user


@router.post("/users/{user_id}/suspend")
async def suspend(
    user_id: uuid.UUID,
    body: SuspendIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    user = await _target(db, staff, user_id)
    now = datetime.now(timezone.utc)
    if body.until is not None and body.until <= now:
        raise Validation("until must be in the future")
    before = _state(user)
    user.suspended_at = now
    user.suspended_until = body.until
    user.suspend_reason = body.reason.strip()
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="user.suspend",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"user:{user.id}",
        before=before,
        after=_state(user),
        ip=client_ip(request),
        detail={"reason": user.suspend_reason},
        severity="warning",
    )
    await db.commit()
    await res.sessions.revoke_user_sessions(user.id)
    return user_row(user)


@router.post("/users/{user_id}/unsuspend")
async def unsuspend(
    user_id: uuid.UUID,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await _target(db, staff, user_id)
    before = _state(user)
    user.suspended_at = user.suspended_until = user.suspend_reason = None
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="user.unsuspend",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"user:{user.id}",
        before=before,
        after=_state(user),
        ip=client_ip(request),
    )
    await db.commit()
    return user_row(user)


def block_view(b: BlockedIP) -> dict:
    return {
        "id": str(b.id),
        "cidr": b.cidr,
        "note": b.note,
        "expires_at": iso(b.expires_at),
        "created_at": iso(b.created_at),
        "created_by": str(b.created_by) if b.created_by else None,
    }


@router.get("/blocked-ips")
async def list_blocked(
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base = select(BlockedIP).where(BlockedIP.tenant_id == staff.tenant_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.scalars(
        base.order_by(BlockedIP.created_at.desc(), BlockedIP.id)
        .offset(page.offset)
        .limit(page.page_size)
    )
    return page.wrap([block_view(b) for b in rows], int(total))


@router.post("/blocked-ips", status_code=201)
async def add_blocked(
    body: BlockIn,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    net = ipblock.parse_block_net(body.cidr)
    if net is None:
        raise Validation("cidr: invalid, or too broad (IPv4 /8, IPv6 /32 at most)")
    if body.expires_at is not None and body.expires_at <= datetime.now(timezone.utc):
        raise Validation("expires_at must be in the future")
    ip = client_ip(request)
    if ip_in_nets(ip, [net]):
        raise Validation("cidr: would block your own address")
    cidr = str(net)
    row = BlockedIP(
        tenant_id=staff.tenant_id,
        cidr=cidr,
        note=(body.note or "").strip() or None,
        expires_at=body.expires_at,
        created_by=staff.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("This range is already blocked") from None
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="blocked_ip.add",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"blocked_ip:{row.id}",
        after=block_view(row),
        ip=ip,
        severity="warning",
    )
    await db.commit()
    await _remirror(res, staff.tenant_id)
    return block_view(row)


@router.delete("/blocked-ips/{block_id}", status_code=204)
async def delete_blocked(
    block_id: uuid.UUID,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    row = await db.scalar(
        select(BlockedIP).where(BlockedIP.id == block_id, BlockedIP.tenant_id == staff.tenant_id)
    )
    if row is None:
        raise NotFound("Blocked range not found")
    before = block_view(row)
    await db.delete(row)
    await audit(
        db,
        tenant_id=staff.tenant_id,
        action="blocked_ip.remove",
        actor_id=staff.id,
        actor_role=staff.role,
        target=f"blocked_ip:{block_id}",
        before=before,
        ip=client_ip(request),
    )
    await db.commit()
    await _remirror(res, staff.tenant_id)
    return Response(status_code=204)


async def _remirror(res: Resources, tid: uuid.UUID) -> None:
    try:
        await ipblock.refresh(res, tid)
    except Exception:  # the Redis key expires (TTL) and is rebuilt from the DB
        await res.redis.delete(ipblock.cfg_key(tid))
