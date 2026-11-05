from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import client_ip, get_db, get_res, get_tenant_id
from app.core.resources import Resources
from app.modules.orders.service import broadcast_order
from app.modules.payments.notify import handle_notify

router = APIRouter(prefix="/pay", tags=["payments"])

NOTIFY_PATH = "/api/v1/pay/notify/zpay"
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
    params = await _params(request)
    outcome = await handle_notify(db, res, tenant_id, params, client_ip(request))
    if outcome.order is not None and outcome.messages:
        await broadcast_order(res, db, outcome.order, outcome.messages)
    return PlainTextResponse(outcome.reply, status_code=200)


@router.get("/return/zpay")
async def return_zpay(request: Request) -> RedirectResponse:
    """Display only: never fulfils. Sends the browser to the order page."""
    order_no = request.query_params.get("out_trade_no", "")
    target = f"/orders/{order_no}" if _ORDER_NO.match(order_no) else "/orders"
    return RedirectResponse(target, status_code=302)
