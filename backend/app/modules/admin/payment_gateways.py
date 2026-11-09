"""Session-only, immutable EPay provider configuration. Secrets are write-only."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import safefetch, secretbox
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import Forbidden, InvalidState, NotFound, Validation
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.security import verify_password
from app.models import PaymentAttempt, PaymentGateway, PaymentTest, User
from app.modules.payments.gateways import (
    active_gateway,
    candidates,
    normalize_gateway,
    routing_mode,
    set_active,
    set_routing_mode,
)

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


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


@router.get("")
async def list_gateways(
    admin: User = Depends(session_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    tid = admin.tenant_id
    active = await active_gateway(db, tid)
    rows = list(
        await db.scalars(
            select(PaymentGateway)
            .where(PaymentGateway.tenant_id == tid)
            .order_by(PaymentGateway.priority, PaymentGateway.created_at)
            .limit(50)
        )
    )
    order = [c.id for c in await candidates(db, tid)]
    return {
        "routing_mode": await routing_mode(db, tid),
        "candidates": [str(i) for i in order],
        "items": [
            {
                "id": str(r.id),
                "label": r.label,
                "gateway": r.gateway,
                "merchant_id": r.merchant_id,
                "mode": r.mode,
                "configured": True,
                "active": active is not None and r.id == active.id,
                "enabled": r.enabled,
                "priority": r.priority,
                "in_use": bool(order) and order[0] == r.id,
                "last_ok_at": _iso(r.last_ok_at),
                "last_fail_at": _iso(r.last_fail_at),
                "last_fail_reason": r.last_fail_reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
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
        enabled=False,  # opt in: enable it (or activate) once the test payment works
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


class RoutingIn(BaseModel):
    mode: Literal["sticky", "priority"]


class OrderIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


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


class GatewayPatch(BaseModel):
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-1000, le=100000)


async def _own(db: AsyncSession, tid: uuid.UUID, ref: uuid.UUID) -> PaymentGateway:
    row = await db.scalar(
        select(PaymentGateway).where(PaymentGateway.id == ref, PaymentGateway.tenant_id == tid)
    )
    if row is None:
        raise NotFound("Gateway not found")
    return row


@router.put("/routing")
async def set_routing(
    body: RoutingIn,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:routing", str(admin.id), 60, 3600)
    old = await set_routing_mode(db, admin.tenant_id, body.mode)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="payment.routing_changed",
        actor_id=admin.id,
        actor_role=admin.role,
        before={"mode": old},
        after={"mode": body.mode},
        ip=client_ip(request),
    )
    await db.commit()
    return {"routing_mode": body.mode}


@router.put("/order")
async def set_order(
    body: OrderIn,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Priority from list position (10, 20, ...). The list must name every gateway exactly once."""
    await enforce(res.redis, "gateway:routing", str(admin.id), 60, 3600)
    tid = admin.tenant_id
    rows = {
        r.id: r
        for r in await db.scalars(
            select(PaymentGateway).where(PaymentGateway.tenant_id == tid).with_for_update()
        )
    }
    if len(set(body.ids)) != len(body.ids) or set(body.ids) != set(rows):
        raise Validation("The order must list every gateway exactly once")
    before = {str(i): rows[i].priority for i in body.ids}
    after = {}
    for n, gid in enumerate(body.ids, start=1):
        await db.execute(
            update(PaymentGateway)
            .where(PaymentGateway.id == gid, PaymentGateway.tenant_id == tid)
            .values(priority=n * 10)
        )
        after[str(gid)] = n * 10
    await audit(
        db,
        tenant_id=tid,
        action="payment.gateway_reordered",
        actor_id=admin.id,
        actor_role=admin.role,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await db.commit()
    return {"order": [str(i) for i in body.ids]}


@router.patch("/{ref}")
async def patch_gateway(
    ref: uuid.UUID,
    body: GatewayPatch,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:routing", str(admin.id), 60, 3600)
    if body.enabled is None and body.priority is None:
        raise Validation("Nothing to change")
    row = await _own(db, admin.tenant_id, ref)
    before = {"enabled": row.enabled, "priority": row.priority}
    values = body.model_dump(exclude_none=True)
    await db.execute(
        update(PaymentGateway)
        .where(PaymentGateway.id == ref, PaymentGateway.tenant_id == admin.tenant_id)
        .values(**values)
    )
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="payment.gateway_updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target="payment_gateway:" + str(ref),
        before=before,
        after={**before, **values},
        ip=client_ip(request),
    )
    await db.commit()
    return {"id": str(ref), **{**before, **values}}


@router.delete("/{ref}")
async def delete_gateway(
    ref: uuid.UUID,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Refused once any payment attempt used the gateway (its in-flight/settled history needs the
    row): disable it instead. A gateway that was only ever tested can be removed."""
    await enforce(res.redis, "gateway:config", str(admin.id), 10, 3600)
    tid = admin.tenant_id
    row = await _own(db, tid, ref)
    used = await db.scalar(
        select(exists().where(PaymentAttempt.tenant_id == tid, PaymentAttempt.provider_id == ref))
    )
    if used:
        raise InvalidState("This gateway has payment attempts; disable it instead of deleting it")
    paid_tests = await db.scalar(
        select(
            exists().where(
                PaymentTest.tenant_id == tid,
                PaymentTest.provider_id == ref,
                PaymentTest.status == "paid",
            )
        )
    )
    if paid_tests:
        raise InvalidState("This gateway has paid test payments; disable it instead of deleting")
    active = await active_gateway(db, tid)
    if active is not None and active.id == row.id:
        await set_active(db, tid, None)
    await db.execute(
        delete(PaymentTest).where(PaymentTest.provider_id == ref, PaymentTest.tenant_id == tid)
    )
    await db.execute(
        delete(PaymentGateway).where(PaymentGateway.id == ref, PaymentGateway.tenant_id == tid)
    )
    await audit(
        db,
        tenant_id=tid,
        action="payment.gateway_deleted",
        actor_id=admin.id,
        actor_role=admin.role,
        target="payment_gateway:" + str(ref),
        ip=client_ip(request),
    )
    await db.commit()
    return {"deleted": str(ref)}
