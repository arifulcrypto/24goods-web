"""Session/tenant/target-bound challenges with atomic initiation reservations.

A reservation whose result is uncertain is never retried by initiating another provider
request. Existing payment-status/reconciliation routes remain available. Redis is an abuse
clearance store; the payment modules retain durable financial truth.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import pow as powmod
from app.core.errors import ApiError, Conflict, InvalidState, NotFound, Unavailable
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.core.turnstile import require_turnstile
from app.models import Checkout, Order, User
from app.modules.content.site import tenant_settings
from app.modules.payments.channels import channel_cfg

TTL_S = 180
RESERVATION_TTL_S = 86400
CLEARANCE_TTL_S = 1800
PRESET_BITS = {"low": 0, "standard": 2, "high": 4}
_verify_slots = asyncio.Semaphore(4)
IDENT = re.compile(r"^[a-zA-Z0-9_-]{16,64}$")


class ChallengeRequired(ApiError):
    status, code = 428, "challenge_required"


class ChallengeInvalid(ApiError):
    status, code = 422, "challenge_invalid"


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def policy(ts: dict) -> dict:
    sec = section(ts, "security")
    return {
        "payment_initiation": sec["payment_challenge_enabled"],
        "site_visit": sec["site_visit_enabled"],
        "preset": sec["challenge_preset"],
    }


def policy_binding(ts: dict) -> str:
    sec = section(ts, "security")
    return digest(
        canonical(
            {
                "policy": policy(ts),
                "turnstile": sec["turnstile_enabled"],
                "actions": sec["turnstile_actions"],
                "site_key": sec["turnstile_site_key"],
                "payments": ts.get("payments", {}),
            }
        )
    )


def host(request: Request) -> str:
    return (request.url.hostname or "").lower()


def session_binding(request: Request, res: Resources) -> str:
    token = res.sessions.token_from(request.cookies)
    if not token:
        raise ChallengeInvalid("An authenticated session is required")
    return digest(token)


def cookie_name(res: Resources, kind: str) -> str:
    return ("__Host-" if res.settings.cookie_secure else "") + "g24_" + kind


def browser_binding(request: Request, res: Resources) -> str | None:
    value = request.cookies.get(cookie_name(res, "visitor"))
    return value if value and re.fullmatch(r"[0-9a-f]{64}", value) else None


def marker(target: Order | Checkout, orders: list[Order] | None = None) -> str:
    def line(row: Order) -> dict:
        return {
            "id": str(row.id),
            "version": row.version,
            "amount": row.amount_minor,
            "subtotal": row.subtotal_minor,
            "discount": row.discount_minor,
            "currency": row.currency,
            "qty": row.qty,
            "channel": row.channel,
            "status": row.status,
            "fee_permille": row.fee_permille,
        }

    return digest(
        canonical(
            {
                "id": str(target.id),
                "amount": target.amount_minor,
                "currency": target.currency,
                "status": target.status,
                "channel": target.channel,
                "updated_at": str(target.updated_at),
                "orders": [line(row) for row in sorted(orders, key=lambda row: row.id)]
                if orders is not None
                else [line(target)],
            }
        )
    )


def check_marker(
    target: Order | Checkout, orders: list[Order] | None, expected: str | None
) -> None:
    if expected is not None and not hmac.compare_digest(marker(target, orders), expected):
        raise ChallengeInvalid("The order changed; request a new challenge")


async def context(
    db: AsyncSession, tenant: uuid.UUID, user: User, target: str, ref: str, channel: str
) -> dict:
    if target == "checkout":
        row = await db.scalar(
            select(Checkout)
            .where(Checkout.checkout_no == ref, Checkout.tenant_id == tenant)
            .execution_options(populate_existing=True)
        )
        members = (
            list(
                await db.scalars(
                    select(Order)
                    .where(Order.checkout_id == row.id, Order.tenant_id == tenant)
                    .order_by(Order.id)
                    .execution_options(populate_existing=True)
                )
            )
            if row
            else []
        )
    else:
        try:
            ident = uuid.UUID(ref)
        except ValueError:
            ident = None
        row = await db.scalar(
            select(Order)
            .where(Order.id == ident if ident else Order.order_no == ref, Order.tenant_id == tenant)
            .execution_options(populate_existing=True)
        )
        members = None
    if row is None or row.buyer_id != user.id:
        raise NotFound("Payment not found")
    if row.expires_at is not None and row.expires_at <= datetime.now(timezone.utc):
        raise InvalidState("Order has expired")
    if row.status != "pending_payment":
        raise InvalidState("Order is not awaiting payment")
    if target != "checkout" and row.checkout_id is not None:
        raise InvalidState("Pay this order through its checkout")
    return {
        "id": str(row.id),
        "target": target,
        "ref": ref,
        "channel": channel,
        "marker": marker(row, members),
        "amount": row.amount_minor,
        "basis": [
            {
                "id": str(item.id),
                "subtotal": item.subtotal_minor,
                "discount": item.discount_minor,
                "qty": item.qty,
                "currency": item.currency,
            }
            for item in members or [row]
        ],
    }


def pow_settings(res: Resources):  # type: ignore[no-untyped-def]
    return res.settings.model_copy(
        update={"pow_bits": min(max(res.settings.pow_bits, 8), 20), "pow_ttl_s": TTL_S}
    )


def binding(
    request: Request,
    res: Resources,
    tenant: uuid.UUID,
    ts: dict,
    action: str,
    browser: str | None,
    user: User | None,
    ctx: dict | None,
) -> dict:
    return {
        "tenant": str(tenant),
        "host": host(request),
        "action": action,
        "session": session_binding(request, res)
        if action == "payment_initiation"
        else digest(browser or ""),
        "user": str(user.id) if user else None,
        "policy": policy_binding(ts),
        "context": ctx,
    }


async def issue(
    request: Request,
    res: Resources,
    db: AsyncSession,
    tenant: uuid.UUID,
    body: Any,
    user: User | None,
    browser: str | None,
) -> dict:
    ts = await tenant_settings(db, tenant)
    if not policy(ts)[body.action]:
        return {"enabled": False, "action": body.action}
    ctx = None
    if body.action == "payment_initiation":
        if user is None or not body.target or not body.ref or not body.channel:
            raise ChallengeInvalid("An owned payment target and method are required")
        ctx = await context(db, tenant, user, body.target, body.ref, body.channel)
        if not channel_cfg(ts, body.channel).enabled:
            raise Unavailable("Payment channel is not available")
    elif not browser:
        raise ChallengeInvalid("Browser binding is required")
    settings = pow_settings(res)
    challenge = powmod.issue(
        settings, "pay" if ctx else "visit", extra_bits=PRESET_BITS[policy(ts)["preset"]]
    )
    ident = secrets.token_hex(16)
    metadata = binding(request, res, tenant, ts, body.action, browser, user, ctx)
    metadata["challenge"] = challenge["challenge"]
    metadata["expires_at"] = int(time.time()) + TTL_S
    await res.redis.set("pow:gate:" + ident, canonical(metadata), ex=TTL_S)
    sec = section(ts, "security")
    return {
        "enabled": True,
        "id": ident,
        "action": body.action,
        "pow": challenge,
        "expires_at": metadata["expires_at"],
        "turnstile": {
            "required": sec["turnstile_enabled"] and body.action in sec["turnstile_actions"],
            "site_key": sec["turnstile_site_key"],
            "action": body.action,
        },
    }


async def proof(
    request: Request, res: Resources, tenant: uuid.UUID, ts: dict, body: Any, expected: dict
) -> tuple[str, str]:
    ident = body.challenge_id
    if not ident or not IDENT.fullmatch(ident):
        raise ChallengeRequired("A fresh challenge is required")
    key = "pow:gate:" + ident
    raw = await res.redis.get(key)
    if not raw:
        raise ChallengeInvalid("Challenge expired or already used")
    raw = raw.decode() if isinstance(raw, bytes) else raw
    try:
        saved = json.loads(raw)
    except ValueError:
        raise ChallengeInvalid() from None
    submitted = body.pow
    if (
        not isinstance(saved, dict)
        or saved.get("expires_at", 0) < time.time()
        or {k: saved.get(k) for k in expected} != expected
        or submitted is None
        or saved.get("challenge") != submitted.challenge
    ):
        raise ChallengeInvalid("Challenge binding changed")
    if _verify_slots.locked():
        raise Unavailable("Challenge verification is busy", headers={"Retry-After": "2"})
    async with _verify_slots:
        await require_turnstile(request, ts, expected["action"], body.turnstile_token)
    await powmod.verify(
        pow_settings(res),
        res.redis,
        "pay" if expected["action"] == "payment_initiation" else "visit",
        submitted,
    )
    return key, raw


_CLAIM = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local previous = redis.call('GET', KEYS[2])
if previous then
  local parsed = cjson.decode(previous)
  if parsed.state ~= 'done' then return -1 end
  if previous ~= ARGV[4] then return -1 end
end
redis.call('DEL', KEYS[1])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""
_FINISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


async def pay(
    request: Request,
    res: Resources,
    db: AsyncSession,
    user: User,
    target: str,
    ref: str,
    body: Any,
    initiate: Callable[[str | None], Awaitable[str]],
) -> str:
    ts = await tenant_settings(db, user.tenant_id)
    if not policy(ts)["payment_initiation"]:
        return await initiate(None)
    if not body.idempotency_key or not IDENT.fullmatch(body.idempotency_key):
        raise ChallengeRequired("A payment idempotency key is required")
    ctx = await context(db, user.tenant_id, user, target, ref, body.channel)
    if not channel_cfg(ts, body.channel).enabled:
        raise Unavailable("Payment channel is not available")
    expected = binding(request, res, user.tenant_id, ts, "payment_initiation", None, user, ctx)
    reservation_key = "pow:payment-reservation:" + str(user.tenant_id) + ":" + ctx["id"]
    idem = digest(body.idempotency_key)
    previous_raw = await res.redis.get(reservation_key)
    if previous_raw:
        previous = json.loads(previous_raw)
        if previous.get("idempotency") == idem:
            if previous.get("binding") != expected:
                raise ChallengeInvalid("The payment context changed")
            if previous.get("state") == "done":
                return str(previous["pay_url"])
            raise Conflict(
                "Payment initiation outcome is uncertain; check payment status",
                code="payment_attempt_unknown",
            )
        if previous.get("state") != "done":
            raise Conflict(
                "Payment initiation is already reserved; check payment status",
                code="payment_attempt_unknown",
            )
    await db.commit()  # no database transaction is held across external verification
    gate_key, raw = await proof(request, res, user.tenant_id, ts, body, expected)
    pending = canonical({"binding": expected, "idempotency": idem, "state": "inflight"})
    result = await res.redis.eval(
        _CLAIM, 2, gate_key, reservation_key, raw, pending, RESERVATION_TTL_S, previous_raw or ""
    )
    if result != 1:
        raise Conflict("Payment initiation is already reserved", code="payment_attempt_unknown")
    try:
        url = await initiate(ctx["marker"])
        final = await context(db, user.tenant_id, user, target, ref, body.channel)
        if final["basis"] != ctx["basis"]:
            raise ChallengeInvalid("The order changed while payment was prepared")
        final_binding = {**expected, "context": final}
        completed = canonical(
            {"binding": final_binding, "idempotency": idem, "state": "done", "pay_url": url}
        )
        if not await res.redis.eval(
            _FINISH, 1, reservation_key, pending, completed, RESERVATION_TTL_S
        ):
            raise Conflict("Payment reservation changed", code="payment_attempt_unknown")
        return url
    except BaseException:
        unknown = canonical({"binding": expected, "idempotency": idem, "state": "unknown"})
        await res.redis.eval(_FINISH, 1, reservation_key, pending, unknown, RESERVATION_TTL_S)
        raise


def clearance(request: Request, res: Resources, tenant: uuid.UUID, ts: dict) -> bool:
    token = request.cookies.get(cookie_name(res, "clearance"), "")
    if len(token) > 2048:
        return False
    try:
        encoded, mac = token.rsplit(".", 1)
        expected = hmac.new(
            res.settings.pow_secret(), ("siteclear:v1:" + encoded).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(mac, expected):
            return False
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return (
            data.get("tenant") == str(tenant)
            and data.get("host") == host(request)
            and data.get("policy") == policy_binding(ts)
            and data.get("expires", 0) > time.time()
            and data.get("browser") == digest(browser_binding(request, res) or "")
        )
    except (ValueError, TypeError, AttributeError):
        return False


def sign_clearance(
    request: Request, res: Resources, tenant: uuid.UUID, ts: dict, browser: str
) -> str:
    payload = canonical(
        {
            "tenant": str(tenant),
            "host": host(request),
            "policy": policy_binding(ts),
            "browser": digest(browser),
            "expires": int(time.time()) + CLEARANCE_TTL_S,
        }
    )
    encoded = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    mac = hmac.new(
        res.settings.pow_secret(), ("siteclear:v1:" + encoded).encode(), hashlib.sha256
    ).hexdigest()
    return encoded + "." + mac
