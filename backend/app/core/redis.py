from __future__ import annotations

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def make_redis(url: str) -> Redis:
    # Retry transparently on dropped/stale pooled connections (e.g. a Redis restart),
    # instead of surfacing a 500 on the first request after the reconnect.
    return Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=5,
        socket_connect_timeout=3,
        health_check_interval=10,
        max_connections=50,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.05), retries=3),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
