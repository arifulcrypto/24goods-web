"""Session-only, immutable EPay provider configuration. Secrets are write-only."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import safefetch, secretbox
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import Forbidden, NotFound, Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.security import verify_password
from app.models import PaymentGateway, User
from app.modules.payments.gateways import active_gateway, normalize_gateway, set_active

router = APIRouter(prefix="/admin/payment-gateways", tags=["admin"])


async def session_admin(request: Request, user: User = Depends(require_admin)) -> User:
    if getattr(request.state, "api_token", None) is not None:
        raise Forbidden("An administrator session is required")
    return user


class GatewayIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    gateway: str = Field(max_length=500)
    merchant_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    secret: str = Field(min_length=16, max_length=256, repr=False, pattern=r"^[!-~]+$")
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    mode: Literal["qr", "redirect"] = "qr"


@router.get("")
async def list_gateways(
    admin: User = Depends(session_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    active = await active_gateway(db, admin.tenant_id)
    rows = await db.scalars(
        select(PaymentGateway)
        .where(PaymentGateway.tenant_id == admin.tenant_id)
        .order_by(PaymentGateway.created_at.desc())
        .limit(50)
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "label": r.label,
                "gateway": r.gateway,
                "merchant_id": r.merchant_id,
                "mode": r.mode,
                "configured": True,
                "active": active is not None and r.id == active.id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("", status_code=201)
async def create_gateway(
    body: GatewayIn,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:config", str(admin.id), 10, 3600)
    if not (await verify_password(admin.pw_hash, body.current_password))[0]:
        raise Forbidden("Current password required")
    if not body.label.strip():
        raise Validation("A provider label is required")
    base = normalize_gateway(body.gateway)
    await safefetch.vet_url(base)
    ref = uuid.uuid4()
    row = PaymentGateway(
        id=ref,
        tenant_id=admin.tenant_id,
        label=body.label.strip(),
        gateway=base,
        merchant_id=body.merchant_id,
        mode=body.mode,
        created_by=admin.id,
        key_ciphertext=secretbox.seal(
            res.settings.settings_secret(),
            admin.tenant_id,
            "payment_gateway:" + str(ref),
            body.secret,
        ),
    )
    db.add(row)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="payment.gateway_created",
        actor_id=admin.id,
        actor_role=admin.role,
        target="payment_gateway:" + str(ref),
        ip=client_ip(request),
        detail={"protocol": "epay_md5_v1"},
    )
    await db.commit()
    return {"id": str(ref), "configured": True}


class ActivateIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    ownership_confirmed: bool = False


@router.post("/{ref}/activate")
async def activate_gateway(
    ref: uuid.UUID,
    body: ActivateIn,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:activate", str(admin.id), 10, 3600)
    if (
        not body.ownership_confirmed
        or not (await verify_password(admin.pw_hash, body.current_password))[0]
    ):
        raise Forbidden("Fresh authentication and merchant ownership confirmation required")
    row = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == ref, PaymentGateway.tenant_id == admin.tenant_id
        )
    )
    if row is None:
        raise NotFound("Gateway not found")
    await set_active(db, admin.tenant_id, ref)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="payment.gateway_activated",
        actor_id=admin.id,
        actor_role=admin.role,
        target="payment_gateway:" + str(ref),
        ip=client_ip(request),
    )
    await db.commit()
    return {"active": str(ref)}
