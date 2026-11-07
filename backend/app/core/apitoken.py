"""Staff API tokens (docs/review-v5.2.md section 5).

``kst_`` + 32 random bytes (urlsafe). Only the SHA-256 hex digest is stored, so a database leak
yields no usable credential. A token is accepted only on ``/api/v1/admin/*`` and
``/api/v1/uploads``, acts as its (still active, same-tenant, staff) owner and only on routes that
declare a scope with :func:`require_scope`: every other route refuses it (403 ``token_scope``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import api_token_ctx
from app.core.errors import Forbidden, Unauthorized
from app.core.ratelimit import blocked, enforce, hit
from app.core.resources import Resources
from app.core.security import STAFF_ROLES
from app.core.suspend import is_suspended
from app.models import ApiToken, User

TOKEN_PREFIX = "kst_"  # noqa: S105 (public format marker, not a secret)
SCOPES = (
    "products:read",
    "products:write",
    "cards:import",
    "orders:read",
    "chat",
    "uploads",
    "stats",
)
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 365
RATE_PER_MIN = 600
FAIL_PER_MIN_IP = 30
LAST_USED_THROTTLE_S = 60
ALLOWED_PATHS = ("/api/v1/admin/", "/api/v1/uploads")


def new_token() -> tuple[str, str, str]:
    """(plaintext, display prefix, sha256 hex). The plaintext is shown once and never stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token[:8], hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_from(request: Request) -> str | None:
    """The ``kst_`` bearer token, only when no session cookie is present and the path allows it."""
    auth = request.headers.get("authorization")
    if not auth or not auth[:7].lower() == "bearer ":
        return None
    token = auth[7:].strip()
    if not token.startswith(TOKEN_PREFIX):
        return None
    return token


def path_allows_token(path: str) -> bool:
    return any(path.startswith(p) for p in ALLOWED_PATHS)


async def authenticate(
    request: Request, db: AsyncSession, res: Resources, tenant_id: uuid.UUID, token: str
) -> tuple[User, ApiToken]:
    """Resolve a bearer token to its owner; every failure is the same generic 401."""
    ip = getattr(request.state, "client_ip", None) or "-"
    await blocked(res.redis, "apitoken:fail", ip, FAIL_PER_MIN_IP, 60)  # cheap, before the DB
    try:
        return await _authenticate(request, db, res, tenant_id, token, ip)
    except Unauthorized:
        await hit(res.redis, "apitoken:fail", ip, FAIL_PER_MIN_IP, 60)
        raise


async def _authenticate(
    request: Request,
    db: AsyncSession,
    res: Resources,
    tenant_id: uuid.UUID,
    token: str,
    ip: str,
) -> tuple[User, ApiToken]:
    if len(token) > 128 or not path_allows_token(request.url.path):
        raise Unauthorized("Authentication required")
    digest = hash_token(token)
    row = await db.scalar(
        select(ApiToken).where(ApiToken.token_hash == digest, ApiToken.tenant_id == tenant_id)
    )
    now = datetime.now(timezone.utc)
    if (
        row is None
        or not hmac.compare_digest(row.token_hash, digest)
        or row.revoked_at is not None
        or (row.expires_at is not None and row.expires_at <= now)
    ):
        raise Unauthorized("Authentication required")
    owner = await db.scalar(select(User).where(User.id == row.user_id, User.tenant_id == tenant_id))
    if (
        owner is None
        or owner.status != "active"
        or owner.role not in STAFF_ROLES
        or is_suspended(owner)
    ):
        raise Unauthorized("Authentication required")
    await enforce(res.redis, "apitoken", str(row.id), RATE_PER_MIN, 60)
    if row.last_used_at is None or (now - row.last_used_at).total_seconds() > LAST_USED_THROTTLE_S:
        await db.execute(
            update(ApiToken)
            .where(ApiToken.id == row.id, ApiToken.tenant_id == tenant_id)
            .values(last_used_at=now, last_used_ip=ip[:64] if ip != "-" else None)
        )
        # expire_on_commit=False (db.py): `row`/`owner` stay readable after this commit.
        await db.commit()
    request.state.api_token = row
    api_token_ctx.set({"id": str(row.id), "prefix": row.prefix})
    return owner, row


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    """Route dependency declaring the scope a token needs; a no-op for cookie sessions.

    List it in the route's ``dependencies=[...]`` so it runs before the user dependency, which
    performs the check (``check_scope``). A route that declares nothing refuses every token.
    """
    assert scope in SCOPES

    async def dep(request: Request) -> None:
        request.state.api_required_scope = scope

    return dep


def check_scope(request: Request, row: ApiToken) -> None:
    need = getattr(request.state, "api_required_scope", None)
    if need is None or need not in row.scopes:
        raise Forbidden("Token scope does not allow this endpoint", code="token_scope")


def clamp_expiry(days: int | None) -> datetime:
    d = DEFAULT_TTL_DAYS if days is None else days
    return datetime.now(timezone.utc) + timedelta(days=d)


async def revoke_user_tokens(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Revoke every live token of a user (suspension, password change, account deletion)."""
    res = await db.execute(
        update(ApiToken)
        .where(
            ApiToken.tenant_id == tenant_id,
            ApiToken.user_id == user_id,
            ApiToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    return res.rowcount or 0  # type: ignore[attr-defined]
