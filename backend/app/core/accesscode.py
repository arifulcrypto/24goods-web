"""Order access codes (卡密查询): 10 Crockford base32 chars = 50 random bits.

Only ``HMAC-SHA256(lookup_key, order_no || 0x1e || code)`` is stored; the plaintext is returned
exactly once (order creation, or an explicit rotation by the owner).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 10
_VALID = re.compile(rf"^[{ALPHABET}]{{{LENGTH}}}$")
_FOLD = str.maketrans({"I": "1", "L": "1", "O": "0"})


def generate() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def normalize(raw: str) -> str | None:
    """Canonical form of user input (case, spaces, hyphens, I/L/O folding) or None."""
    code = re.sub(r"[\s-]", "", raw or "").upper().translate(_FOLD)
    return code if _VALID.match(code) else None


def digest(key: bytes, order_no: str, code: str) -> str:
    return hmac.new(key, order_no.encode() + b"\x1e" + code.encode(), hashlib.sha256).hexdigest()


def matches(key: bytes, order_no: str, code: str, stored: str | None) -> bool:
    if not stored:
        return False
    return hmac.compare_digest(digest(key, order_no, code), stored)
