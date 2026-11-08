"""易支付 (EPay) protocol: MD5 signing + hardened notify verification.

ZPay and most "彩虹易支付"-compatible gateways speak this protocol.
Signature: md5( "a=1&b=2..." (sorted by key, excluding sign / sign_type /
empty values) + merchant_key ), lowercase hex.

MD5 here is the gateway's protocol, not our choice; we compensate by never
trusting the notify alone (see verify_notify + docs/architecture.md §7):
signature -> merchant id -> status -> exact amount -> order state -> idempotency,
and the caller SHOULD additionally re-query the gateway server-to-server.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

EXCLUDED = {"sign", "sign_type"}


def canonical_string(params: Mapping[str, str]) -> str:
    items = sorted(
        (k, str(v)) for k, v in params.items() if k not in EXCLUDED and v not in (None, "")
    )
    return "&".join(f"{k}={v}" for k, v in items)


def sign(params: Mapping[str, str], key: str) -> str:
    # usedforsecurity=False: MD5 is mandated by the external EPay protocol.
    return hashlib.md5((canonical_string(params) + key).encode(), usedforsecurity=False).hexdigest()


def verify_signature(params: Mapping[str, str], key: str) -> bool:
    given = str(params.get("sign", "")).lower()
    if len(given) != 32 or any(c not in "0123456789abcdef" for c in given):
        return False
    if not given or not key:
        return False
    # Pin the algorithm: never let the caller choose (no RSA->MD5 downgrade).
    if params.get("sign_type", "MD5") != "MD5":
        return False
    return hmac.compare_digest(given, sign(params, key))


@dataclass(frozen=True)
class ExpectedOrder:
    out_trade_no: str
    merchant_id: str
    amount: Decimal  # exact amount we asked the gateway to collect


class NotifyRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def verify_notify(params: Mapping[str, str], key: str, expected: ExpectedOrder) -> str:
    """Validate an async notify. Returns the gateway trade_no on success.

    Raises NotifyRejected with a machine-readable reason otherwise. The HTTP
    handler must still: (1) run this inside a DB transaction holding
    SELECT ... FOR UPDATE on the order, (2) rely on UNIQUE(channel, trade_no)
    for idempotency, (3) only then enqueue delivery via the outbox table.
    """
    if not verify_signature(params, key):
        raise NotifyRejected("bad_signature")
    if params.get("pid") != expected.merchant_id:
        raise NotifyRejected("merchant_mismatch")
    if params.get("out_trade_no") != expected.out_trade_no:
        raise NotifyRejected("order_mismatch")
    if params.get("trade_status") != "TRADE_SUCCESS":
        raise NotifyRejected("not_paid")
    try:
        paid = Decimal(str(params.get("money", "")))
    except InvalidOperation as exc:
        raise NotifyRejected("bad_amount") from exc
    if paid != expected.amount:
        raise NotifyRejected("amount_mismatch")
    trade_no = params.get("trade_no", "")
    if not trade_no:
        raise NotifyRejected("missing_trade_no")
    return trade_no


def gateway_trade_key(instance_id: str, upstream_trade_no: str) -> str:
    """Fixed 64-character collision-resistant identity; the raw ID remains audit metadata."""
    if instance_id or len(upstream_trade_no) > 64:
        return hashlib.sha256((instance_id + "\0" + upstream_trade_no).encode()).hexdigest()
    return upstream_trade_no
