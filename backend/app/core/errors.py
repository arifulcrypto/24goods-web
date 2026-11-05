"""Consistent error envelope: ``{"error": {"code": "...", "message": "..."}}``.

Framework-free on purpose: the delivery service imports it without FastAPI/pydantic.
FastAPI handlers live in :mod:`app.core.error_handlers`.
"""

from __future__ import annotations


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


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}
