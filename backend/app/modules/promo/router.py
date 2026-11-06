"""Promo endpoints: buyer ``POST /promo/validate`` and admin ``/admin/promo-codes``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    Page,
    client_ip,
    get_db,
    get_page,
    get_res,
    get_tenant_id,
    optional_user,
    require_admin,
)
from app.core.features import require_feature
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.storefront import Storefront, get_storefront
from app.models import PromoCode, PromoRedemption, User
from app.modules.common import turnstile_hook
from app.modules.content.site import tenant_settings
from app.modules.promo import service
from app.modules.promo.schemas import PromoIn, PromoPatch, ValidateIn

router = APIRouter(tags=["promo"])


@router.post("/promo/validate")
async def validate(
    body: ValidateIn,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
    sf: Storefront | None = Depends(get_storefront),
) -> dict:
    ident = str(user.id) if user is not None else (client_ip(request) or "-")
    await enforce(res.redis, "promo:validate", ident, 20, 60)
    ts = await tenant_settings(db, tenant_id)
    require_feature(ts, "promo_codes")
    await turnstile_hook.check(request, ts, "promo_validate", body.turnstile_token)
    if sf is not None:  # promo is not applicable on reseller storefronts in v5
        raise service.PromoError("promo_not_applicable")
    lines = await service.lines_for(db, tenant_id, [(i.product_id, i.qty) for i in body.items])
    try:
        ev = await service.evaluate(
            db,
            tenant_id=tenant_id,
            user=user,
            code=body.code,
            lines=lines,
            currency="CNY",
            lock=False,
        )
    except service.PromoError:
        if user is None:  # anonymous: one opaque error, no code-existence oracle
            raise service.PromoError("promo_invalid") from None
        raise
    return {
        "code": ev.promo.code,
        "discount_minor": ev.discount_minor,
        "eligible_subtotal_minor": ev.eligible_minor,
        "description": service.describe(ev.promo),
    }


# ----------------------------------------------------------------------------- admin
admin = APIRouter(prefix="/admin/promo-codes", tags=["admin-promo"])


@admin.get("")
async def admin_list(
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    base = select(PromoCode).where(PromoCode.tenant_id == staff.tenant_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await db.scalars(
        base.order_by(PromoCode.created_at.desc(), PromoCode.id)
        .offset(page.offset)
        .limit(page.page_size)
    )
    return page.wrap([service.admin_view(p) for p in rows], int(total))


@admin.get("/generate")
async def generate(_: User = Depends(require_admin)) -> dict:
    return {"code": service.generate_code()}


@admin.post("")
async def admin_create(
    body: PromoIn,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.create(db, staff, body.model_dump(), client_ip(request))


@admin.patch("/{promo_id}")
async def admin_patch(
    promo_id: uuid.UUID,
    body: PromoPatch,
    request: Request,
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.update_promo(
        db, staff, promo_id, body.model_dump(exclude_unset=True), client_ip(request)
    )


@admin.get("/{promo_id}/redemptions")
async def admin_redemptions(
    promo_id: uuid.UUID,
    page: Page = Depends(get_page),
    staff: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    exists = await db.scalar(
        select(PromoCode.id).where(PromoCode.id == promo_id, PromoCode.tenant_id == staff.tenant_id)
    )
    if exists is None:
        raise service.NotFound("Promo code not found")
    base = select(PromoRedemption).where(
        PromoRedemption.promo_code_id == promo_id, PromoRedemption.tenant_id == staff.tenant_id
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = list(
        await db.scalars(
            base.order_by(PromoRedemption.created_at.desc(), PromoRedemption.id)
            .offset(page.offset)
            .limit(page.page_size)
        )
    )
    users = (
        {
            u.id: u
            for u in await db.scalars(
                select(User).where(
                    User.id.in_({r.user_id for r in rows}), User.tenant_id == staff.tenant_id
                )
            )
        }
        if rows
        else {}
    )
    return page.wrap(
        [
            {
                "id": str(r.id),
                "user": {
                    "id": str(r.user_id),
                    "email": users[r.user_id].email if r.user_id in users else "",
                },
                "order_id": str(r.order_id) if r.order_id else None,
                "checkout_id": str(r.checkout_id) if r.checkout_id else None,
                "discount_minor": int(r.discount_minor),
                "status": r.status,
                "created_at": r.created_at.isoformat().replace("+00:00", "Z"),
            }
            for r in rows
        ],
        int(total),
    )
