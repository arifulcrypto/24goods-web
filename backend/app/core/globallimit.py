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

from app.core.middleware import ASGIApp, Receive, Scope, Send, _header
from app.core.ratelimit import hit

log = logging.getLogger("app.ratelimit")

CFG_TTL_S = 5.0
DEFAULTS = {"enabled": False, "limit": 120, "window_s": 60}
BODY = b'{"code":429,"msg":"Access is too frequent. Please try again later."}'
EXEMPT_EXACT = frozenset({"/api/v1/pay/notify/zpay"})
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


async def mirror(redis: Redis, tenant_id: uuid.UUID, cfg: dict[str, Any]) -> None:
    await redis.set(cfg_key(tenant_id), json.dumps(normalize(cfg)))


def exempt(path: str) -> bool:
    return not path.startswith("/api/") or path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIX)


class GlobalRateLimitMiddleware:
    """Pure ASGI, after CoreMiddleware (client IP already resolved via trusted proxies)."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._cache: dict[uuid.UUID, tuple[float, dict[str, Any]]] = {}

    async def _config(self, redis: Redis, tid: uuid.UUID) -> dict[str, Any]:
        now = time.monotonic()
        hit_ = self._cache.get(tid)
        if hit_ and hit_[0] > now:
            return hit_[1]
        raw = await redis.get(cfg_key(tid))
        cfg = normalize(json.loads(raw)) if raw else dict(DEFAULTS)
        self._cache[tid] = (now + CFG_TTL_S, cfg)
        if len(self._cache) > 500:
            self._cache.clear()
        return cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        retry = 0
        try:
            res = scope["app"].state.res
            tid = await res.tenants.resolve(_header(scope, b"host"))
            cfg = await self._config(res.redis, tid)
            if cfg["enabled"]:
                ip = (scope.get("state") or {}).get("client_ip") or "-"
                allowed, reset = await hit(
                    res.redis, f"global:{tid}", ip, cfg["limit"], cfg["window_s"]
                )
                if not allowed:
                    retry = max(1, reset)
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
