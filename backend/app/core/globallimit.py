"""Global per-IP access-frequency limit (docs/review-v4.md §10). Default OFF.

Config: ``tenants.settings.rate_limit`` = ``{"enabled", "limit", "window_s"}``, mirrored by the
admin Site PUT into Redis ``cfg:ratelimit:<tenant_id>``. The middleware only reads Redis
(cached in-process; a disabled tenant costs at most one Redis read per ``CFG_TTL_S``).
Fail open: any Redis/tenant error lets the request through.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from starlette.requests import cookie_parser

from app.core.db import tenant_session
from app.core.middleware import ASGIApp, Receive, Scope, Send, _header
from app.core.ratelimit import hit
from app.core.security import STAFF_ROLES
from app.core.settings_v5 import ip_in_nets, parse_net, section
from app.models import Tenant

log = logging.getLogger("app.ratelimit")

CFG_TTL_S = 5.0
MIRROR_TTL_S = 600
DEFAULTS = {"enabled": False, "limit": 120, "window_s": 60}
BODY = b'{"code":429,"msg":"Access is too frequent. Please try again later."}'
EXEMPT_EXACT = frozenset({"/api/v1/pay/notify/zpay", "/api/v1/pay/notify/bepusdt"})
EXEMPT_PREFIX = ("/api/v1/realtime/", "/api/v1/admin/")


def cfg_key(tenant_id: uuid.UUID | str) -> str:
    return f"cfg:ratelimit:{tenant_id}"


def normalize(raw: Any) -> dict[str, Any]:
    """Validated config with defaults (bad values fall back to the defaults)."""
    r = raw if isinstance(raw, dict) else {}
    limit, window = r.get("limit"), r.get("window_s")
    return {
        "enabled": r.get("enabled") is True,
        "limit": limit if isinstance(limit, int) and 10 <= limit <= 10000 else DEFAULTS["limit"],
        "window_s": window
        if isinstance(window, int) and 1 <= window <= 3600
        else DEFAULTS["window_s"],
    }


def _with_ips(cfg: dict[str, Any], staff_ips: Any) -> dict[str, Any]:
    ips = [x for x in staff_ips if isinstance(x, str)] if isinstance(staff_ips, list) else []
    out = normalize(cfg)
    out["staff_ips"] = [x for x in ips if parse_net(x) is not None][:50]
    return out


async def mirror(
    redis: Redis, tenant_id: uuid.UUID, cfg: dict[str, Any], staff_ips: list[str] | None = None
) -> None:
    """Cache config + the staff IP allowlist (security section) for the middleware."""
    # TTL: a failed re-mirror (e.g. after removing an IP) self-heals from the DB within MIRROR_TTL_S
    await redis.set(cfg_key(tenant_id), json.dumps(_with_ips(cfg, staff_ips)), ex=MIRROR_TTL_S)


def exempt(path: str) -> bool:
    return not path.startswith("/api/") or path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIX)


class GlobalRateLimitMiddleware:
    """Pure ASGI, after CoreMiddleware (client IP already resolved via trusted proxies)."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._cache: dict[uuid.UUID, tuple[float, dict[str, Any]]] = {}

    async def _config(self, res: Any, tid: uuid.UUID) -> dict[str, Any]:
        now = time.monotonic()
        hit_ = self._cache.get(tid)
        if hit_ and hit_[0] > now:
            return hit_[1]
        raw = await res.redis.get(cfg_key(tid))
        if raw:
            parsed = json.loads(raw)
            cfg = _with_ips(parsed, parsed.get("staff_ips") if isinstance(parsed, dict) else None)
        else:
            # Redis miss (flush / restart): the DB is the source of truth. Reload and re-mirror.
            async with tenant_session(res.maker, None) as s:
                stored = await s.scalar(select(Tenant.settings).where(Tenant.id == tid))
            ips = section(stored, "security")["staff_ip_allowlist"]
            cfg = _with_ips((stored or {}).get("rate_limit"), ips)
            try:
                await mirror(res.redis, tid, cfg, ips)
            except Exception as exc:
                log.warning("rate limit config re-mirror failed error=%s", type(exc).__name__)
        cfg["nets"] = [n for n in map(parse_net, cfg["staff_ips"]) if n is not None]
        self._cache[tid] = (now + CFG_TTL_S, cfg)
        if len(self._cache) > 500:
            self._cache.clear()
        return cfg

    @staticmethod
    async def _is_staff(scope: Scope, res: Any, tid: uuid.UUID) -> bool:
        raw = _header(scope, b"cookie")
        if not raw:
            return False
        sess = await res.sessions.get(res.sessions.token_from(cookie_parser(raw)))
        return sess is not None and sess.tenant_id == tid and sess.role in STAFF_ROLES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        retry = 0
        try:
            res = scope["app"].state.res
            tid = await res.tenants.resolve(_header(scope, b"host"))
            cfg = await self._config(res, tid)
            ip = (scope.get("state") or {}).get("client_ip")
            # unknown IP: skip, never a shared bucket; allowlisted staff IPs are never limited
            if cfg["enabled"] and ip and not ip_in_nets(ip, cfg["nets"]):
                allowed, reset = await hit(
                    res.redis, f"global:{tid}", ip, cfg["limit"], cfg["window_s"]
                )
                if not allowed:
                    retry = max(1, reset)
                    if await self._is_staff(scope, res, tid):  # over-limit path only
                        retry = 0
        except Exception as exc:  # fail open
            log.warning("global rate limit skipped error=%s", type(exc).__name__)
        if retry:
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(BODY)).encode()),
                        (b"retry-after", str(retry).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": BODY})
            return
        await self.app(scope, receive, send)
