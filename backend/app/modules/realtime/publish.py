"""Publish realtime events to Redis pub/sub. Failures never break the request."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger("app.realtime")

PREFIX = "rt:"


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"{PREFIX}user:{user_id}"


def staff_channel(tenant_id: uuid.UUID | str) -> str:
    return f"{PREFIX}staff:{tenant_id}"


def public_channel(tenant_id: uuid.UUID | str) -> str:
    return f"{PREFIX}public:{tenant_id}"


async def publish(redis: Redis, channel: str, event: str, data: Any) -> None:
    try:
        await redis.publish(channel, json.dumps({"event": event, "data": data}, default=str))
    except RedisError as exc:
        log.warning("realtime publish failed channel=%s error=%s", channel, type(exc).__name__)


async def publish_message(
    redis: Redis, tenant_id: uuid.UUID, buyer_id: uuid.UUID, message: dict[str, Any]
) -> None:
    data = {"conversation_id": message["conversation_id"], "message": message}
    await publish(redis, user_channel(buyer_id), "message", data)
    await publish(redis, staff_channel(tenant_id), "message", data)


async def publish_order(
    redis: Redis,
    tenant_id: uuid.UUID,
    buyer_id: uuid.UUID,
    buyer_view: dict[str, Any],
    staff_view: dict[str, Any],
) -> None:
    await publish(redis, user_channel(buyer_id), "order", buyer_view)
    await publish(redis, staff_channel(tenant_id), "order", staff_view)


async def publish_after_sales(
    redis: Redis,
    tenant_id: uuid.UUID,
    buyer_id: uuid.UUID,
    buyer_view: dict[str, Any],
    staff_view: dict[str, Any],
) -> None:
    await publish(redis, user_channel(buyer_id), "after_sales", buyer_view)
    await publish(redis, staff_channel(tenant_id), "after_sales", staff_view)


async def publish_conversation(
    redis: Redis,
    tenant_id: uuid.UUID,
    buyer_id: uuid.UUID,
    buyer_view: dict[str, Any] | None,
    staff_view: dict[str, Any] | None,
) -> None:
    if buyer_view is not None:
        await publish(redis, user_channel(buyer_id), "conversation", buyer_view)
    if staff_view is not None:
        await publish(redis, staff_channel(tenant_id), "conversation", staff_view)


async def publish_agent_status(
    redis: Redis, tenant_id: uuid.UUID, best: dict[str, Any], agent: dict[str, Any]
) -> None:
    """Buyers get the aggregated best status (same as GET /support/status); staff get the
    individual agent's status (``agent_id`` added)."""
    await publish(redis, public_channel(tenant_id), "agent_status", best)
    await publish(redis, staff_channel(tenant_id), "agent_status", agent)
