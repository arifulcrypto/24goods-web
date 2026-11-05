"""Order lookup (卡密查询): order no + access code, no session is ever created."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import accesscode
from app.core import pow as powmod
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_lang, get_res, get_tenant_id
from app.core.errors import NotFound
from app.core.ratelimit import blocked, enforce, hit
from app.core.resources import Resources
from app.models import Order
from app.modules.orders import service

router = APIRouter(prefix="/lookup", tags=["lookup"])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ORDER_NO = re.compile(r"^G[A-Z2-7]{12}$")
IP_LOOKUPS = (10, 3600)  # per IP per hour (each attempt counts)
IP_REVEALS = (30, 3600)
ORDER_FAILURES = (5, 3600)  # wrong codes per order per hour


class LookupIn(BaseModel):
    order_no: str = Field(max_length=40)
    access_code: str = Field(max_length=40)
    pow: powmod.PowIn | None = None


class RevealIn(BaseModel):
    order_no: str = Field(max_length=40)
    access_code: str = Field(max_length=40)


async def _authenticate(
    db: AsyncSession, res: Resources, tenant_id: uuid.UUID, order_no: str, access_code: str
) -> Order:
    """The order if the code matches; otherwise one generic ``not_found`` (no oracle).

    Failures are counted per order (5/h) whether or not the order exists.
    """
    no = (order_no or "").strip().upper()
    code = accesscode.normalize(access_code)
    key = f"{tenant_id}:{no}"
    await blocked(res.redis, "lookup:fail", key, *ORDER_FAILURES)
    order = None
    if code is not None and _ORDER_NO.match(no):
        order = await db.scalar(select(Order).where(Order.order_no == no))
    ok = order is not None and accesscode.matches(
        res.settings.lookup_secret(), order.order_no, code or "", order.access_code_hash
    )
    if not ok or order is None:
        await hit(res.redis, "lookup:fail", key, *ORDER_FAILURES)
        raise NotFound("Order not found or access code incorrect")
    return order


@router.post("", response_model=None)
async def lookup(
    body: LookupIn,
    request: Request,
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    await powmod.verify(res.settings, res.redis, "lookup", body.pow)
    ip = client_ip(request)
    await enforce(res.redis, "lookup:ip", ip or "-", *IP_LOOKUPS)
    order = await _authenticate(db, res, tenant_id, body.order_no, body.access_code)
    view = await service.one_view(db, order, order.buyer_id, lang)
    return JSONResponse(view, headers=NO_STORE)


@router.post("/reveal", response_model=None)
async def reveal(
    body: RevealIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    ip = client_ip(request)
    await enforce(res.redis, "lookup:reveal:ip", ip or "-", *IP_REVEALS)
    order = await _authenticate(db, res, tenant_id, body.order_no, body.access_code)
    await audit(
        db,
        tenant_id=order.tenant_id,
        action="lookup.reveal",
        target=f"order:{order.id}",
        ip=ip,
    )
    await db.commit()  # end the read transaction before the cross-service call
    cards = await service.reveal_for(res, order, order.buyer_id)
    return JSONResponse({"cards": cards}, headers=NO_STORE)
