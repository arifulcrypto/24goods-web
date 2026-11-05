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


async def count(redis: Redis, name: str, ident: str, window_s: int) -> tuple[int, int]:
    """Current count in the fixed window (no increment). Returns (count, seconds_to_reset)."""
    now = int(time.time())
    window = now // window_s
    raw = await redis.get(f"rl:{name}:{ident}:{window}")
    return int(raw or 0), (window + 1) * window_s - now


async def blocked(redis: Redis, name: str, ident: str, limit: int, window_s: int) -> None:
    """Raise RateLimited if the window already holds ``limit`` events (check only)."""
    n, reset = await count(redis, name, ident, window_s)
    if n >= limit:
        raise RateLimited(retry_after=reset)


# Atomically add ARGV[1] units only if the total stays <= ARGV[2]; never inflate on refusal.
_RESERVE = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
local n = tonumber(ARGV[1])
if cur + n > tonumber(ARGV[2]) then return -1 end
local total = redis.call('INCRBY', KEYS[1], n)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return total
"""


def window_key(name: str, ident: str, window_s: int) -> tuple[str, int]:
    now = int(time.time())
    window = now // window_s
    return f"rl:{name}:{ident}:{window}", (window + 1) * window_s - now


async def reserve(
    redis: Redis, name: str, ident: str, units: int, limit: int, window_s: int
) -> tuple[bool, int]:
    """Consume ``units`` of a windowed budget only if they all fit. (ok, seconds_to_reset)."""
    key, reset = window_key(name, ident, window_s)
    total = await redis.eval(_RESERVE, 1, key, units, limit, window_s + 1)
    return int(total) >= 0, reset


async def release(redis: Redis, name: str, ident: str, units: int, window_s: int) -> None:
    """Give back units taken by :func:`reserve` when a later step refuses the request."""
    key, _ = window_key(name, ident, window_s)
    await redis.decrby(key, units)
