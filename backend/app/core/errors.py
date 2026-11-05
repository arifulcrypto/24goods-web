"""Consistent error envelope: ``{"error": {"code": "...", "message": "..."}}``."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")


class ApiError(Exception):
    status: int = 400
    code: str = "validation"

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message or self.__class__.__doc__ or self.code
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.headers = headers or {}


class Unauthorized(ApiError):
    """Authentication required"""

    status, code = 401, "unauthorized"


class Forbidden(ApiError):
    """Forbidden"""

    status, code = 403, "forbidden"


class NotFound(ApiError):
    """Not found"""

    status, code = 404, "not_found"


class Validation(ApiError):
    """Invalid request"""

    status, code = 422, "validation"


class Conflict(ApiError):
    """Conflict"""

    status, code = 409, "conflict"


class InvalidState(ApiError):
    """Operation not allowed in the current state"""

    status, code = 409, "invalid_state"


class OutOfStock(ApiError):
    """Not enough stock"""

    status, code = 409, "out_of_stock"


class QuoteExpired(ApiError):
    """Quote has expired"""

    status, code = 409, "quote_expired"


class UploadRejected(ApiError):
    """Upload rejected"""

    status, code = 422, "upload_rejected"


class RateLimited(ApiError):
    """Too many requests"""

    status, code = 429, "rate_limited"

    def __init__(self, retry_after: int = 60, message: str = ""):
        super().__init__(message, headers={"Retry-After": str(max(1, retry_after))})


class Unavailable(ApiError):
    """Service temporarily unavailable"""

    status, code = 503, "unavailable"


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


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.headers)

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
