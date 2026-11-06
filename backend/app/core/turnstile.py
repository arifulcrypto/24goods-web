"""Cloudflare Turnstile verification (review-v5 §5.1).

Off by default. When enabled in Settings -> Security, the listed actions require a token
(JSON field ``turnstile_token`` or, for lookup, header ``X-Turnstile-Token``). Fail closed on
failure/timeout; if enabled but no secret is configured the check is skipped (and a warning
logged) so a misconfiguration cannot lock everybody out. Tokens are single use (Redis, 5 min).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

import httpx
from fastapi import Request

from app.core import secretbox
from app.core.db import tenant_session
from app.core.deps import client_ip
from app.core.errors import ApiError
from app.core.settings_v5 import section

log = logging.getLogger("app.turnstile")

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TOKEN_TTL_S = 300
MAX_TOKEN_LEN = 2048


class TurnstileFailed(ApiError):
    """Human verification failed"""

    status, code = 400, "turnstile_failed"


async def require_turnstile(
    request: Request, ts: dict[str, Any], action: str, token: str | None
) -> None:
    """Verify ``token`` when Turnstile protects ``action`` for this tenant, else no-op."""
    sec = section(ts, "security")
    if not sec["turnstile_enabled"] or action not in sec["turnstile_actions"]:
        return
    res = request.app.state.res
    tenant_id: uuid.UUID = request.state.tenant_id
    async with tenant_session(res.maker, tenant_id) as db:
        secret = await secretbox.get_secret(db, res.settings, tenant_id, "turnstile_secret")
    if not secret:
        log.warning("turnstile enabled without a secret; check skipped tenant=%s", tenant_id)
        return
    token = (token or "").strip()
    if not token or len(token) > MAX_TOKEN_LEN:
        raise TurnstileFailed("Human verification required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not await res.redis.set(f"ts:used:{digest}", "1", ex=TOKEN_TTL_S, nx=True):
        raise TurnstileFailed("Human verification token already used")
    data = {"secret": secret, "response": token, "idempotency_key": str(uuid.uuid4())}
    ip = client_ip(request)
    if ip:
        data["remoteip"] = ip
    try:
        r = await res.http.post(SITEVERIFY_URL, data=data, timeout=5.0)
        ok = r.status_code == 200 and r.json().get("success") is True
    except (httpx.HTTPError, ValueError):
        ok = False
    if not ok:
        raise TurnstileFailed("Human verification failed")
