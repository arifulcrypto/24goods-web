"""Reseller center (buyer app) and admin APIs (review-v5 s7, s12)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    Page,
    client_ip,
    current_user,
    get_db,
    get_lang,
    get_page,
    get_res,
    require_admin,
)
from app.core.features import require_feature
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.models import User
from app.modules.common import turnstile_hook
from app.modules.content.site import tenant_settings
from app.modules.reseller import service
from app.modules.reseller.schemas import (
    AdminResellerPatch,
    ApplyIn,
    DecideIn,
    MePatch,
    PayoutIn,
    ProductPut,
)

router = APIRouter(prefix="/reseller", tags=["reseller"])


async def _ts(db: AsyncSession, user: User) -> dict:
    ts = await tenant_settings(db, user.tenant_id)
    require_feature(ts, "resellers")  # whole reseller center is off with the toggle
    return ts


@router.get("/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict | None:
    rcfg = section(await _ts(db, user), "resellers")
    r = await service.get_mine(db, user)
    return await service.me_view(db, r, rcfg) if r is not None else None


@router.post("/apply")
async def apply(
    body: ApplyIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    ts = await _ts(db, user)
    await enforce(res.redis, "reseller:apply", str(user.id), 10, 3600)
    await turnstile_hook.check(request, ts, "reseller_apply", body.turnstile_token)
    r, order, rcfg = await service.apply(
        db,
        res.settings,
        user,
        slug=body.slug,
        store_name=body.store_name,
        contact=body.contact,
        ip=client_ip(request),
    )
    out = await service.me_view(db, r, rcfg)
    out["signup_order"] = order  # pay it with POST /orders/{no}/pay; None when the fee is 0
    return out


@router.patch("/me")
async def patch_me(
    body: MePatch,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _ts(db, user)
    data = body.model_dump(exclude_unset=True)
    r, rcfg = await service.patch_me(db, user, data, client_ip(request))
    return await service.me_view(db, r, rcfg)


@router.get("/products")
async def products(
    q: str | None = Query(None, max_length=100),
    page: Page = Depends(get_page),
    lang: str = Depends(get_lang),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rcfg = section(await _ts(db, user), "resellers")
    r = await service.require_mine(db, user)
    return await service.list_products(db, r, rcfg, page, q, lang)


@router.put("/products/{product_id}", status_code=204)
async def put_product(
    product_id: uuid.UUID,
    body: ProductPut,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    rcfg = section(await _ts(db, user), "resellers")
    r = await service.require_mine(db, user, active=True)
    await service.put_product(db, user, r, rcfg, product_id, body.model_dump(exclude_unset=True))
    return Response(status_code=204)


@router.delete("/products/{product_id}", status_code=204)
async def reset_product(
    product_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> Response:
    await _ts(db, user)
    r = await service.require_mine(db, user, active=True)
    await service.reset_product(db, user, r, product_id)
    return Response(status_code=204)


@router.get("/orders")
async def orders(
    page: Page = Depends(get_page),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _ts(db, user)
    r = await service.require_mine(db, user)
    return await service.list_orders(db, r, page)


@router.get("/stats")
async def stats(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    rcfg = section(await _ts(db, user), "resellers")
    r = await service.require_mine(db, user)
    return await service.stats(db, r, rcfg)


@router.get("/payouts")
async def payouts(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[dict]:
    await _ts(db, user)
    r = await service.require_mine(db, user)
    return await service.list_payouts(db, r)


@router.post("/payouts")
async def request_payout(
    body: PayoutIn,
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await _ts(db, user)
    await enforce(res.redis, "reseller:payout", str(user.id), 10, 3600)
    return await service.request_payout(db, user, body.amount_minor, client_ip(request))


# ----------------------------------------------------------------------------- admin
admin = APIRouter(prefix="/admin", tags=["admin-resellers"])


@admin.get("/resellers")
async def admin_list(
    status: Literal["pending_payment", "active", "suspended"] | None = None,
    q: str | None = Query(None, max_length=100),
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.admin_list(db, staff, page, status, q)


@admin.get("/resellers/{rid}")
async def admin_get(
    rid: uuid.UUID, staff: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    return await service.admin_detail(db, staff, rid)


@admin.patch("/resellers/{rid}")
async def admin_patch(
    rid: uuid.UUID,
    body: AdminResellerPatch,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.admin_patch(
        db, staff, rid, body.model_dump(exclude_unset=True), client_ip(request)
    )


@admin.get("/payouts")
async def admin_payouts(
    status: Literal["requested", "paid", "rejected"] | None = None,
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.admin_payouts(db, staff, page, status)


@admin.post("/payouts/{payout_id}/decide")
async def admin_decide(
    payout_id: uuid.UUID,
    body: DecideIn,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.decide_payout(
        db, staff, payout_id, body.action, body.note, client_ip(request)
    )
