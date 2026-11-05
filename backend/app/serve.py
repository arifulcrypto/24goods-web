"""``python -m app.serve``: public API (APP_PORT) + internal listener (APP_INTERNAL_PORT)
in ONE process / event loop, sharing DB and Redis pools."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import Iterator

import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.resources import build_resources
from app.internal import create_internal_app
from app.main import create_app

log = logging.getLogger("app.serve")


class _Server(uvicorn.Server):
    """Signals are handled once for both servers (see ``_main``)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _config(app, host: str, port: int) -> uvicorn.Config:  # type: ignore[no-untyped-def]
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        proxy_headers=False,  # client IP comes from CoreMiddleware + APP_TRUSTED_PROXIES
        server_header=False,
        date_header=True,
        access_log=False,
        log_config=None,
        lifespan="on",
        workers=1,
        timeout_graceful_shutdown=10,
    )


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if os.environ.get("APP_VAULT_PRIVATE_KEYS_FILE"):
        # ADR-002: the API must never be able to open card secrets.
        log.critical("refusing to start: APP_VAULT_PRIVATE_KEYS_FILE is set for the API process")
        sys.exit(2)
    res = build_resources(settings)
    servers = [
        _Server(_config(create_app(res), settings.host, settings.port)),
        _Server(_config(create_internal_app(res), settings.internal_host, settings.internal_port)),
    ]
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        for s in servers:
            if s.should_exit:
                s.force_exit = True
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _stop)
    log.info(
        "api listening on %s:%d, internal on %s:%d",
        settings.host,
        settings.port,
        settings.internal_host,
        settings.internal_port,
    )
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        await res.close()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
