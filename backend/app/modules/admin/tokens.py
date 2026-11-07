"""Staff API tokens: ``/admin/api-tokens`` (admin session only; a token can never manage tokens)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import apitoken
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import NotFound
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import ApiToken, User
from app.modules.common.views import iso

router = APIRouter(prefix="/admin/api-tokens", tags=["admin-api-tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = Field(min_length=1, max_length=len(apitoken.SCOPES))
    expires_in_days: int = Field(apitoken.DEFAULT_TTL_DAYS, ge=1, le=apitoken.MAX_TTL_DAYS)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        if any(s not in apitoken.SCOPES for s in v):
            raise ValueError("unknown scope")
        return sorted(set(v))


def view(t: ApiToken) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": str(t.id),
        "name": t.name,
        "prefix": t.prefix,
        "scopes": list(t.scopes),
        "user_id": str(t.user_id),
        "expires_at": iso(t.expires_at),
        "last_used_at": iso(t.last_used_at),
        "last_used_ip": t.last_used_ip,
        "revoked_at": iso(t.revoked_at),
        "created_at": iso(t.created_at),
        "status": "revoked"
        if t.revoked_at
        else "expired"
        if t.expires_at and t.expires_at <= now
        else "active",
    }


@router.get("")
async def list_tokens(
    admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await db.scalars(
        select(ApiToken)
        .where(ApiToken.tenant_id == admin.tenant_id)
        .order_by(ApiToken.created_at.desc())
        .limit(200)
    )
    return {"items": [view(t) for t in rows], "scopes": list(apitoken.SCOPES)}


@router.post("", status_code=201)
async def create_token(
    body: TokenIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "apitoken:create", str(admin.id), 20, 3600)
    token, prefix, digest = apitoken.new_token()
    row = ApiToken(
        tenant_id=admin.tenant_id,
        user_id=admin.id,
        name=body.name,
        prefix=prefix,
        token_hash=digest,
        scopes=body.scopes,
        expires_at=apitoken.clamp_expiry(body.expires_in_days),
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="api_token.create",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"api_token:{row.id}",
        after={"name": row.name, "prefix": prefix, "scopes": body.scopes},
        ip=client_ip(request),
    )
    await db.commit()
    await db.refresh(row)
    return {**view(row), "token": token}  # the only time the secret is ever returned


@router.post("/{token_id}/revoke")
async def revoke_token(
    token_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.tenant_id == admin.tenant_id)
    )
    if row is None:
        raise NotFound("Token not found")
    if row.revoked_at is None:
        await db.execute(
            update(ApiToken)
            .where(ApiToken.id == row.id, ApiToken.tenant_id == admin.tenant_id)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await audit(
            db,
            tenant_id=admin.tenant_id,
            action="api_token.revoke",
            actor_id=admin.id,
            actor_role=admin.role,
            target=f"api_token:{row.id}",
            before={"name": row.name, "prefix": row.prefix},
            ip=client_ip(request),
        )
        await db.commit()
        await db.refresh(row)
    return view(row)
