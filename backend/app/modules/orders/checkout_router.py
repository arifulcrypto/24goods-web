"""Cart quote, checkout and checkout pay/cancel endpoints (docs/review-v4.md §2, §12)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    client_ip,
    current_user,
    get_db,
    get_lang,
    get_res,
    get_tenant_id,
    optional_user,
)
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.storefront import Storefront, get_storefront
from app.models import User
from app.modules.orders import cancel as cancel_svc
from app.modules.orders import checkout as svc
from app.modules.orders.schemas import BuyerCancelIn, CartQuoteIn, CheckoutIn, PayIn

router = APIRouter(tags=["checkout"])

ACTIONS = (20, 60)  # per user per minute


@router.post("/cart/quote")
async def cart_quote(
    body: CartQuoteIn,
    request: Request,
    lang: str = Depends(get_lang),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    ident = client_ip(request) or (str(user.id) if user is not None else None)
    if ident is not None:  # no IP and no session: no shared bucket
        await enforce(res.redis, "cart:quote", ident, 60, 60)
    if body.promo_code and body.promo_code.strip():
        # a quote with a code is a code oracle: it shares the /promo/validate bucket
        await enforce(
            res.redis,
            "promo:validate",
            str(user.id) if user is not None else (ident or "-"),
            20,
            60,
        )
    return await svc.quote_cart(
        db, tenant_id, body.items, lang, promo_code=body.promo_code, user=user, storefront=sf
    )


@router.post("/checkout")
async def checkout(
    body: CheckoutIn,
    request: Request,
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    await enforce(res.redis, "order:create", str(user.id), *ACTIONS)
    out = await svc.create_checkout(
        db,
        res.settings,
        user,
        body.items,
        body.source,
        client_ip(request),
        lang,
        promo_code=body.promo_code,
        storefront=sf,
    )
    return out


@router.get("/checkouts/{checkout_no}")
async def get_checkout(
    checkout_no: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    co = await svc.get_owned_checkout(db, checkout_no, user)
    (view,) = await svc.checkout_views(db, [co])
    return view


@router.post("/checkouts/{checkout_no}/pay")
async def pay(
    checkout_no: str,
    body: PayIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "order:pay", str(user.id), *ACTIONS)
    return {"pay_url": await svc.pay_checkout(db, res, checkout_no, user, body.channel)}


@router.post("/checkouts/{checkout_no}/cancel")
async def cancel(
    checkout_no: str,
    request: Request,
    body: BuyerCancelIn | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "order:cancel", str(user.id), *ACTIONS)
    co = await svc.get_owned_checkout(db, checkout_no, user)
    await cancel_svc.cancel_group(
        db,
        res,
        checkout_id=co.id,
        actor=user,
        by="buyer",
        reason=body.reason if body else None,
        ip=client_ip(request),
    )
    await db.refresh(co)
    (view,) = await svc.checkout_views(db, [co])
    return view
