from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    client_ip,
    current_user,
    forbid_custom_host,
    get_db,
    get_lang,
    get_res,
    get_tenant_id,
)
from app.core.errors import ApiError
from app.core.features import require_feature
from app.core.ratelimit import enforce, hit
from app.core.resources import Resources
from app.core.security import STAFF_ROLES
from app.core.settings_v5 import section
from app.core.turnstile import require_turnstile
from app.models import User
from app.modules.auth import codes, service
from app.modules.auth.schemas import (
    EmailCodeIn,
    InstantIn,
    LoginIn,
    PasswordResetIn,
    RegisterIn,
)
from app.modules.common.views import user_view
from app.modules.content.site import tenant_settings
from app.modules.operations.service import record_event


class TermsRequired(ApiError):
    """Accept the terms to register"""

    status, code = 422, "terms_required"


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
    ts = await tenant_settings(db, tenant_id)
    require_feature(ts, "registration")
    await require_turnstile(request, ts, "register", body.turnstile_token)
    if section(ts, "legal")["enabled"] and not body.agree_terms:
        raise TermsRequired("You must accept the terms to register")
    verified = False
    if section(ts, "security")["email_verify_on_register"]:
        await enforce(res.redis, "register:code:ip", client_ip(request) or "-", 10, 60)
        if not await codes.verify(
            res.redis, res.settings, tenant_id, body.email, "register", body.email_code
        ):
            raise codes.InvalidCode("The verification code is invalid or expired")
        verified = True
    user = await service.register(
        db, res.redis, res.settings, tenant_id, body, client_ip(request), email_verified=verified
    )
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
    ts = await tenant_settings(db, tenant_id)
    require_feature(ts, "guest_checkout")
    await require_turnstile(request, ts, "instant_account", body.turnstile_token)
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
    await require_turnstile(
        request, await tenant_settings(db, tenant_id), "login", body.turnstile_token
    )
    try:
        user = await service.login(db, res.redis, res.settings, tenant_id, body, client_ip(request))
    except ApiError:
        sampled, _ = await hit(
            res.redis, "login:denied-log", str(tenant_id) + ":" + (client_ip(request) or "-"), 1, 60
        )
        budget, _ = await hit(res.redis, "login:denied-log-budget", str(tenant_id), 8, 60)
        if sampled and budget:
            await record_event(
                db,
                tenant_id=tenant_id,
                environment=res.settings.env,
                family="login",
                action="auth.login",
                outcome="denied",
                reason="authentication_rejected",
                ip=client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
            await db.commit()
        raise
    await record_event(
        db,
        tenant_id=tenant_id,
        environment=res.settings.env,
        family="login",
        action="auth.login",
        outcome="success",
        reason="authenticated",
        actor_id=user.id,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    if user.role in STAFF_ROLES:
        await forbid_custom_host(request, db, tenant_id)
    return await _start_session(request, res, user)


@router.post("/email-code", status_code=204)
async def email_code(
    body: EmailCodeIn,
    request: Request,
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    """Always 204 when email is on (no account enumeration); 403 when email is off."""
    ts = await tenant_settings(db, tenant_id)
    await enforce(res.redis, "ecode:ip", client_ip(request) or "-", 10, 3600)
    action = "register" if body.purpose == "register" else "password_reset"
    await require_turnstile(request, ts, action, body.turnstile_token)
    await codes.request_code(db, res, tenant_id, ts, body.email, body.purpose, lang)
    return Response(status_code=204)


@router.post("/password-reset", status_code=204)
async def password_reset(
    body: PasswordResetIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    """The emailed code is the proof here (it was gated by Turnstile when it was requested)."""
    await codes.reset_password(
        db, res, tenant_id, body.email, body.code, body.new_password, client_ip(request)
    )
    return Response(status_code=204)


@router.post("/logout", status_code=204)
async def logout(request: Request, res: Resources = Depends(get_res)) -> Response:
    await res.sessions.delete(res.sessions.token_from(request.cookies))
    resp = Response(status_code=204)
    res.sessions.clear_cookie(resp)
    return resp


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return user_view(user)
