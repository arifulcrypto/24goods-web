"""Google sign-in (OAuth 2.0 authorization-code + PKCE, review-v5 §5.3).

State and the PKCE verifier live in Redis for 10 minutes and are bound to the browser by a
short-lived HttpOnly cookie (prevents login CSRF / code injection). The id_token comes
straight from Google's token endpoint over TLS (OIDC Core 3.1.3.7), so its signature check is
optional; issuer, audience, expiry and ``email_verified`` are still enforced. Staff accounts
may NOT sign in with Google.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox, suspend
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, get_tenant_id
from app.core.errors import ApiError, Forbidden
from app.core.features import require_feature
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.core.security import STAFF_ROLES, hash_password
from app.core.settings_v5 import section
from app.models import User
from app.modules.auth.schemas import normalize_email
from app.modules.content.site import tenant_settings

log = logging.getLogger("app.auth.oauth")

router = APIRouter(prefix="/auth/oauth/google", tags=["auth"])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_TTL_S = 600
STATE_COOKIE = "g_state"


class OAuthFailed(ApiError):
    """Google sign-in failed"""

    status, code = 400, "oauth_failed"


def safe_next(value: str | None) -> str:
    """Relative path only: no scheme/host, no protocol-relative, no backslash tricks."""
    v = (value or "/").strip()
    if (
        not v.startswith("/")
        or v.startswith("//")
        or "\\" in v
        or len(v) > 300
        or any(ord(c) < 32 or ord(c) == 127 for c in v)
    ):
        return "/"
    return v


def redirect_uri(res: Resources) -> str:
    return res.settings.public_base_url.rstrip("/") + "/api/v1/auth/oauth/google/callback"


def _pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


async def _google_config(db: AsyncSession, res: Resources, tenant_id: uuid.UUID) -> tuple[str, str]:
    ts = await tenant_settings(db, tenant_id)
    require_feature(ts, "google_login")
    client_id = section(ts, "security")["google_client_id"]
    secret = await secretbox.get_secret(db, res.settings, tenant_id, "google_client_secret")
    if not client_id or not secret:
        raise OAuthFailed("Google sign-in is not configured")
    return client_id, secret


def _state_key(state: str) -> str:
    return "oauth:g:" + hashlib.sha256(state.encode()).hexdigest()


@router.get("/start")
async def start(
    request: Request,
    next: str | None = Query(None, max_length=300),
    agree: int = Query(0, ge=0, le=1),  # the web passes 1 after showing the terms
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> RedirectResponse:
    await enforce(res.redis, "oauth:start", client_ip(request) or "-", 20, 60)
    client_id, _ = await _google_config(db, res, tenant_id)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    await res.redis.set(
        _state_key(state),
        json.dumps(
            {
                "v": verifier,
                "next": safe_next(next),
                "tid": str(tenant_id),
                "agree": bool(agree),
            }
        ),
        ex=STATE_TTL_S,
    )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(res),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    resp = RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=302)
    resp.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_S,
        httponly=True,
        samesite="lax",  # sent on the top-level redirect back from Google
        secure=res.settings.cookie_secure,
        path="/api/v1/auth/oauth/google",
    )
    return resp


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise OAuthFailed("Invalid id_token") from None
    if not isinstance(data, dict):
        raise OAuthFailed("Invalid id_token")
    return data


def _display_name(claims: dict[str, Any], email: str) -> str:
    name = " ".join(str(claims.get("name") or "").split())
    name = "".join(c for c in name if ord(c) >= 32)[:40]
    return name or email.split("@")[0][:40]


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = Query(None, max_length=2048),
    state: str | None = Query(None, max_length=200),
    error: str | None = Query(None, max_length=100),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> RedirectResponse:
    ip = client_ip(request)
    await enforce(res.redis, "oauth:cb", ip or "-", 20, 60)
    if error or not code or not state:
        raise OAuthFailed("Google sign-in was cancelled")
    cookie = request.cookies.get(STATE_COOKIE)
    raw = await res.redis.getdel(_state_key(state))
    if not raw or not cookie or not secrets.compare_digest(cookie, state):
        raise OAuthFailed("Sign-in session expired, try again")
    saved = json.loads(raw)
    if saved.get("tid") != str(tenant_id):
        raise OAuthFailed("Sign-in session expired, try again")
    client_id, client_secret = await _google_config(db, res, tenant_id)
    try:
        r = await res.http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri(res),
                "grant_type": "authorization_code",
                "code_verifier": saved["v"],
            },
            timeout=10.0,
        )
        tokens = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        tokens = {}
    id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(id_token, str):
        raise OAuthFailed("Google sign-in failed")
    claims = _claims(id_token)
    now = time.time()
    if (
        claims.get("iss") not in ISSUERS
        or claims.get("aud") != client_id
        or not isinstance(claims.get("exp"), int | float)
        or claims["exp"] < now
        or claims.get("email_verified") not in (True, "true")
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
        or len(claims["sub"]) > 64
    ):
        raise OAuthFailed("Google account could not be verified")
    try:
        email = normalize_email(str(claims.get("email") or ""))
    except ValueError:
        raise OAuthFailed("Google account has no usable email") from None
    sub: str = claims["sub"]

    user = await db.scalar(select(User).where(User.google_sub == sub).with_for_update())
    created = False
    revoke_sessions = False
    if user is None:
        user = await db.scalar(select(User).where(User.email == email).with_for_update())
        if user is not None:  # link the Google identity to the existing verified-email account
            if user.role in STAFF_ROLES:
                raise Forbidden("Staff accounts cannot sign in with Google")
            if user.google_sub and user.google_sub != sub:
                raise OAuthFailed("This account is linked to a different Google account")
            if not user.email_verified:
                # pre-hijack guard: whoever registered this address first never proved they own
                # it, so their password and sessions are void once the real owner links Google
                user.pw_hash = await hash_password(secrets.token_urlsafe(32))
                revoke_sessions = True
            user.google_sub = sub
            user.email_verified = True
        else:
            ts = await tenant_settings(db, tenant_id)
            require_feature(ts, "registration")  # Google sign-UP is registration too
            if section(ts, "legal")["enabled"] and not saved.get("agree"):
                return RedirectResponse("/login?error=terms_required", status_code=302)
            user = User(
                tenant_id=tenant_id,
                email=email,
                pw_hash=await hash_password(secrets.token_urlsafe(32)),
                role="buyer",
                display_name=_display_name(claims, email),
                locale=section(ts, "defaults")["locale"],
                email_verified=True,
                google_sub=sub,
            )
            db.add(user)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                raise OAuthFailed("Google sign-in failed") from None
            created = True
    if user.role in STAFF_ROLES:
        raise Forbidden("Staff accounts cannot sign in with Google")
    if user.status != "active":
        raise Forbidden("Account is not active")
    await suspend.enforce(db, user)
    user.last_login_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=tenant_id,
        action="auth.google_login",
        actor_id=user.id,
        actor_role=user.role,
        target=f"user:{user.id}",
        ip=ip,
        detail={"created": created},
    )
    uid = user.id
    await db.commit()
    if revoke_sessions:
        await res.sessions.revoke_user_sessions(uid)
    await res.sessions.delete(res.sessions.token_from(request.cookies))
    token = await res.sessions.create(user.id, user.tenant_id, user.role)
    resp = RedirectResponse(safe_next(saved.get("next")), status_code=302)
    res.sessions.set_cookie(resp, token, user.role)
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/oauth/google")
    return resp
