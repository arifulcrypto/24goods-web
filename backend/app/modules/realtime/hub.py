"""One Redis pattern subscription per process, fanned out to in-memory SSE queues."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.modules.realtime.publish import PREFIX

log = logging.getLogger("app.realtime")

HEARTBEAT_S = 15.0
QUEUE_MAX = 200


class Subscription:
    def __init__(self, channels: Iterable[str]):
        self.channels = frozenset(channels)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.overflowed = False

    def offer(self, item: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.overflowed = True  # slow client: it will be disconnected and re-sync


class RealtimeHub:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._subs: dict[str, set[Subscription]] = {}
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    def ensure_started(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="realtime-hub")

    async def wait_ready(self, timeout: float = 2.0) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    def subscribe(self, channels: Iterable[str]) -> Subscription:
        self.ensure_started()
        sub = Subscription(channels)
        for ch in sub.channels:
            self._subs.setdefault(ch, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        for ch in sub.channels:
            subs = self._subs.get(ch)
            if subs is not None:
                subs.discard(sub)
                if not subs:
                    self._subs.pop(ch, None)

    def dispatch(self, channel: str, raw: bytes | str) -> None:
        subs = self._subs.get(channel)
        if not subs:
            return
        try:
            item = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(item, dict) or "event" not in item:
            return
        for sub in list(subs):
            sub.offer(item)

    async def _run(self) -> None:
        backoff = 0.5
        while True:
            pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(PREFIX + "*")
                self._ready.set()
                backoff = 0.5
                while True:
                    msg = await pubsub.get_message(timeout=30.0)
                    if msg is None or msg.get("type") != "pmessage":
                        continue
                    channel = msg["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    self.dispatch(channel, msg["data"])
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError) as exc:
                self._ready.clear()
                log.warning("realtime hub reconnecting error=%s", type(exc).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()


async def sse_events(
    hub: RealtimeHub,
    sub: Subscription,
    heartbeat_s: float = HEARTBEAT_S,
    alive: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """``alive`` is re-checked at every heartbeat: a revoked session (logout, suspension)
    closes the stream instead of receiving events until the client disconnects."""
    try:
        yield "retry: 4000\n\n"
        while not sub.overflowed:
            try:
                item = await asyncio.wait_for(sub.queue.get(), heartbeat_s)
            except asyncio.TimeoutError:
                if alive is not None and not await alive():
                    break
                yield ": ping\n\n"
                continue
            data = json.dumps(item.get("data"), separators=(",", ":"), default=str)
            yield f"event: {item['event']}\ndata: {data}\n\n"
    finally:
        hub.unsubscribe(sub)
