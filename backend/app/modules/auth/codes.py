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
from app.modules.auth.schemas import INSTANT_DOMAIN

log = logging.getLogger("app.auth.codes")

Purpose = Literal["register", "reset", "email_change"]
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
        pipe.hset(
            key,
            mapping={
                "mac": _mac(settings, purpose, tenant_id, email, code),
                "generation": secrets.token_hex(16),
                "n": 0,
            },
        )
        pipe.expire(key, CODE_TTL_S)
        await pipe.execute()
    return code


# Compare the fixed-size keyed MAC and consume/count in ONE atomic script. A burst of
# correct submissions cannot delete the record before the winning consumer claims it.
# Redis Lua has bit operations; inspect every byte instead of an early-exit string compare.
_VERIFY = """
local stored = redis.call('HGET', KEYS[1], 'mac')
if not stored then return 0 end
local given = ARGV[1]
local diff = bit.bxor(string.len(stored), string.len(given))
for i=1,64 do
  diff = bit.bor(diff, bit.bxor(string.byte(stored,i) or 0, string.byte(given,i) or 0))
end
if diff == 0 then return redis.call('DEL', KEYS[1]) end
local n = redis.call('HINCRBY', KEYS[1], 'n', 1)
if n >= tonumber(ARGV[2]) then redis.call('DEL', KEYS[1]) end
return 0
"""


async def verify(
    redis: Redis,
    settings: Settings,
    tenant_id: uuid.UUID,
    email: str,
    purpose: Purpose,
    code: str | None,
) -> bool:
    """Fixed-size constant-time MAC comparison, attempt cap and consumption are atomic."""
    if not code or not code.isascii() or not code.isdigit() or len(code) != 6:
        return False
    candidate = _mac(settings, purpose, tenant_id, email, code)
    return bool(
        await redis.eval(_VERIFY, 1, _key(purpose, tenant_id, email), candidate, MAX_ATTEMPTS)
    )


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


def email_change_ident(user_id: uuid.UUID, email: str) -> str:
    """Code scope for an email change: bound to the requesting user AND the new address, so a
    code can't be replayed by another account or for another address."""
    return f"{user_id}|{email}"


async def request_email_change_code(
    db: AsyncSession,
    res: Resources,
    user: User,
    ts: dict,
    new_email: str,
    lang: str,
) -> None:
    """Mail a code to ``new_email``. Returns silently (nothing sent) when the address is already
    registered or rate-limited, so the caller's response is identical in every case."""
    cfg = await mailer.load_config(db, res.settings, user.tenant_id, ts, require_enabled=True)
    if cfg is None:
        raise FeatureDisabled("Email is not enabled")
    if await db.scalar(select(User.id).where(User.email == new_email)) is not None:
        return
    # per-ADDRESS budget as well (the code itself is scoped per user+address): several accounts
    # must not be able to mail-bomb one victim address (#72 review L1)
    addr = ident_hash(f"{user.tenant_id}:chg:{new_email}")
    ok_min, _ = await hit(res.redis, "ecode:chg:min", addr, 1, 60)
    if not ok_min:
        return
    ok_day, _ = await hit(res.redis, "ecode:chg:day", addr, 10, 86400)
    if not ok_day:
        return
    code = await issue(
        res.redis,
        res.settings,
        user.tenant_id,
        email_change_ident(user.id, new_email),
        "email_change",
    )
    if code is None:
        return
    subject, body, html = render_template(
        ts,
        "verify_code",
        lang,
        {
            "site_name": site_name(ts, lang, res.settings.brand_name),
            "code": code,
            "minutes": str(CODE_TTL_S // 60),
        },
    )
    _spawn(_send_bg(cfg, new_email, subject, body, html))


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


async def notify_email_changed(
    db: AsyncSession, res: Resources, tenant_id: uuid.UUID, old_email: str, lang: str
) -> None:
    """Tell the PREVIOUS address that the login email changed (#72 review L2). Best effort: no
    mailer, or an instant placeholder address, sends nothing."""
    if old_email.endswith("@" + INSTANT_DOMAIN):
        return
    from app.modules.content.site import tenant_settings

    ts = await tenant_settings(db, tenant_id)
    cfg = await mailer.load_config(db, res.settings, tenant_id, ts, require_enabled=True)
    if cfg is None:
        return
    name = site_name(ts, lang, res.settings.brand_name)
    if lang == "zh-CN":
        subject = f"{name}：你的登录邮箱已更改"
        body = (
            f"你在 {name} 的账户登录邮箱刚刚被更改，此邮箱不再用于登录。\n"
            "如果这不是你本人操作，请立即联系客服。"
        )
    else:
        subject = f"{name}: your sign-in email was changed"
        body = (
            f"The sign-in email of your {name} account was just changed; this address is no "
            "longer used to sign in.\nIf you did not do this, contact support right away."
        )
    html = "<p>" + body.replace("\n", "</p><p>") + "</p>"
    _spawn(_send_bg(cfg, old_email, subject, body, html))
