from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, current_user, get_db, get_lang, get_res, get_tenant_id
from app.core.resources import Resources
from app.models import User
from app.modules.auth import service
from app.modules.auth.schemas import InstantIn, LoginIn, RegisterIn
from app.modules.common.views import user_view

router = APIRouter(prefix="/auth", tags=["auth"])


async def _start_session(
    request: Request, res: Resources, user: User, body: dict | None = None
) -> JSONResponse:
    # Rotation: any pre-existing session token is destroyed before a new one is issued.
    await res.sessions.delete(res.sessions.token_from(request.cookies))
    token = await res.sessions.create(user.id, user.tenant_id, user.role)
    resp = JSONResponse(user_view(user) if body is None else body)
    if body is not None:
        resp.headers["Cache-Control"] = "no-store"
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
    user = await service.register(db, res.redis, res.settings, tenant_id, body, client_ip(request))
    return await _start_session(request, res, user)


@router.post("/instant")
async def instant(
    body: InstantIn,
    request: Request,
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    """One-tap account for checkout. The password is returned exactly once."""
    user, password = await service.instant(
        db, res.redis, res.settings, tenant_id, body, client_ip(request), lang
    )
    return await _start_session(
        request,
        res,
        user,
        {"user": user_view(user), "credentials": {"login": user.handle, "password": password}},
    )


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    user = await service.login(db, res.redis, res.settings, tenant_id, body, client_ip(request))
    return await _start_session(request, res, user)


@router.post("/logout", status_code=204)
async def logout(request: Request, res: Resources = Depends(get_res)) -> Response:
    await res.sessions.delete(res.sessions.token_from(request.cookies))
    resp = Response(status_code=204)
    res.sessions.clear_cookie(resp)
    return resp


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return user_view(user)
