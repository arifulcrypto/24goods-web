from __future__ import annotations

import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import Conflict, Unauthorized
from app.core.ratelimit import blocked, enforce, hit, ident_hash
from app.core.security import hash_password, verify_password
from app.models import User
from app.modules.auth.schemas import LoginIn, RegisterIn, normalize_email

GENERIC_LOGIN_ERROR = "Invalid email or password"
# (limit, window_s) on FAILED logins only
IP_FAILURES = (5, 60)  # contract: 5/min/IP
PAIR_FAILURES = (10, 3600)  # per (email, ip)
EMAIL_FAILURES = (50, 3600)  # looser per-email cap across IPs (distributed guessing)


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
    # Only FAILED attempts are counted (a successful login never consumes budget), so an
    # attacker cannot lock a victim out from another IP and the victim's own logins are free.
    ip_key = ip or "-"
    await blocked(redis, "loginfail:ip", ip_key, *IP_FAILURES)
    try:
        email = normalize_email(body.email)
    except ValueError:
        await hit(redis, "loginfail:ip", ip_key, IP_FAILURES[0], IP_FAILURES[1])
        raise Unauthorized(GENERIC_LOGIN_ERROR) from None
    email_key = ident_hash(f"{tenant_id}:{email}")
    pair_key = ident_hash(f"{tenant_id}:{email}:{ip_key}")
    await blocked(redis, "loginfail:pair", pair_key, *PAIR_FAILURES)
    await blocked(redis, "loginfail:email", email_key, *EMAIL_FAILURES)
    user = await db.scalar(select(User).where(User.email == email))
    ok, rehash = await verify_password(
        user.pw_hash if user is not None and user.status == "active" else None, body.password
    )
    if not ok or user is None:
        for name, ident, (limit, window) in (
            ("loginfail:ip", ip_key, IP_FAILURES),
            ("loginfail:pair", pair_key, PAIR_FAILURES),
            ("loginfail:email", email_key, EMAIL_FAILURES),
        ):
            await hit(redis, name, ident, limit, window)
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
