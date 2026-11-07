"""FastAPI dependencies: resources, tenant, DB session, user, language, pagination."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import tenant_session
from app.core.errors import Forbidden, Unauthorized
from app.core.i18n import resolve_lang
from app.core.resources import Resources
from app.core.security import STAFF_ROLES, SessionData
from app.core.suspend import enforce as enforce_not_suspended
from app.models import User


def get_res(request: Request) -> Resources:
    return request.app.state.res


async def get_tenant_id(request: Request, res: Resources = Depends(get_res)) -> uuid.UUID:
    tid = await res.tenants.resolve(request.headers.get("host"))
    request.state.tenant_id = tid
    return tid


async def get_db(
    tenant_id: uuid.UUID = Depends(get_tenant_id), res: Resources = Depends(get_res)
) -> AsyncIterator[AsyncSession]:
    """Tenant-pinned session. Handlers commit explicitly; anything else is rolled back."""
    async with tenant_session(res.maker, tenant_id) as session:
        yield session


def client_ip(request: Request) -> str | None:
    ip = getattr(request.state, "client_ip", None)
    if ip is None and request.client:
        ip = request.client.host
    return ip


def get_lang(request: Request) -> str:
    return resolve_lang(request)


async def get_session(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> SessionData | None:
    sess = await res.sessions.get(res.sessions.token_from(request.cookies))
    if sess is None or sess.tenant_id != tenant_id:
        return None  # sessions are bound to the tenant they were created on
    return sess


async def optional_user(
    sess: SessionData | None = Depends(get_session), db: AsyncSession = Depends(get_db)
) -> User | None:
    if sess is None:
        return None
    user = await db.scalar(select(User).where(User.id == sess.user_id))
    if user is None or user.status != "active":
        return None
    await enforce_not_suspended(db, user)
    return user


async def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise Unauthorized("Authentication required")
    return user


async def require_staff(user: User = Depends(current_user)) -> User:
    if user.role not in STAFF_ROLES:
        raise Forbidden("Staff only")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise Forbidden("Admin only")
    return user


@dataclass(frozen=True)
class Page:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    def wrap(self, items: list, total: int) -> dict:
        return {"items": items, "total": total, "page": self.page, "page_size": self.page_size}


def get_page(page: int = Query(1, ge=1, le=1000), page_size: int = Query(20, ge=1, le=100)) -> Page:
    return Page(page, page_size)
