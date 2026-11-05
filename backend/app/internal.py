"""Internal-only app (loopback / private listener, never routed by Caddy).

GET /internal/tls/ask?domain=  — Caddy on-demand TLS permission check.
"""

from __future__ import annotations

import ipaddress
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.core.db import tenant_session
from app.core.ratelimit import hit
from app.core.resources import Resources
from app.models import CustomDomain

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_FORBIDDEN = re.compile(r"[\s/:@\[\]\\?#%]")
NEG_TTL_S = 600


def normalize_hostname(raw: str | None) -> str | None:
    """IDNA-normalized lowercase hostname, or None if malformed / an IP literal."""
    if not raw:
        return None
    host = raw.strip()
    if not host or len(host) > 253 or _FORBIDDEN.search(host):
        return None
    host = host.rstrip(".")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(ascii_host) > 253:
        return None
    labels = ascii_host.split(".")
    if len(labels) < 2 or not all(_LABEL.match(lbl) for lbl in labels):
        return None
    if labels[-1].isdigit():
        return None  # numeric TLD: an IPv4 in disguise (e.g. 0x7f.1 / 127.1)
    return ascii_host


def create_internal_app(res: Resources | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if res is not None:
            app.state.res = res
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if res is not None:
        app.state.res = res

    @app.get("/internal/tls/ask", response_class=PlainTextResponse)
    async def tls_ask(
        request: Request, domain: str = Query("", max_length=300)
    ) -> PlainTextResponse:
        r: Resources = request.app.state.res
        host = normalize_hostname(domain)
        if host is None:
            return PlainTextResponse("malformed", status_code=400)
        neg_key = f"tls:neg:{host}"
        if await r.redis.exists(neg_key):
            return PlainTextResponse("unknown", status_code=404)
        allowed, reset = await hit(
            r.redis, "tls:ask", "global", r.settings.tls_ask_rate_per_min, 60
        )
        if not allowed:
            return PlainTextResponse(
                "rate limited", status_code=429, headers={"Retry-After": str(reset)}
            )
        async with tenant_session(r.maker, None) as db:
            found = await db.scalar(
                select(CustomDomain.id).where(
                    CustomDomain.hostname == host,
                    CustomDomain.status == "active",
                    CustomDomain.verified_at.is_not(None),
                )
            )
        if found is None:
            await r.redis.set(neg_key, b"1", ex=NEG_TTL_S)
            return PlainTextResponse("unknown", status_code=404)
        return PlainTextResponse("ok", status_code=200)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
