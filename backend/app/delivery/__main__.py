"""``python -m app.delivery``: outbox consumer + internal reveal API (APP_DELIVERY_PORT)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

from app.core.db import make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.core.redis import make_redis
from app.delivery.config import DeliverySettings
from app.delivery.context import DeliveryContext
from app.delivery.keys import load_opener
from app.delivery.reveal import create_delivery_app
from app.delivery.worker import run_worker

log = logging.getLogger("app.delivery")


async def _main() -> None:
    settings = DeliverySettings()
    configure_logging(settings.log_level)
    if len(settings.internal_token) < 32:
        raise SystemExit("APP_INTERNAL_TOKEN (>= 32 chars) is required")
    opener = load_opener(settings.vault_private_keys_file)
    engine = make_engine(settings.delivery_database_url, pool_size=3, max_overflow=2)
    redis = make_redis(settings.redis_url)
    ctx = DeliveryContext(settings, make_sessionmaker(engine), redis, opener)
    stop = asyncio.Event()
    worker = asyncio.create_task(run_worker(ctx, stop), name="outbox-worker")
    server = uvicorn.Server(
        uvicorn.Config(
            create_delivery_app(ctx),
            host=settings.delivery_host,
            port=settings.delivery_port,
            proxy_headers=False,
            server_header=False,
            access_log=False,
            log_config=None,
            lifespan="off",
        )
    )
    log.info("delivery service on %s:%d", settings.delivery_host, settings.delivery_port)
    try:
        await server.serve()
    finally:
        stop.set()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
