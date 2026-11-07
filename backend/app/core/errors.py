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
        extra: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message or self.__class__.__doc__ or self.code
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.headers = headers or {}
        self.extra = extra or {}  # machine-readable fields echoed in the error envelope


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


class AlreadyPaid(ApiError):
    """The order was already paid"""

    status, code = 409, "already_paid"


class PayViaCheckout(ApiError):
    """This order is part of a cart checkout; pay the checkout"""

    status, code = 409, "pay_via_checkout"


class FeeChannelMismatch(ApiError):
    """The payment fee of this order does not match the chosen channel"""

    status, code = 409, "fee_channel_mismatch"


class BuyerInputRequired(ApiError):
    """This product needs the order information"""

    status, code = 422, "buyer_input_required"


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


def error_body(
    code: str, message: str, extra: dict[str, str] | None = None
) -> dict[str, dict[str, object]]:
    """``{"error": {code, message}}``; machine-readable ``extra`` fields (e.g. ``checkout_no``,
    ``product_id``) are placed flat in ``error`` and mirrored under ``error.data``."""
    body: dict[str, object] = {"code": code, "message": message}
    if extra:
        body.update(extra)
        body["data"] = dict(extra)
    return {"error": body}


class AccountSuspended(ApiError):
    """Your account has been suspended"""

    status, code = 403, "account_suspended"
