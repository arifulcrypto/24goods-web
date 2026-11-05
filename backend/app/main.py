"""FastAPI entrypoint. MVP seed: health check + security headers only."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response

from app.core.config import settings

app = FastAPI(
    title="24goods API",
    docs_url="/docs" if settings.env != "prod" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.env != "prod" else None,
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response: Response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
