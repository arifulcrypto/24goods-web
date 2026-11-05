from __future__ import annotations

import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, Unauthorized
from app.core.ratelimit import enforce, ident_hash
from app.core.security import hash_password, verify_password
from app.models import User
from app.modules.auth.schemas import LoginIn, RegisterIn, normalize_email

GENERIC_LOGIN_ERROR = "Invalid email or password"


async def register(
    db: AsyncSession, redis: Redis, tenant_id: uuid.UUID, body: RegisterIn, ip: str | None
) -> User:
    await enforce(redis, "register:ip", ip or "-", 5, 60)
    user = User(
        tenant_id=tenant_id,
        email=body.email,
        pw_hash=await hash_password(body.password),
        role="buyer",
        display_name=body.display_name,
        locale=body.locale,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("Email already registered") from None
    await audit(
        db,
        tenant_id=tenant_id,
        action="auth.register",
        actor_id=user.id,
        actor_role="buyer",
        target=f"user:{user.id}",
        ip=ip,
    )
    await db.commit()
    return user


async def login(
    db: AsyncSession, redis: Redis, tenant_id: uuid.UUID, body: LoginIn, ip: str | None
) -> User:
    await enforce(redis, "login:ip", ip or "-", 5, 60)
    try:
        email = normalize_email(body.email)
    except ValueError:
        raise Unauthorized(GENERIC_LOGIN_ERROR) from None
    await enforce(redis, "login:email", ident_hash(f"{tenant_id}:{email}"), 10, 3600)
    user = await db.scalar(select(User).where(User.email == email))
    ok, rehash = await verify_password(
        user.pw_hash if user is not None and user.status == "active" else None, body.password
    )
    if not ok or user is None:
        await audit(
            db,
            tenant_id=tenant_id,
            action="auth.login_failed",
            ip=ip,
            detail={"email_hash": ident_hash(email)},
            severity="warning",
        )
        await db.commit()
        raise Unauthorized(GENERIC_LOGIN_ERROR)
    if rehash:
        user.pw_hash = await hash_password(body.password)
    user.last_login_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=tenant_id,
        action="auth.login",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
    )
    await db.commit()
    return user
