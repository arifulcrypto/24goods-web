from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import notify_tg
from app.core.deps import client_ip, current_user, get_db, get_res, get_tenant_id
from app.core.errors import NotFound
from app.core.ratelimit import hit
from app.core.resources import Resources
from app.models import Checkout, Order, User
from app.modules.orders.service import broadcast_order
from app.modules.payments import usdt as usdt_svc
from app.modules.payments.notify import handle_notify
from app.modules.payments.notify_bepusdt import handle_bepusdt_notify
from app.modules.payments.settle import SUCCESS
from app.payments import bepusdt

router = APIRouter(prefix="/pay", tags=["payments"])

NOTIFY_PATH = "/api/v1/pay/notify/zpay"
NOTIFY_BEPUSDT_PATH = "/api/v1/pay/notify/bepusdt"
_ORDER_NO = re.compile(r"^G[A-Z2-7]{12}$")
MAX_PARAMS = 40


async def _params(request: Request) -> dict[str, str]:
    params: dict[str, str] = {}
    if request.method == "POST":
        form = await request.form(max_fields=MAX_PARAMS, max_files=0)
        for k, v in form.multi_items():
            if isinstance(v, str):
                params.setdefault(k, v)
    for k, v in request.query_params.multi_items():
        params.setdefault(k, v)
    return dict(list(params.items())[:MAX_PARAMS])


@router.api_route("/notify/zpay", methods=["GET", "POST"], response_class=PlainTextResponse)
async def notify_zpay(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> PlainTextResponse:
    ip = client_ip(request)
    allowed, _ = await hit(res.redis, "notify:ip", ip or "-", res.settings.notify_rate_per_min, 60)
    if not allowed:  # flood: cheap refusal before parsing/verifying; gateways retry later
        return PlainTextResponse("fail", status_code=200)
    params = await _params(request)
    outcome = await handle_notify(db, res, tenant_id, params, ip)
    touched = outcome.orders or ([outcome.order] if outcome.order is not None else [])
    for o in touched:
        await broadcast_order(
            res,
            db,
            o,
            [m for m in outcome.messages if m and m.conversation_id == o.conversation_id],
        )
    notify_tg.outcome_events(res, touched)
    return PlainTextResponse(outcome.reply, status_code=200)


@router.post("/notify/bepusdt", response_class=PlainTextResponse)
async def notify_bepusdt(
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> PlainTextResponse:
    """BEpusdt JSON notify (internal network). Body ``ok`` ONLY for a processed paid notify
    (or a harmless waiting/expired ping / duplicate); anything else gets ``fail`` so the gateway
    retries. The pipeline is documented in :mod:`app.modules.payments.notify_bepusdt`."""
    ip = client_ip(request)
    try:
        params = bepusdt.parse_json(await request.body())
    except ValueError:
        return PlainTextResponse("fail", status_code=200)
    if not res.bepusdt.configured or not res.bepusdt.verify_signature(params):
        # unsigned noise: cheap refusal, rate limited per source IP
        allowed, _ = await hit(
            res.redis, "notify:ip", ip or "-", res.settings.notify_rate_per_min, 60
        )
        if not allowed:
            return PlainTextResponse("fail", status_code=200)
    outcome = await handle_bepusdt_notify(db, res, tenant_id, params, ip)
    touched = outcome.orders or ([outcome.order] if outcome.order is not None else [])
    if outcome.reply == SUCCESS:
        for o in touched:
            await broadcast_order(
                res,
                db,
                o,
                [m for m in outcome.messages if m and m.conversation_id == o.conversation_id],
            )
        notify_tg.outcome_events(res, touched)
    return PlainTextResponse("ok" if outcome.reply in (SUCCESS, "ok") else "fail", status_code=200)


@router.get("/usdt/{no}")
async def usdt_attempt(
    no: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Our own USDT pay page data: network, address, exact USDT amount, countdown, QR and the
    order/checkout status (the page polls this). Owner only; other tenants/buyers get 404."""
    no = no.upper()[:16]
    if no.startswith("C"):
        co = await db.scalar(
            select(Checkout).where(Checkout.checkout_no == no, Checkout.tenant_id == user.tenant_id)
        )
        if co is None or co.buyer_id != user.id:
            raise NotFound("Payment not found")
        status, channel = co.status, co.channel
    else:
        order = await db.scalar(
            select(Order).where(Order.order_no == no, Order.tenant_id == user.tenant_id)
        )
        if order is None or order.buyer_id != user.id:
            raise NotFound("Payment not found")
        status, channel = order.status, order.channel
    att = await usdt_svc.load_attempt(res, no)
    if att is None or att.get("channel") != channel:
        raise NotFound("Payment not found")
    return usdt_svc.attempt_view(att, status)


@router.get("/return/zpay")
async def return_zpay(request: Request) -> RedirectResponse:
    """Display only: never fulfils. Sends the browser to the order page."""
    order_no = request.query_params.get("out_trade_no", "")
    target = f"/orders/{order_no}" if _ORDER_NO.match(order_no) else "/orders"  # checkout -> list
    return RedirectResponse(target, status_code=302)
