from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, current_user, get_db, get_res, get_tenant_id
from app.core.resources import Resources
from app.core.security import COOKIE_NAME
from app.models import User
from app.modules.auth import service
from app.modules.auth.schemas import LoginIn, RegisterIn
from app.modules.common.views import user_view

router = APIRouter(prefix="/auth", tags=["auth"])


async def _start_session(request: Request, res: Resources, user: User) -> JSONResponse:
    # Rotation: any pre-existing session token is destroyed before a new one is issued.
    await res.sessions.delete(request.cookies.get(COOKIE_NAME))
    token = await res.sessions.create(user.id, user.tenant_id, user.role)
    resp = JSONResponse(user_view(user))
    res.sessions.set_cookie(resp, token, user.role)
    return resp


@router.post("/register")
async def register(
    body: RegisterIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    user = await service.register(db, res.redis, tenant_id, body, client_ip(request))
    return await _start_session(request, res, user)


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    user = await service.login(db, res.redis, tenant_id, body, client_ip(request))
    return await _start_session(request, res, user)


@router.post("/logout", status_code=204)
async def logout(request: Request, res: Resources = Depends(get_res)) -> Response:
    await res.sessions.delete(request.cookies.get(COOKIE_NAME))
    resp = Response(status_code=204)
    res.sessions.clear_cookie(resp)
    return resp


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return user_view(user)
