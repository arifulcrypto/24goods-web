"""Small uncompressed payment responses and bounded exact numeric tokens."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx


def bounded_decimal(value: str | int | Decimal) -> Decimal:
    raw = str(value)
    if len(raw) > 64:
        raise ValueError("numeric token too long")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise ValueError("invalid numeric token") from None
    parts = number.as_tuple()
    if (
        not number.is_finite()
        or len(parts.digits) > 40
        or not isinstance(parts.exponent, int)
        or not -18 <= parts.exponent <= 18
        or number.adjusted() > 18
    ):
        raise ValueError("numeric token outside payment bounds")
    return number


def bounded_integer(value: str) -> int:
    if len(value.lstrip("-")) > 19:
        raise ValueError("integer outside payment bounds")
    return int(value)


async def response_bytes(
    response: httpx.Response, maximum: int, *, body_statuses: frozenset[int] = frozenset()
) -> bytes:
    """Bounded body read. Non-2xx raises, except ``body_statuses``: gateways that answer an
    application-level error with a JSON body on a 4xx (e.g. EPay forks: 404 + "order not found")
    hand that body to the caller's parser, which still decides what it means."""
    if response.status_code not in body_statuses:
        response.raise_for_status()
    # Never let a small compressed response expand before the cap can inspect its bytes.
    if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        raise ValueError("compressed payment responses are not supported")
    length = response.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > maximum):
        raise ValueError("payment response too large")
    if response.is_stream_consumed:  # deterministic in-process test transports may preload
        raw = response.content
        if len(raw) > maximum:
            raise ValueError("payment response too large")
        return raw
    raw = bytearray()
    async for chunk in response.aiter_raw(chunk_size=min(maximum + 1, 65536)):
        if len(raw) + len(chunk) > maximum:
            raise ValueError("payment response too large")
        raw.extend(chunk)
    return bytes(raw)


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")
