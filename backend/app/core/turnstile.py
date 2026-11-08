"""Cloudflare Turnstile verification (review-v5 §5.1).

Off by default. When enabled in Settings -> Security, the listed actions require a token
(JSON field ``turnstile_token`` or, for lookup, header ``X-Turnstile-Token``). Fail closed on
failure/timeout and incomplete configuration. Tokens are single use (Redis, 5 min).
An authenticated administrator can disable the setting through the audited settings route.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request

from app.core import secretbox
from app.core.db import tenant_session
from app.core.deps import client_ip
from app.core.errors import ApiError
from app.core.settings_v5 import ip_allowlisted, section

log = logging.getLogger("app.turnstile")

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TOKEN_TTL_S = 300
TEST_SECRET_PREFIXES = (
    "1x0000000000000000000000000000000",
    "2x0000000000000000000000000000000",
    "3x0000000000000000000000000000000",
)
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
    if action == "login" and ip_allowlisted(client_ip(request), sec["staff_ip_allowlist"]):
        return  # staff office IPs skip the login challenge only (review-v5.2 §1)
    res = request.app.state.res
    tenant_id: uuid.UUID = request.state.tenant_id
    async with tenant_session(res.maker, tenant_id) as db:
        secret = await secretbox.get_secret(db, res.settings, tenant_id, "turnstile_secret")
    is_test_secret = bool(secret and secret.startswith(TEST_SECRET_PREFIXES))
    if (
        not secret
        or not sec["turnstile_site_key"]
        or (res.settings.env == "prod" and is_test_secret)
    ):
        log.warning("turnstile configuration unhealthy tenant=%s", tenant_id)
        raise TurnstileFailed("Human verification is temporarily unavailable")
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
        body = r.json()
        ok = (
            r.status_code == 200
            and isinstance(body, dict)
            and body.get("success") is True
            and body.get("action") == action
        )
        # the token must have been issued for one of OUR hostnames (Cloudflare test secrets
        # always answer example.com, so the check is skipped for them)
        host = body.get("hostname") if isinstance(body, dict) else None
        if ok and not is_test_secret:
            allowed = {
                (urlsplit(res.settings.public_base_url).hostname or "").lower(),
                (request.headers.get("host") or "").rsplit(":", 1)[0].lower().strip("[]"),
            } - {""}
            ok = isinstance(host, str) and host.lower() in allowed
    except (httpx.HTTPError, ValueError, AttributeError):
        ok = False
    if not ok:
        raise TurnstileFailed("Human verification failed")
