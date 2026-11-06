"""FastAPI exception handlers mapping everything onto the contract error envelope."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ApiError, error_body

log = logging.getLogger("app.errors")

_STATUS_CODES = {
    400: "validation",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "validation",
    409: "conflict",
    413: "validation",
    415: "validation",
    422: "validation",
    429: "rate_limited",
}


def error_response(
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(code, message, extra), status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.headers, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query"))
        msg = f"{loc}: {first.get('msg', 'invalid')}" if loc else "Invalid request"
        return error_response(422, "validation", msg[:200])

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "code", None) or _STATUS_CODES.get(
            exc.status_code, "internal" if exc.status_code >= 500 else "validation"
        )
        detail = exc.detail if isinstance(exc.detail, str) else code
        return error_response(exc.status_code, code, detail, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled error request_id=%s type=%s",
            getattr(request.state, "request_id", "-"),
            type(exc).__name__,
            exc_info=exc,
        )
        return error_response(500, "internal", "Internal server error")
