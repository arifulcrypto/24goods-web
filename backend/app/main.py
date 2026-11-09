"""FastAPI entrypoint: ``create_app()`` builds the public API (``/api/v1``)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.config import get_settings
from app.core.error_handlers import install_error_handlers
from app.core.globallimit import GlobalRateLimitMiddleware
from app.core.ipblock import IPBlockMiddleware
from app.core.memtrim import trim
from app.core.middleware import (
    SECURITY_HEADERS,
    BodyLimitMiddleware,
    CoreMiddleware,
    CSRFMiddleware,
)
from app.core.resources import Resources, build_resources
from app.modules import emails, jobs
from app.modules.admin.blocking import router as admin_blocking_router
from app.modules.admin.categories import router as admin_categories_router
from app.modules.admin.importer import router as admin_import_router
from app.modules.admin.payment_gateways import router as payment_gateways_router
from app.modules.admin.payment_ops import router as payment_ops_router
from app.modules.admin.router import router as admin_router
from app.modules.admin.settings import router as admin_settings_router
from app.modules.admin.tokens import router as admin_tokens_router
from app.modules.admin.v3 import router as admin_v3_router
from app.modules.aftersales.router import router as aftersales_router
from app.modules.auth.oauth import router as oauth_router
from app.modules.auth.router import router as auth_router
from app.modules.catalog.router import router as catalog_router
from app.modules.challenges.middleware import SiteVisitMiddleware
from app.modules.challenges.router import router as challenges_router
from app.modules.chat.actions import router as chat_actions_router
from app.modules.chat.router import router as chat_router
from app.modules.content.router import router as content_router
from app.modules.lookup.router import router as lookup_router
from app.modules.me.router import avatar_router
from app.modules.me.router import router as me_router
from app.modules.operations import jobs as operations_jobs
from app.modules.operations.router import router as operations_router
from app.modules.orders.checkout_router import router as checkout_router
from app.modules.orders.router import router as orders_router
from app.modules.orders.service import expire_pending
from app.modules.payments.epay_page import router as epay_page_router
from app.modules.payments.router import NOTIFY_BEPUSDT_PATH, NOTIFY_PATH
from app.modules.payments.router import router as payments_router
from app.modules.promo.router import admin as promo_admin_router
from app.modules.promo.router import router as promo_router
from app.modules.realtime.router import router as realtime_router
from app.modules.reseller import domains
from app.modules.reseller.router import admin as reseller_admin_router
from app.modules.reseller.router import router as reseller_router
from app.modules.seo.router import router as seo_router
from app.modules.uploads.router import router as uploads_router

log = logging.getLogger("app.main")

UPLOAD_PATHS = re.compile(r"^/api/v1/(uploads|admin/products/[^/]+/images|admin/content/images)$")
JSON_BODY_LIMIT = 2 * 1024 * 1024 + 64 * 1024  # card import: 5000 lines fit comfortably
UPLOAD_BODY_LIMIT = 8 * 1024 * 1024 + 64 * 1024

__all__ = ["SECURITY_HEADERS", "create_app"]


async def _job(name: str, coro, res: Resources | None = None) -> None:  # type: ignore[no-untyped-def]
    """Run one periodic job; a failure never stops the loop."""
    started = time.monotonic()
    failed = False
    try:
        n = await coro
        if n:
            log.info("%s: %d", name, n)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failed = True
        log.error("%s failed error=%s", name, type(exc).__name__)
    finally:
        if res is not None:
            try:
                from app.modules.operations.task_events import report

                await report(res, name, started, failed)
            except Exception:
                log.warning("task heartbeat unavailable")


async def run_jobs_once(res: Resources, *, hourly: bool) -> None:
    from app.modules.payments.reconcile import run as reconcile_payments

    await _job("payment reconciliation", reconcile_payments(res), res)
    await _job("subscription sync", operations_jobs.sync_subscriptions(res), res)
    await _job("service reminder scheduling", operations_jobs.schedule_reminders(res), res)
    await _job("service reminder delivery", operations_jobs.process_reminders(res), res)
    await _job("order expiry", expire_pending(res.maker, res), res)
    await _job("auto confirm", jobs.auto_confirm(res.maker, res), res)
    await _job("order emails", emails.enqueue_order_emails(res.maker), res)
    await _job("order emails sent", emails.process_email_outbox(res.maker, res.settings), res)
    if hourly:
        await _job("deleted media purge", jobs.purge_deleted_media(res), res)
        await _job("card expiry", jobs.void_expired_cards(res.maker), res)
        await _job("instant purge", jobs.purge_instant_accounts(res.maker, res.settings), res)
        await _job("buyer input purge", jobs.purge_buyer_input(res.maker), res)
        await _job("operations event purge", operations_jobs.purge_events(res), res)
        await _job("audit purge", jobs.purge_audit(res.maker), res)
        await _job("reseller domains recheck", domains.recheck_active(res), res)


async def _expiry_loop(res: Resources) -> None:
    last_hourly = 0.0
    while True:
        await asyncio.sleep(res.settings.expiry_interval_s)
        now = time.monotonic()
        hourly = now - last_hourly >= res.settings.card_expiry_interval_s
        if hourly:
            last_hourly = now
        await run_jobs_once(res, hourly=hourly)


def api_router() -> APIRouter:
    r = APIRouter(prefix="/api/v1")
    r.include_router(payment_gateways_router)
    r.include_router(payment_ops_router)
    r.include_router(epay_page_router)
    r.include_router(operations_router)
    for sub in (
        auth_router,
        oauth_router,
        me_router,
        avatar_router,
        content_router,
        lookup_router,
        aftersales_router,
        catalog_router,
        chat_router,
        chat_actions_router,
        orders_router,
        checkout_router,
        payments_router,
        uploads_router,
        realtime_router,
        admin_router,
        admin_v3_router,
        promo_router,
        promo_admin_router,
        reseller_router,
        reseller_admin_router,
        admin_import_router,
        admin_categories_router,
        admin_settings_router,
        admin_blocking_router,
        admin_tokens_router,
        challenges_router,
    ):
        r.include_router(sub)
    return r


@functools.cache
def _route_table() -> APIRouter:
    """All API + crawler routes, analysed once per process.

    ``include_router`` re-runs FastAPI's dependency analysis for every endpoint (~0.5 s here).
    Production builds one app, but the test suite builds one per test, so each app gets a fresh
    copy of this (immutable) route list instead -- its own app, middleware and state as before.
    """
    table = APIRouter()
    table.include_router(api_router())
    table.include_router(seo_router)  # root-level crawler HTML, sitemap, robots (ADR-024)
    return table


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
    app.router.routes.extend(_route_table().routes)  # a per-app list: later includes stay local

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Added inner-most first:
    # Core (outermost) -> IPBlock -> GlobalRateLimit -> BodyLimit -> CSRF -> SiteVisit -> routes.
    app.add_middleware(SiteVisitMiddleware)
    app.add_middleware(CSRFMiddleware, exempt=[NOTIFY_PATH, NOTIFY_BEPUSDT_PATH])
    app.add_middleware(
        BodyLimitMiddleware,
        default_limit=JSON_BODY_LIMIT,
        upload_limit=UPLOAD_BODY_LIMIT,
        upload_paths=UPLOAD_PATHS,
    )
    app.add_middleware(GlobalRateLimitMiddleware)  # after Core: client IP is resolved
    app.add_middleware(IPBlockMiddleware)  # outside the limiter: blocked IPs cost no limiter hits
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
