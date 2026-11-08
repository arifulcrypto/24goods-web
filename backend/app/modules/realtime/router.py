"""SSE endpoints. Authentication uses a short-lived DB session that is closed before
streaming starts, so long-lived streams never pin pooled connections."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core import suspend
from app.core.db import tenant_session
from app.core.deps import client_ip, get_res, get_session, get_tenant_id
from app.core.errors import Forbidden, Unauthorized
from app.core.resources import Resources
from app.core.security import STAFF_ROLES, SessionData
from app.models import User
from app.modules.realtime.hub import sse_events
from app.modules.realtime.publish import public_channel, staff_channel, user_channel

router = APIRouter(tags=["realtime"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


async def _stream_user(res: Resources, sess: SessionData | None, tenant_id: uuid.UUID) -> User:
    if sess is None:
        raise Unauthorized("Authentication required")
    async with tenant_session(res.maker, tenant_id) as db:
        user = await db.scalar(
            select(User).where(User.id == sess.user_id, User.tenant_id == tenant_id)
        )
    if user is None or user.status != "active":
        raise Unauthorized("Authentication required")
    if suspend.is_suspended(user):
        raise suspend.suspended_error(user)
    return user


def _alive(
    res: Resources, request: Request, tenant_id: uuid.UUID, user: User
) -> Callable[[], Awaitable[bool]]:
    token = res.sessions.token_from(request.cookies)
    user_id, role = user.id, user.role

    async def check() -> bool:
        sess = await res.sessions.get(token)
        if sess is None or sess.tenant_id != tenant_id or sess.user_id != user_id:
            return False
        async with tenant_session(res.maker, tenant_id) as db:
            current = await db.scalar(
                select(User).where(User.id == user_id, User.tenant_id == tenant_id)
            )
        return bool(
            current is not None
            and current.status == "active"
            and current.role == role
            and not suspend.is_suspended(current)
        )

    return check


@router.get("/stream")
async def stream(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    sess: SessionData | None = Depends(get_session),
    res: Resources = Depends(get_res),
) -> StreamingResponse:
    user = await _stream_user(res, sess, tenant_id)
    sub = res.hub.subscribe(
        [user_channel(user.id), public_channel(tenant_id)],
        tenant=str(tenant_id),
        account=str(user.id),
        ip=client_ip(request) or "-",
    )
    return StreamingResponse(
        sse_events(res.hub, sub, alive=_alive(res, request, tenant_id, user)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/admin/stream")
async def admin_stream(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    sess: SessionData | None = Depends(get_session),
    res: Resources = Depends(get_res),
) -> StreamingResponse:
    user = await _stream_user(res, sess, tenant_id)
    if user.role not in STAFF_ROLES:
        raise Forbidden("Staff only")
    sub = res.hub.subscribe(
        [staff_channel(tenant_id)],
        tenant=str(tenant_id),
        account=str(user.id),
        ip=client_ip(request) or "-",
    )
    return StreamingResponse(
        sse_events(res.hub, sub, alive=_alive(res, request, tenant_id, user)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
