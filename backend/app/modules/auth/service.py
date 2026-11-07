from __future__ import annotations

import ipaddress
import secrets
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pow as powmod
from app.core import suspend
from app.core.audit import audit
from app.core.config import Settings
from app.core.errors import Conflict, Unauthorized
from app.core.ratelimit import blocked, count, enforce, hit, ident_hash
from app.core.security import hash_password, verify_password
from app.models import User
from app.modules.auth.schemas import (
    HANDLE_RE,
    INSTANT_DOMAIN,
    InstantIn,
    LoginIn,
    RegisterIn,
    normalize_email,
)

GENERIC_LOGIN_ERROR = "Invalid email or password"
# (limit, window_s) on FAILED logins only
IP_FAILURES = (5, 60)  # contract: 5/min/IP
PAIR_FAILURES = (10, 3600)  # per (email, ip)
EMAIL_FAILURES = (50, 3600)  # looser per-email cap across IPs (distributed guessing)
POW_AFTER_FAILURES = 3  # proof of work is required from the 4th attempt per (login, ip)

HANDLE_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"  # base32, lowercase
PASSWORD_ALPHABET = (
    "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/l/I  # noqa: S105
)
PASSWORD_LEN = 14


def new_handle() -> str:
    return "u-" + "".join(secrets.choice(HANDLE_ALPHABET) for _ in range(7))


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LEN))


def net_of(ip: str | None) -> str:
    """/24 (IPv4) or /48 (IPv6) bucket of an address, for per-network limits."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return "-"
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


async def register(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    tenant_id: uuid.UUID,
    body: RegisterIn,
    ip: str | None,
    email_verified: bool = False,
) -> User:
    await powmod.verify(settings, redis, "register", body.pow)
    await enforce(redis, "register:ip", ip or "-", 5, 60)
    user = User(
        tenant_id=tenant_id,
        email=body.email,
        pw_hash=await hash_password(body.password),
        role="buyer",
        display_name=body.display_name,
        locale=body.locale,
        email_verified=email_verified,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise Conflict("This email can't be used") from None
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


async def instant(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    tenant_id: uuid.UUID,
    body: InstantIn,
    ip: str | None,
    locale: str,
) -> tuple[User, str]:
    """One-tap buyer: random handle + password, returned exactly once."""
    await powmod.verify(settings, redis, "instant", body.pow)
    ip_key = ip or "-"
    await enforce(redis, "instant:ip:h", ip_key, settings.instant_per_ip_hour, 3600)
    await enforce(redis, "instant:ip:d", ip_key, settings.instant_per_ip_day, 86400)
    await enforce(redis, "instant:net:d", net_of(ip), settings.instant_per_net_day, 86400)
    password = new_password()
    pw_hash = await hash_password(password)
    for _ in range(5):
        handle = new_handle()
        user = User(
            tenant_id=tenant_id,
            email=f"{handle}@{INSTANT_DOMAIN}",
            pw_hash=pw_hash,
            role="buyer",
            display_name=handle,
            locale=locale,
            is_instant=True,
            handle=handle,
        )
        db.add(user)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
    else:  # pragma: no cover - 35 bits of handle space; five collisions in a row
        raise Conflict("Could not allocate an account, try again")
    await audit(
        db,
        tenant_id=tenant_id,
        action="auth.instant",
        actor_id=user.id,
        actor_role="buyer",
        target=f"user:{user.id}",
        ip=ip,
    )
    await db.commit()
    return user, password


async def _find_login(db: AsyncSession, identifier: str) -> tuple[str, User | None]:
    """Resolve ``identifier`` (handle or email) to (canonical key, user)."""
    ident = identifier.strip().lower()
    if HANDLE_RE.match(ident):
        return ident, await db.scalar(select(User).where(User.handle == ident))
    email = normalize_email(identifier)  # ValueError -> caller counts a failure
    return email, await db.scalar(select(User).where(User.email == email))


async def login(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    tenant_id: uuid.UUID,
    body: LoginIn,
    ip: str | None,
) -> User:
    # Only FAILED attempts are counted (a successful login never consumes budget), so an
    # attacker cannot lock a victim out from another IP and the victim's own logins are free.
    ip_key = ip or "-"
    await blocked(redis, "loginfail:ip", ip_key, *IP_FAILURES)
    ident = body.email.strip().lower()
    email_key = ident_hash(f"{tenant_id}:{ident}")
    pair_key = ident_hash(f"{tenant_id}:{ident}:{ip_key}")
    await blocked(redis, "loginfail:pair", pair_key, *PAIR_FAILURES)
    await blocked(redis, "loginfail:email", email_key, *EMAIL_FAILURES)
    failures, _ = await count(redis, "loginfail:pair", pair_key, PAIR_FAILURES[1])
    if failures >= POW_AFTER_FAILURES:
        await powmod.verify(settings, redis, "login", body.pow)
    try:
        _, user = await _find_login(db, body.email)
    except ValueError:
        await hit(redis, "loginfail:ip", ip_key, IP_FAILURES[0], IP_FAILURES[1])
        await hit(redis, "loginfail:pair", pair_key, PAIR_FAILURES[0], PAIR_FAILURES[1])
        raise Unauthorized(GENERIC_LOGIN_ERROR) from None
    ok, rehash = await verify_password(
        user.pw_hash if user is not None and user.status == "active" else None, body.password
    )
    if not ok or user is None:
        for name, ident_, (limit, window) in (
            ("loginfail:ip", ip_key, IP_FAILURES),
            ("loginfail:pair", pair_key, PAIR_FAILURES),
            ("loginfail:email", email_key, EMAIL_FAILURES),
        ):
            await hit(redis, name, ident_, limit, window)
        await audit(
            db,
            tenant_id=tenant_id,
            action="auth.login_failed",
            ip=ip,
            detail={"email_hash": ident_hash(ident)},
            severity="warning",
        )
        await db.commit()
        raise Unauthorized(GENERIC_LOGIN_ERROR)
    await suspend.enforce(db, user)  # only after the password matched: no account probing
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
