"""FastAPI entrypoint: ``create_app()`` builds the public API (``/api/v1``)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.config import get_settings
from app.core.error_handlers import install_error_handlers
from app.core.memtrim import trim
from app.core.middleware import (
    SECURITY_HEADERS,
    BodyLimitMiddleware,
    CoreMiddleware,
    CSRFMiddleware,
)
from app.core.resources import Resources, build_resources
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.chat.router import router as chat_router
from app.modules.orders.router import router as orders_router
from app.modules.orders.service import expire_pending
from app.modules.payments.router import NOTIFY_PATH
from app.modules.payments.router import router as payments_router
from app.modules.realtime.router import router as realtime_router
from app.modules.uploads.router import router as uploads_router

log = logging.getLogger("app.main")

UPLOAD_PATHS = re.compile(r"^/api/v1/(uploads|admin/products/[^/]+/images)$")
JSON_BODY_LIMIT = 2 * 1024 * 1024 + 64 * 1024  # card import: 5000 lines fit comfortably
UPLOAD_BODY_LIMIT = 8 * 1024 * 1024 + 64 * 1024

__all__ = ["SECURITY_HEADERS", "create_app"]


async def _expiry_loop(res: Resources) -> None:
    while True:
        await asyncio.sleep(res.settings.expiry_interval_s)
        try:
            n = await expire_pending(res.maker, res)
            if n:
                log.info("expired %d pending orders", n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("order expiry run failed error=%s", type(exc).__name__)


def api_router() -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    for sub in (
        auth_router,
        catalog_router,
        chat_router,
        orders_router,
        payments_router,
        uploads_router,
        realtime_router,
        admin_router,
    ):
        r.include_router(sub)
    return r


def create_app(res: Resources | None = None, *, background: bool = True) -> FastAPI:
    """``res`` given: resources are shared/owned by the caller (serve.py, tests)."""
    settings = res.settings if res is not None else get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = app.state.res is None
        if owned:
            app.state.res = build_resources(settings)
        r: Resources = app.state.res
        tasks: list[asyncio.Task[None]] = []
        trim()  # drop import/startup garbage from RSS
        if background:
            r.hub.ensure_started()
            tasks.append(asyncio.create_task(_expiry_loop(r), name="order-expiry"))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
            if owned:
                await r.close()

    docs = not settings.is_prod
    app = FastAPI(
        title="24goods API",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    app.state.res = res
    install_error_handlers(app)
    app.include_router(api_router())

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Added inner-most first: Core (outermost) -> BodyLimit -> CSRF -> routes.
    app.add_middleware(CSRFMiddleware, exempt=[NOTIFY_PATH])
    app.add_middleware(
        BodyLimitMiddleware,
        default_limit=JSON_BODY_LIMIT,
        upload_limit=UPLOAD_BODY_LIMIT,
        upload_paths=UPLOAD_PATHS,
    )
    app.add_middleware(CoreMiddleware, trusted=settings.trusted_proxy_networks)
    return app


_default_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """``app.main:app`` is built lazily (PEP 562) so ``app.serve`` doesn't build it twice."""
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(name)
