"""Public site endpoints: ``/site``, ``/articles``, ``/pow/challenge``."""

from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pow as powmod
from app.core.deps import Page, client_ip, get_db, get_lang, get_page, get_res, get_tenant_id
from app.core.ratelimit import count, enforce
from app.core.resources import Resources
from app.modules.auth.service import net_of
from app.modules.content import service

router = APIRouter(tags=["content"])


@router.get("/site")
async def site(
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    return await service.public_site(db, tenant_id, lang, res.settings.brand_name)


@router.get("/pow/challenge")
async def pow_challenge(
    request: Request,
    scope: Literal["instant", "register", "lookup", "login"] = Query(...),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    await enforce(res.redis, "pow:challenge", client_ip(request) or "-", 60, 60)
    extra = 0
    if scope == "instant":
        s = res.settings
        today, _ = await count(res.redis, "instant:net:d", net_of(client_ip(request)), 86400)
        extra = 6 if today > s.instant_ramp_2 else 4 if today > s.instant_ramp_1 else 0
    return JSONResponse(
        powmod.issue(res.settings, scope, extra_bits=extra), headers={"Cache-Control": "no-store"}
    )


@router.get("/articles")
async def articles(
    category: str | None = Query(None, max_length=32),
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.list_public(db, lang, page, category)


@router.get("/articles/{slug}")
async def article(
    slug: str,
    request: Request,
    lang: str = Depends(get_lang),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    viewer = hashlib.sha256((client_ip(request) or "-").encode()).hexdigest()[:16]
    return await service.get_public(db, res.redis, lang, slug[:64], viewer)
