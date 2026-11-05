from __future__ import annotations

from redis.asyncio import Redis


def make_redis(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=5,
        socket_connect_timeout=3,
        health_check_interval=30,
        max_connections=50,
    )
