from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, client_ip, current_user, get_db, get_lang, get_page, get_res
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.storefront import Storefront, get_storefront
from app.models import User
from app.modules.challenges import service as challenge_service
from app.modules.orders import cancel as cancel_svc
from app.modules.orders import expedite as expedite_svc
from app.modules.orders import service
from app.modules.orders.riskctl import guard_order_create
from app.modules.orders.schemas import BuyerCancelIn, BuyNowIn, InputIn, PayIn

router = APIRouter(prefix="/orders", tags=["orders"])

ORDER_ACTIONS = (20, 60)  # per user per minute: buy-now, pay, quote-accept


@router.post("")
async def buy_now(
    body: BuyNowIn,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    await guard_order_create(res, db, user, client_ip(request))
    order, msg = await service.buy_now(
        db,
        res.settings,
        user,
        body.product_id,
        body.qty,
        client_ip(request),
        body.src,
        body.buyer_input,
        promo_code=body.promo_code,
        storefront=sf,
        variant_id=body.variant_id,
    )
    code = getattr(order, "access_code_plain", None)  # shown exactly once
    await service.broadcast_order(res, db, order, [msg])
    view = await service.one_view(db, order, user.id, lang)
    view["access_code"] = code
    return view


@router.get("")
async def list_orders(
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.list_own(db, user, page, lang)


@router.get("/{ref}")
async def get_order(
    ref: str,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    order = await service.get_owned(db, ref, user)
    view = await service.one_view(db, order, user.id, lang)
    await service.attach_service(db, user, [view])
    return view


@router.post("/{ref}/access-code", response_model=None)
async def rotate_access_code(
    ref: str,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    """Replace the order's access code (the old one stops working) and return the new one once."""
    await enforce(res.redis, "order:accesscode", str(user.id), 10, 3600)
    code = await service.rotate_access_code(db, res.settings, ref, user, client_ip(request))
    return JSONResponse(
        {"access_code": code}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )


@router.post("/{ref}/pay")
async def pay(
    ref: str,
    body: PayIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "order:pay", str(user.id), *ORDER_ACTIONS)

    async def initiate(expected: str | None) -> str:
        return await service.pay_url(
            db, res, ref, user, body.channel, ip=client_ip(request), expected_binding=expected
        )

    return {
        "pay_url": await challenge_service.pay(request, res, db, user, "order", ref, body, initiate)
    }


@router.post("/{ref}/reveal", response_model=None)
async def reveal(
    ref: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> JSONResponse:
    order = await service.get_owned(db, ref, user)
    await db.commit()  # end the read transaction before the cross-service call
    cards = await service.reveal(res, order, user)
    return JSONResponse(
        {"secrets": [c["code"] for c in cards], "cards": cards},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/{ref}/confirm")
async def confirm(
    ref: str,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    order, msg = await service.confirm(db, ref, user, client_ip(request))
    await service.broadcast_order(res, db, order, [msg])
    return await service.one_view(db, order, user.id, lang)


@router.post("/{ref}/cancel")
async def cancel(
    ref: str,
    request: Request,
    body: BuyerCancelIn | None = None,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Buyer cancels a pending order; inside a checkout this cancels the whole checkout."""
    uid = user.id  # the re-query path rolls back, which expires ORM instances
    await enforce(res.redis, "order:cancel", str(uid), *ORDER_ACTIONS)
    order = await service.get_owned(db, ref, user)
    await cancel_svc.cancel_group(
        db,
        res,
        order_id=order.id,
        actor=user,
        by="buyer",
        reason=body.reason if body else None,
        ip=client_ip(request),
    )
    await db.refresh(order)
    return await service.one_view(db, order, uid, lang)


@router.post("/{ref}/expedite")
async def expedite(
    ref: str,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Buyer nudges the seller about a paid manual order (30 min cooldown, max 3)."""
    await enforce(res.redis, "order:expedite", str(user.id), *ORDER_ACTIONS)
    uid = user.id
    order, msg = await expedite_svc.expedite(db, ref, user, client_ip(request))
    await db.commit()
    await service.broadcast_order(res, db, order, [msg] if msg else None)
    expedite_svc.alert_staff(res, order, str(await expedite_svc.count(db, order)))
    return await service.one_view(db, order, uid, lang)


@router.patch("/{ref}/input")
async def update_input(
    ref: str,
    body: InputIn,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "order:input", str(user.id), *ORDER_ACTIONS)
    order, msg = await service.update_input(db, ref, user, body.buyer_input, client_ip(request))
    await service.broadcast_order(res, db, order, [msg])
    return await service.one_view(db, order, user.id, lang)
