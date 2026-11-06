"""Self-hosted proof-of-work (no third-party captcha: China reachability + privacy).

Challenge (opaque to the client)::

    v1.<scope>.<bits>.<expires_unix>.<rand_hex16>.<mac_hex32>

``mac = HMAC-SHA256(APP_POW_KEY, "v1.<scope>.<bits>.<expires_unix>.<rand_hex16>")[:16]`` in hex.
The client finds a decimal integer ``nonce`` such that

    sha256(utf8("<challenge>:<nonce>"))

has at least ``bits`` leading zero bits. Challenges are bound to a scope, expire after
``APP_POW_TTL_S`` and are single use (Redis ``SET NX``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.errors import ApiError

SCOPES = ("instant", "register", "lookup", "login")
_NONCE = re.compile(r"^[0-9]{1,16}$")
_CHALLENGE = re.compile(r"^v1\.[a-z]{3,10}\.[0-9]{1,2}\.[0-9]{10,12}\.[0-9a-f]{16}\.[0-9a-f]{32}$")


class PowRequired(ApiError):
    """Proof of work required"""

    status, code = 428, "pow_required"


class PowInvalid(ApiError):
    """Proof of work invalid, expired or already used"""

    status, code = 422, "pow_invalid"


class PowIn(BaseModel):
    challenge: str = Field(max_length=200)
    nonce: str = Field(max_length=16)


def _mac(key: bytes, body: str) -> str:
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()[:32]


def issue(
    settings: Settings, scope: str, *, now: float | None = None, extra_bits: int = 0
) -> dict[str, Any]:
    """``extra_bits`` raises the difficulty (signed into the challenge); verify accepts any
    signed value >= the base."""
    exp = int((now if now is not None else time.time()) + settings.pow_ttl_s)
    bits = min(settings.pow_bits + max(0, extra_bits), 40)
    body = f"v1.{scope}.{bits}.{exp}.{secrets.token_hex(8)}"
    return {
        "challenge": f"{body}.{_mac(settings.pow_secret(), body)}",
        "bits": bits,
        "expires_at": exp,
    }


def leading_zero_bits(digest: bytes) -> int:
    n = int.from_bytes(digest, "big")
    return len(digest) * 8 - n.bit_length()


def solve(challenge: str, bits: int) -> str:
    """Reference solver (tests, tooling). The browser uses its own implementation."""
    nonce = 0
    prefix = challenge + ":"
    while True:
        digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
        if leading_zero_bits(digest) >= bits:
            return str(nonce)
        nonce += 1


async def verify(
    settings: Settings, redis: Redis, scope: str, pow_in: PowIn | None, *, now: float | None = None
) -> None:
    """Raise :class:`PowRequired` (missing) or :class:`PowInvalid` (anything else wrong)."""
    if pow_in is None:
        raise PowRequired("Proof of work required")
    ch, nonce = pow_in.challenge, pow_in.nonce
    if not _CHALLENGE.match(ch) or not _NONCE.match(nonce):
        raise PowInvalid()
    body, _, mac = ch.rpartition(".")
    if not hmac.compare_digest(mac, _mac(settings.pow_secret(), body)):
        raise PowInvalid()
    _, ch_scope, bits_s, exp_s, _ = body.split(".")
    if ch_scope != scope or int(exp_s) < (now if now is not None else time.time()):
        raise PowInvalid()
    if int(bits_s) < settings.pow_bits:  # a stale, easier challenge (config was raised)
        raise PowInvalid()
    digest = hashlib.sha256(f"{ch}:{nonce}".encode()).digest()
    if leading_zero_bits(digest) < int(bits_s):
        raise PowInvalid()
    # Single use: only a verified solution burns the challenge.
    fresh = await redis.set(
        "pow:used:" + hashlib.sha256(ch.encode()).hexdigest()[:32],
        b"1",
        nx=True,
        ex=settings.pow_ttl_s + 60,
    )
    if not fresh:
        raise PowInvalid()
