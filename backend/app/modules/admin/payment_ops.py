"""Payment-channel diagnostics (review-v9 §1.3, §2): staff resolve of stuck attempts, a server-side
self-test per gateway and a derived health state. Secrets are never echoed."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import safefetch
from app.core.audit import audit
from app.core.deps import client_ip, get_db, get_res, require_admin
from app.core.errors import InvalidState, NotFound
from app.core.ratelimit import enforce
from app.core.resources import Resources
from app.models import PaymentAttempt, PaymentGateway, User
from app.modules.admin.payment_gateways import session_admin
from app.modules.operations.service import record_event
from app.modules.payments.gateways import client_for
from app.payments import bepusdt
from app.payments.zpay import ZPayQueryError, ZPayUnreachable

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

CONNECT_TIMEOUT_S = 5.0
HEALTH_WINDOW = timedelta(minutes=30)
TEST_TTL_S = 24 * 3600
PREPARING_GRACE = timedelta(minutes=2)


async def tcp_connect_ms(host: str, port: int) -> int:
    """TCP connect latency in ms; raises OSError / TimeoutError when unreachable."""
    started = time.monotonic()
    _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT_S)
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return int((time.monotonic() - started) * 1000)


def _key(tid: uuid.UUID, ref: str) -> str:
    return f"paytest:{tid}:{ref}"


async def _remember(res: Resources, tid: uuid.UUID, ref: str, result: dict[str, Any]) -> None:
    slim = {"ok": result["ok"], "at": result["at"], "latency_ms": result.get("latency_ms")}
    try:
        await res.redis.set(_key(tid, ref), json.dumps(slim), ex=TEST_TTL_S)
    except Exception:  # health is advisory
        log.warning("could not remember gateway self-test")


async def _last_test(res: Resources, tid: uuid.UUID, ref: str) -> dict[str, Any] | None:
    try:
        raw = await res.redis.get(_key(tid, ref))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def derive_health(
    last_test: dict[str, Any] | None, unreachable_at: list[datetime], unknown: int, now: datetime
) -> str:
    """healthy / degraded / unreachable. A newer successful self-test outweighs older failures."""
    ok_at: datetime | None = None
    test_failed_recent = False
    if last_test:
        at = datetime.fromisoformat(last_test["at"])
        if last_test["ok"]:
            ok_at = at
        elif now - at <= HEALTH_WINDOW:
            test_failed_recent = True
    if test_failed_recent or any(ok_at is None or t > ok_at for t in unreachable_at):
        return "unreachable"
    if unknown > 0:
        return "degraded"
    return "healthy"


async def gateway_health(
    db: AsyncSession, res: Resources, tid: uuid.UUID, row: PaymentGateway
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = list(
        await db.scalars(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.provider_id == row.id,
                PaymentAttempt.updated_at >= now - HEALTH_WINDOW,
            )
            .limit(200)
        )
    )
    unreachable = [
        a.updated_at
        for a in rows
        if a.status == "failed" and (a.data or {}).get("reason") == "gateway_unreachable"
    ]
    unknown = sum(1 for a in rows if a.status in ("unknown", "preparing"))
    last = await _last_test(res, tid, str(row.id))
    return {"status": derive_health(last, unreachable, unknown, now), "last_test": last}


# ----------------------------------------------------------------------------- resolve attempt
class ResolveIn(BaseModel):
    outcome: Literal["not_paid"]
    note: str = Field(min_length=10, max_length=500)


@router.post("/payment-attempts/{attempt_id}/resolve")
async def resolve_attempt(
    attempt_id: uuid.UUID,
    body: ResolveIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Staff confirmed in the gateway console that no charge exists for an attempt whose outcome
    is unknown. The attempt becomes ``failed`` so cancel/pay can proceed; a payment that still
    turns up later takes the normal late-payment path (manual review)."""
    note = body.note.strip()
    if len(note) < 10:
        raise InvalidState("A note of at least 10 characters is required")
    att = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt_id, PaymentAttempt.tenant_id == admin.tenant_id)
        .with_for_update()
    )
    if att is None:
        raise NotFound("Payment attempt not found")
    if att.status not in ("unknown", "preparing"):
        raise InvalidState("Only an attempt with an unknown outcome can be resolved")
    if att.status == "preparing" and datetime.now(timezone.utc) - att.created_at < PREPARING_GRACE:
        raise InvalidState("This attempt is still being prepared; wait a moment and retry")
    before = {"status": att.status}
    att.status = "failed"
    att.data = {
        **(att.data or {}),
        "reason": "resolved_not_paid",
        "resolved_by": str(admin.id),
        "resolved_note": note,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    ref = "payment_attempt:" + str(att.id)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="payment.attempt_resolved",
        actor_id=admin.id,
        actor_role=admin.role,
        target=ref,
        before=before,
        after={"status": "failed", "outcome": body.outcome},
        ip=client_ip(request),
        detail={"note": note, "local_ref": att.local_ref},
        severity="warning",
    )
    await record_event(
        db,
        tenant_id=admin.tenant_id,
        environment="payment",
        family="payment",
        action="payment.attempt_resolved",
        outcome="success",
        reason="staff_not_paid",
        actor_id=admin.id,
        resource_id=str(att.id),
        ip=client_ip(request),
    )
    out = {"id": str(att.id), "status": "failed", "local_ref": att.local_ref}
    await db.commit()
    return out


