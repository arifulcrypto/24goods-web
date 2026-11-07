"""Blocked-IP enforcement (docs/review-v5.1.md §5, app-level MVP).

Config: the tenant's ``blocked_ips`` rows (+ ``security.staff_ip_allowlist``) mirrored by the admin
CRUD into Redis ``cfg:blockedips:<tenant_id>``; the middleware reads Redis (in-process cache a few
seconds) and falls back to the DB on a miss. Fail open on any error. The allowlist always wins;
payment notifies and health are exempt. Pure ASGI, after CoreMiddleware (trusted-proxy client IP).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.middleware import ASGIApp, Receive, Scope, Send, _header
from app.core.settings_v5 import IPNet, ip_in_nets, parse_net, section
from app.models import BlockedIP, Tenant

log = logging.getLogger("app.ipblock")

CFG_TTL_S = 5.0
MIRROR_TTL_S = 600
MAX_BLOCKS = 1000
BODY = b'{"error":{"code":"ip_blocked","message":"Access denied"}}'
EXEMPT_PREFIX = ("/api/v1/pay/notify/",)


def parse_block_net(raw: str) -> IPNet | None:
    """An IP or CIDR to block: IPv4 down to /8, IPv6 down to /32; never 0.0.0.0/0 or ::/0."""
    try:
        net = ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError:
        return None
    if net.prefixlen < (8 if net.version == 4 else 32) or net.is_unspecified:
        return None
    return net


def cfg_key(tenant_id: uuid.UUID | str) -> str:
    return f"cfg:blockedips:{tenant_id}"


def exempt(path: str) -> bool:
    return not path.startswith("/api/") or path.startswith(EXEMPT_PREFIX)


async def load_cfg(session: Any, tid: uuid.UUID) -> dict[str, Any]:
    stored = await session.scalar(select(Tenant.settings).where(Tenant.id == tid))
    rows = await session.execute(
        select(BlockedIP.cidr, BlockedIP.expires_at)
        .order_by(BlockedIP.created_at.desc())
        .limit(MAX_BLOCKS)
    )
    return {
        "staff_ips": section(stored, "security")["staff_ip_allowlist"],
        "blocks": [[c, e.timestamp() if e else None] for c, e in rows.all()],
    }


async def mirror(redis: Redis, tid: uuid.UUID, cfg: dict[str, Any]) -> None:
    await redis.set(cfg_key(tid), json.dumps(cfg), ex=MIRROR_TTL_S)


async def refresh(res: Any, tid: uuid.UUID) -> None:
    """Re-mirror after an admin change (the caller's tenant-pinned DB session)."""
    async with tenant_session(res.maker, tid) as s:
        cfg = await load_cfg(s, tid)
    await mirror(res.redis, tid, cfg)


def is_blocked(ip: str | None, cfg: dict[str, Any], now: float | None = None) -> bool:
    if not ip:
        return False
    now = time.time() if now is None else now
    if ip_in_nets(ip, cfg["staff_nets"]):
        return False  # allowlist wins
    return ip_in_nets(ip, [n for n, exp in cfg["blocks"] if exp is None or exp > now])


class IPBlockMiddleware:
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
        else:
            async with tenant_session(res.maker, tid) as s:
                parsed = await load_cfg(s, tid)
            try:
                await mirror(res.redis, tid, parsed)
            except Exception as exc:
                log.warning("ip block config re-mirror failed error=%s", type(exc).__name__)
        blocks: list[tuple[IPNet, float | None]] = []
        for cidr, exp in parsed.get("blocks", []):
            net = parse_block_net(str(cidr))
            if net is not None:
                blocks.append((net, exp))
        staff = [n for n in map(parse_net, parsed.get("staff_ips", [])) if n is not None]
        cfg = {"blocks": blocks, "staff_nets": staff}
        self._cache[tid] = (now + CFG_TTL_S, cfg)
        if len(self._cache) > 500:
            self._cache.clear()
        return cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        denied = False
        try:
            res = scope["app"].state.res
            tid = await res.tenants.resolve(_header(scope, b"host"))
            cfg = await self._config(res, tid)
            if cfg["blocks"]:
                denied = is_blocked((scope.get("state") or {}).get("client_ip"), cfg)
        except Exception as exc:  # fail open
            log.warning("ip block check skipped error=%s", type(exc).__name__)
        if denied:
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(BODY)).encode()),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": BODY})
            return
        await self.app(scope, receive, send)


__all__ = ["IPBlockMiddleware", "parse_block_net", "refresh"]
