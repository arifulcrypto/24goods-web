"""Email verification codes + password reset (review-v5 §5.2).

6-digit code in Redis for 10 minutes, 5 verification attempts, single use. Requesting is
limited per email (1/min, 10/day; silently no mail when exceeded) and per IP (429). The
endpoint answers 204 whether or not an account exists, and the mail is sent in a background
task so response timing does not reveal it either.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import Coroutine
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import apitoken, mailer
from app.core.audit import audit
from app.core.config import Settings
from app.core.email_templates import render_template, site_name
from app.core.errors import ApiError
from app.core.features import FeatureDisabled
from app.core.ratelimit import count, enforce, hit, ident_hash
from app.core.resources import Resources
from app.core.security import hash_password
from app.models import User

log = logging.getLogger("app.auth.codes")

Purpose = Literal["register", "reset"]
CODE_TTL_S = 600
MAX_ATTEMPTS = 5
_pending: set[asyncio.Task[None]] = set()


class InvalidCode(ApiError):
    """The verification code is invalid or expired"""

    status, code = 400, "invalid_code"


def _key(purpose: str, tenant_id: uuid.UUID, email: str) -> str:
    return f"ecode:{purpose}:{tenant_id}:{ident_hash(email)}"


def _mac(settings: Settings, purpose: str, tenant_id: uuid.UUID, email: str, code: str) -> str:
    msg = f"{purpose}|{tenant_id}|{email}|{code}".encode()
    return hmac.new(settings.pow_secret(), msg, hashlib.sha256).hexdigest()


async def issue(
    redis: Redis, settings: Settings, tenant_id: uuid.UUID, email: str, purpose: Purpose
) -> str | None:
    """A fresh code, or ``None`` when this email is in cooldown / over its daily send budget.
    A refused request never replaces (or extends) an outstanding code, and only actual sends
    count towards the daily cap."""
    ident = ident_hash(f"{tenant_id}:{email}")
    ok_min, _ = await hit(redis, "ecode:min", ident, 1, 60)
    if not ok_min:
        return None
    sent_today, _ = await count(redis, "ecode:day", ident, 86400)
    if sent_today >= 10:
        return None
    await hit(redis, "ecode:day", ident, 10, 86400)
    code = f"{secrets.randbelow(1_000_000):06d}"
    key = _key(purpose, tenant_id, email)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.hset(key, mapping={"mac": _mac(settings, purpose, tenant_id, email, code), "n": 0})
        pipe.expire(key, CODE_TTL_S)
        await pipe.execute()
    return code


# Atomic attempt accounting: count first, refuse past the cap (and burn the code) BEFORE any
# comparison happens; returns the stored MAC for a constant-time compare in Python.
_ATTEMPT = """
local stored = redis.call('HGET', KEYS[1], 'mac')
if not stored then return false end
local n = redis.call('HINCRBY', KEYS[1], 'n', 1)
if n > tonumber(ARGV[1]) then redis.call('DEL', KEYS[1]) return false end
if n >= tonumber(ARGV[1]) then redis.call('PEXPIRE', KEYS[1], 5000) end
return stored
"""


async def verify(
    redis: Redis,
    settings: Settings,
    tenant_id: uuid.UUID,
    email: str,
    purpose: Purpose,
    code: str | None,
) -> bool:
    """Constant-time check; consumes the code on success and after the last allowed attempt."""
    key = _key(purpose, tenant_id, email)
    if not code or not code.isdigit() or len(code) != 6:
        return False
    stored = await redis.eval(_ATTEMPT, 1, key, MAX_ATTEMPTS)
    if not stored:
        return False
    stored_s = stored.decode() if isinstance(stored, bytes) else str(stored)
    ok = hmac.compare_digest(stored_s, _mac(settings, purpose, tenant_id, email, code))
    if ok:
        await redis.delete(key)
    return ok


async def drain_pending() -> None:
    """Wait for in-flight background mails (tests / shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _send_bg(cfg: mailer.SmtpConfig, to: str, subject: str, body: str, html: str) -> None:
    try:
        await mailer.send_mail(cfg, to, subject, body, html)
    except mailer.MailError as exc:
        log.warning("verification mail failed code=%s", exc)


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.ensure_future(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def request_code(
    db: AsyncSession,
    res: Resources,
    tenant_id: uuid.UUID,
    ts: dict,
    email: str,
    purpose: Purpose,
    lang: str,
) -> None:
    """Send a code when the address may receive one; always returns silently otherwise."""
    cfg = await mailer.load_config(db, res.settings, tenant_id, ts, require_enabled=True)
    if cfg is None:
        raise FeatureDisabled("Email is not enabled")
    user = await db.scalar(select(User).where(User.email == email))
    if purpose == "register" and user is not None:
        return
    if purpose == "reset" and (
        user is None or user.status != "active" or user.is_instant or user.role != "buyer"
    ):
        return  # staff reset passwords through an admin, not through a mailbox
    code = await issue(res.redis, res.settings, tenant_id, email, purpose)
    if code is None:
        return
    subject, body, html = render_template(
        ts,
        "verify_code" if purpose == "register" else "password_reset",
        lang,
        {
            "site_name": site_name(ts, lang, res.settings.brand_name),
            "code": code,
            "minutes": str(CODE_TTL_S // 60),
        },
    )
    _spawn(_send_bg(cfg, email, subject, body, html))


async def reset_password(
    db: AsyncSession,
    res: Resources,
    tenant_id: uuid.UUID,
    email: str,
    code: str,
    new_password: str,
    ip: str | None,
) -> None:
    await enforce(res.redis, "pwreset:ip", ip or "-", 10, 3600)
    if not await verify(res.redis, res.settings, tenant_id, email, "reset", code):
        raise InvalidCode("The verification code is invalid or expired")
    user = await db.scalar(select(User).where(User.email == email).with_for_update())
    if user is None or user.status != "active" or user.is_instant or user.role != "buyer":
        raise InvalidCode("The verification code is invalid or expired")
    user.pw_hash = await hash_password(new_password)
    await apitoken.revoke_user_tokens(db, user.tenant_id, user.id)
    user.email_verified = True
    await audit(
        db,
        tenant_id=tenant_id,
        action="auth.password_reset",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
    )
    uid = user.id
    await db.commit()
    await res.sessions.revoke_user_sessions(uid)
