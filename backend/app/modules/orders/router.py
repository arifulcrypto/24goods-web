from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, client_ip, current_user, get_db, get_page, get_res
from app.core.resources import Resources
from app.models import User
from app.modules.orders import service
from app.modules.orders.schemas import BuyNowIn, PayIn

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("")
async def buy_now(
    body: BuyNowIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await service.buy_now(
        db, res.settings, user, body.product_id, body.qty, client_ip(request)
    )
    await service.broadcast_order(res, db, order, [msg])
    return await service.one_view(db, order, user.id)


@router.get("")
async def list_orders(
    page: Page = Depends(get_page),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.list_own(db, user, page)


@router.get("/{ref}")
async def get_order(
    ref: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    order = await service.get_owned(db, ref, user)
    return await service.one_view(db, order, user.id)


@router.post("/{ref}/pay")
async def pay(
    ref: str,
    body: PayIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    return {"pay_url": await service.pay_url(db, res, ref, user, body.channel)}


@router.post("/{ref}/reveal")
async def reveal(
    ref: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order = await service.get_owned(db, ref, user)
    await db.commit()  # end the read transaction before the cross-service call
    return {"secrets": await service.reveal(res, order, user)}


@router.post("/{ref}/confirm")
async def confirm(
    ref: str,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await service.confirm(db, ref, user, client_ip(request))
    await service.broadcast_order(res, db, order, [msg])
    return await service.one_view(db, order, user.id)
