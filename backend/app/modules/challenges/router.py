"""Public configuration-aware challenge endpoints; payment issuance verifies ownership."""

from __future__ import annotations

import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_db, get_res, get_tenant_id, optional_user
from app.core.pow import PowIn
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import User
from app.modules.challenges import service
from app.modules.content.site import tenant_settings

router = APIRouter(prefix="/security", tags=["security-challenges"])


class ChallengeIn(BaseModel):
    action: Literal["payment_initiation", "site_visit"]
    target: Literal["order", "checkout"] | None = None
    ref: str | None = Field(None, max_length=64)
    channel: Literal["zpay_alipay", "usdt_arbitrum", "usdt_xlayer", "usdt_trc20"] | None = None


class ProofIn(BaseModel):
    challenge_id: str = Field(max_length=64)
    pow: PowIn | None = None
    turnstile_token: str | None = Field(None, max_length=2048)


def set_cookie(response: Response, res: Resources, kind: str, value: str, ttl: int) -> None:
    response.set_cookie(
        service.cookie_name(res, kind),
        value,
        max_age=ttl,
        path="/",
        secure=res.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/challenges")
async def issue(
    body: ChallengeIn,
    request: Request,
    response: Response,
    tenant: uuid.UUID = Depends(get_tenant_id),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(
        res.redis, "pow:gate:issue", str(tenant) + ":" + (client_ip(request) or "-"), 30, 60
    )
    browser = service.browser_binding(request, res)
    if body.action == "site_visit" and not browser:
        browser = secrets.token_hex(32)
        set_cookie(response, res, "visitor", browser, 86400)
    response.headers["Cache-Control"] = "no-store"
    return await service.issue(
        request,
        res,
        db,
        tenant,
        body,
        user if body.action == "payment_initiation" else None,
        browser,
    )


@router.get("/site-clearance")
async def status(
    request: Request,
    response: Response,
    tenant: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    ts = await tenant_settings(db, tenant)
    required = service.policy(ts)["site_visit"]
    response.headers["Cache-Control"] = "no-store"
    return {
        "required": required,
        "cleared": not required or service.clearance(request, res, tenant, ts),
    }


@router.post("/site-clearance", status_code=204)
async def clear(
    body: ProofIn,
    request: Request,
    tenant: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> Response:
    await enforce(
        res.redis, "pow:gate:verify", str(tenant) + ":" + (client_ip(request) or "-"), 30, 60
    )
    ts = await tenant_settings(db, tenant)
    response = Response(status_code=204)
    if not service.policy(ts)["site_visit"]:
        response.headers["Cache-Control"] = "no-store"
        return response
    browser = service.browser_binding(request, res)
    if not browser:
        raise service.ChallengeInvalid("A browser-bound challenge is required")
    expected = service.binding(request, res, tenant, ts, "site_visit", browser, None, None)
    await db.commit()
    key, raw = await service.proof(request, res, tenant, ts, body, expected)
    # Atomic compare-and-consume after proof: stale verifiers cannot consume a replacement.
    consumed = await res.redis.eval(
        "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        "return redis.call('DEL', KEYS[1]) end return 0",
        1,
        key,
        raw,
    )
    if not consumed:
        raise service.ChallengeInvalid("Challenge already used")
    set_cookie(
        response,
        res,
        "clearance",
        service.sign_clearance(request, res, tenant, ts, browser),
        service.CLEARANCE_TTL_S,
    )
    return response
