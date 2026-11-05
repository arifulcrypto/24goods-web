"""Redis fixed-window rate limiting (``INCR`` + ``EXPIRE`` in one round-trip)."""

from __future__ import annotations

import hashlib
import time

from redis.asyncio import Redis

from app.core.errors import RateLimited


def ident_hash(value: str) -> str:
    """Hash identifiers (emails) so Redis keys never contain PII."""
    return hashlib.sha256(value.encode()).hexdigest()[:32]


async def hit(redis: Redis, name: str, ident: str, limit: int, window_s: int) -> tuple[bool, int]:
    """Count one event. Returns (allowed, seconds_until_reset)."""
    now = int(time.time())
    window = now // window_s
    key = f"rl:{name}:{ident}:{window}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window_s + 1)
        count, _ = await pipe.execute()
    reset = (window + 1) * window_s - now
    return int(count) <= limit, reset


async def enforce(redis: Redis, name: str, ident: str, limit: int, window_s: int) -> None:
    allowed, reset = await hit(redis, name, ident, limit, window_s)
    if not allowed:
        raise RateLimited(retry_after=reset)