# ------------------------------------------------------------------------------ gateway test
def _result(ok: bool, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "at": datetime.now(timezone.utc).isoformat(), **extra}


@router.post("/payment-gateways/bepusdt/test")
async def test_bepusdt(
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:test", str(admin.id), 20, 600)
    if not res.bepusdt.configured:
        raise NotFound("USDT gateway is not configured")
    parts = urlsplit(res.bepusdt.config.base_url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    result: dict[str, Any]
    try:
        latency = await tcp_connect_ms(host, port)
    except (OSError, asyncio.TimeoutError):
        result = _result(False, stage="connect", reachable=False, message="connection failed")
    else:
        try:
            info = await res.bepusdt.query_info("selftest-" + secrets.token_hex(8))
            msg = "unknown trade (expected)" if not info.found else "gateway answered"
            result = _result(True, stage="query", reachable=True, latency_ms=latency, message=msg)
        except bepusdt.BepusdtError as exc:
            result = _result(
                False,
                stage="query",
                reachable=True,
                latency_ms=latency,
                message=str(exc)[:200],
            )
    await _record_test(db, request, admin, "bepusdt", result)
    await _remember(res, admin.tenant_id, "bepusdt", result)
    return result


@router.post("/payment-gateways/{ref}/test")
async def test_gateway(
    ref: uuid.UUID,
    request: Request,
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    await enforce(res.redis, "gateway:test", str(admin.id), 20, 600)
    row = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == ref, PaymentGateway.tenant_id == admin.tenant_id
        )
    )
    if row is None:
        raise NotFound("Gateway not found")
    client, gateway_url, tid = client_for(res, row), row.gateway, admin.tenant_id
    await db.commit()  # no transaction held across the network calls
    result: dict[str, Any]
    try:
        target = await safefetch.vet_url(gateway_url)
        latency = await tcp_connect_ms(target.ip, target.port)
    except (OSError, asyncio.TimeoutError, safefetch.FetchError):
        result = _result(False, stage="connect", reachable=False, message="connection failed")
    else:
        try:
            ans = await client.probe_order("SELFTEST" + secrets.token_hex(8).upper())
            # a well-formed answer for a non-existent order proves reachability and that the
            # merchant id / key format was accepted well enough to be parsed
            result = _result(
                True,
                stage="query",
                reachable=True,
                latency_ms=latency,
                gateway_code=ans["code"],
                message=ans["msg"],
            )
        except ZPayUnreachable:
            result = _result(False, stage="query", reachable=False, message="connection failed")
        except ZPayQueryError:
            result = _result(
                False,
                stage="query",
                reachable=True,
                latency_ms=latency,
                message="gateway answered with an unexpected response",
            )
    row_ref = str(ref)
    await _record_test(db, request, admin, row_ref, result, tid)
    await _remember(res, tid, row_ref, result)
    return result


async def _record_test(
    db: AsyncSession,
    request: Request,
    admin: User,
    ref: str,
    result: dict[str, Any],
    tid: uuid.UUID | None = None,
) -> None:
    await audit(
        db,
        tenant_id=tid or admin.tenant_id,
        action="payment.gateway_tested",
        actor_id=admin.id,
        actor_role=admin.role,
        target="payment_gateway:" + ref,
        ip=client_ip(request),
        detail={
            "ok": result["ok"],
            "stage": result.get("stage"),
            "reachable": result.get("reachable"),
        },
        severity="info" if result["ok"] else "warning",
    )
    await db.commit()


@router.get("/payment-gateways-health")
async def health(
    admin: User = Depends(session_admin),
    db: AsyncSession = Depends(get_db),
    res: Resources = Depends(get_res),
) -> dict:
    """Per-gateway health; the settings page polls this (cheap: one query per gateway)."""
    rows = await db.scalars(
        select(PaymentGateway)
        .where(PaymentGateway.tenant_id == admin.tenant_id)
        .order_by(PaymentGateway.created_at.desc())
        .limit(50)
    )
    items = {str(r.id): await gateway_health(db, res, admin.tenant_id, r) for r in list(rows)}
    usdt = None
    if res.bepusdt.configured:
        last = await _last_test(res, admin.tenant_id, "bepusdt")
        usdt = {"status": derive_health(last, [], 0, datetime.now(timezone.utc)), "last_test": last}
    return {"gateways": items, "bepusdt": usdt}
