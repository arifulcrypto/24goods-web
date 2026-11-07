from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import notify_tg
from app.core.deps import (
    Page,
    client_ip,
    current_user,
    get_db,
    get_page,
    get_res,
    require_staff,
)
from app.core.errors import Forbidden
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import AfterSales, Message, Order, User
from app.modules.aftersales import service
from app.modules.aftersales.schemas import ActionIn, OpenIn
from app.modules.orders.service import broadcast_order
from app.modules.realtime.publish import publish_after_sales

router = APIRouter(tags=["after-sales"])


async def _publish(res: Resources, db: AsyncSession, t: AfterSales) -> dict:
    (staff_view,) = await service.admin_views(db, [t])
    buyer_view = await service.buyer_view(db, t)
    await publish_after_sales(res.redis, t.tenant_id, t.buyer_id, buyer_view, staff_view)
    return staff_view


@router.post("/orders/{ref}/after-sales")
async def open_after_sales(
    ref: str,
    body: OpenIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "aftersales:open", str(user.id), 10, 3600)
    t, order, msg = await service.open_ticket(db, user, ref, body, client_ip(request))
    await broadcast_order(res, db, order, [msg])
    await _publish(res, db, t)
    notify_tg.notify_bg(
        res,
        order.tenant_id,
        "after_sales",
        [f"Order {order.order_no}", order.title, f"Reason: {t.reason}"],
        link=notify_tg.admin_link(res, "after-sales"),
        once=(f"aftersales:{t.id}", 86400),
    )
    return await service.buyer_view(db, t)


# ------------------------------------------------------------------ admin
@router.get("/admin/after-sales")
async def list_after_sales(
    status: Literal["open", "processing", "replaced", "refunded", "rejected", "resolved"]
    | None = Query(None),
    page: Page = Depends(get_page),
    _: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.admin_list(db, page, status)


@router.get("/admin/after-sales/{ticket_id}")
async def get_after_sales(
    ticket_id: uuid.UUID, _: User = Depends(require_staff), db: AsyncSession = Depends(get_db)
) -> dict:
    return await service.admin_detail(db, ticket_id)


@router.post("/admin/after-sales/{ticket_id}/action")
async def after_sales_action(
    ticket_id: uuid.UUID,
    body: ActionIn,
    request: Request,
    staff: User = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    if body.action == "refund" and staff.role != "admin":
        raise Forbidden("Refunds require an admin")
    t, order, msgs = await service.act(db, staff, ticket_id, body, client_ip(request))
    await _after_commit(res, db, t, order, msgs)
    return await _publish(res, db, t)


async def _after_commit(
    res: Resources, db: AsyncSession, t: AfterSales, order: Order, msgs: list[Message]
) -> None:
    await broadcast_order(res, db, order, msgs)
