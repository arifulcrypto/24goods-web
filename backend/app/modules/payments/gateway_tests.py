"""Admin test payments (review-v10 section C).

A real payment at one gateway in a reserved out_trade_no namespace (``T`` + 16 hex; orders use
``G``, checkouts ``C``). Never linked to an order/checkout, never fulfils, never touches stock,
resellers or the sales ledger. A paid test books ``gateway_clearing:zpay`` against a dedicated
``test_receipts`` account so clearing still reconciles without mixing into sales.

The notify path is the normal one: signature with the test's own gateway key, merchant id, exact
amount, server-to-server re-query, then a row lock + idempotent state change.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import NotFound, Unavailable, Validation
from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import PaymentGateway, PaymentTest
from app.modules.payments.gateways import _gateway_failed, client_for, mark_gateway_ok
from app.modules.payments.ledger import Line, post_txn
from app.modules.payments.settle import FAIL, SUCCESS, NotifyOutcome, safe_params
from app.modules.payments.usdt import qr_svg
from app.payments import epay
from app.payments.zpay import ZPayQueryError, ZPayRefused, ZPayUnreachable

TEST_PREFIX = "T"
TEST_TTL = timedelta(minutes=10)
REQUERY_MIN_INTERVAL_S = 3.0
MIN_MINOR, MAX_MINOR = 1, 1000  # 0.01 .. 10.00 CNY


def is_test_ref(out_trade_no: str) -> bool:
    return out_trade_no.startswith(TEST_PREFIX) and len(out_trade_no) == 17


def parse_amount(raw: str) -> int:
    """Minor units of a 0.01..10.00 amount with at most two decimals."""
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise Validation("Invalid amount") from None
    minor = value * 100
    if not value.is_finite() or minor != minor.to_integral_value():
        raise Validation("Amount can have at most 2 decimals")
    if not MIN_MINOR <= int(minor) <= MAX_MINOR:
        raise Validation("Test amount must be between 0.01 and 10.00")
    return int(minor)


def view(row: PaymentTest, label: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    data = row.data or {}
    kind = data.get("kind")
    left = (
        max(0, int((row.expires_at - now).total_seconds()))
        if row.expires_at and row.status == "pending"
        else 0
    )
    return {
        "id": str(row.id),
        "gateway_id": str(row.provider_id),
        "gateway_label": label,
        "out_trade_no": row.gateway_ref,
        "amount": str(to_decimal(row.amount_minor)),
        "currency": row.currency,
        "status": row.status,
        "qr_content": data.get("payload") if kind else None,
        "qr_svg": qr_svg(str(data["payload"])) if kind and row.status == "pending" else None,
        "redirect_url": data.get("payload") if kind == "redirect" else None,
        "reason": data.get("reason"),
        "message": data.get("message"),
        "seconds_left": left,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat(),
        "paid_at": row.paid_at.isoformat() if row.paid_at else None,
        "trade_no": row.trade_no,
    }


async def create(
    db: AsyncSession,
    res: Resources,
    *,
    tid: uuid.UUID,
    admin_id: uuid.UUID,
    admin_role: str,
    gateway_id: uuid.UUID,
    amount_minor: int,
    ip: str | None,
) -> dict[str, Any]:
    gw = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == gateway_id, PaymentGateway.tenant_id == tid
        )
    )
    if gw is None:
        raise NotFound("Gateway not found")
    client, mode, label = client_for(res, gw), gw.mode, gw.label
    ref = TEST_PREFIX + secrets.token_hex(8).upper()
    row = PaymentTest(
        tenant_id=tid,
        provider_id=gateway_id,
        local_ref=ref,
        gateway_ref=ref,
        amount_minor=amount_minor,
        currency="CNY",
        status="pending",
        created_by=admin_id,
        data={},
        expires_at=datetime.now(timezone.utc) + TEST_TTL,
    )
    db.add(row)
    await db.flush()
    test_id = row.id
    await audit(
        db,
        tenant_id=tid,
        action="payment.test_created",
        actor_id=admin_id,
        actor_role=admin_role,
        target="payment_test:" + str(test_id),
        ip=ip,
        detail={"provider_id": str(gateway_id), "amount_minor": amount_minor, "ref": ref},
    )
    await db.commit()  # no transaction held across the gateway call
    data: dict[str, Any]
    status = "pending"
    try:
        if mode == "redirect":
            data = {
                "kind": "redirect",
                "payload": client.build_pay_url(ref, "24goods test", to_decimal(amount_minor)),
            }
        else:
            data = await client.create_payment(
                ref, "24goods test", to_decimal(amount_minor), ip or ""
            )
    except ZPayRefused as exc:
        status, data = "failed", {"reason": "gateway_refused", "message": exc.gateway_message}
    except ZPayUnreachable:
        status, data = "failed", {"reason": "gateway_unreachable"}
    except (ZPayQueryError, asyncio.TimeoutError):
        status, data = "failed", {"reason": "gateway_unknown"}
    row = await db.scalar(
        select(PaymentTest)
        .where(PaymentTest.id == test_id, PaymentTest.tenant_id == tid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover
        raise Unavailable("Test payment unavailable")
    row.status, row.data = status, data
    if status == "failed" and data["reason"] in ("gateway_refused", "gateway_unreachable"):
        await _gateway_failed(db, tid, gateway_id, data["reason"])
    await db.commit()
    return view(row, label)


async def refresh(
    db: AsyncSession, res: Resources, *, tid: uuid.UUID, test_id: uuid.UUID
) -> dict[str, Any]:
    """Current state; while pending, re-queries the gateway at most every 3 seconds."""
    row = await db.scalar(
        select(PaymentTest).where(PaymentTest.id == test_id, PaymentTest.tenant_id == tid)
    )
    if row is None:
        raise NotFound("Test payment not found")
    gw = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == row.provider_id, PaymentGateway.tenant_id == tid
        )
    )
    label = gw.label if gw else None
    now = datetime.now(timezone.utc)
    if (
        gw is None
        or row.status != "pending"
        or (row.checked_at and (now - row.checked_at).total_seconds() < REQUERY_MIN_INTERVAL_S)
    ):
        return view(row, label)
    expired = bool(row.expires_at and row.expires_at <= now)
    client, ref, amount_minor = client_for(res, gw), row.gateway_ref, row.amount_minor
    row.checked_at = now
    await db.commit()  # claim the slot, release locks before the network call
    try:
        q = await client.query_order(ref)
    except ZPayQueryError:
        q = None
    if q is not None and q.found and q.paid and q.out_trade_no == ref:
        if q.money == to_decimal(amount_minor) and q.trade_no:
            await mark_paid(db, tid=tid, test_id=test_id, trade_no=q.trade_no, source="requery")
    elif expired:
        locked = await db.scalar(
            select(PaymentTest)
            .where(PaymentTest.id == test_id, PaymentTest.tenant_id == tid)
            .with_for_update()
        )
        if locked is not None and locked.status == "pending":
            locked.status = "expired"
            await db.commit()
    row = await db.scalar(
        select(PaymentTest)
        .where(PaymentTest.id == test_id, PaymentTest.tenant_id == tid)
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover
        raise NotFound("Test payment not found")
    return view(row, label)


async def mark_paid(
    db: AsyncSession, *, tid: uuid.UUID, test_id: uuid.UUID, trade_no: str, source: str
) -> bool:
    """Idempotent: ``True`` only for the call that flips the row to paid."""
    row = await db.scalar(
        select(PaymentTest)
        .where(PaymentTest.id == test_id, PaymentTest.tenant_id == tid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status not in ("pending", "expired"):
        await db.rollback()
        return False
    row.status = "paid"
    row.paid_at = datetime.now(timezone.utc)
    row.trade_no = trade_no[:128]
    await post_txn(
        db,
        tenant_id=tid,
        currency=row.currency,
        order_id=None,
        memo=f"test payment {row.gateway_ref}",
        lines=[
            Line("gateway_clearing:zpay", row.amount_minor),
            Line("test_receipts", -row.amount_minor),
        ],
    )
    await mark_gateway_ok(db, tid, row.provider_id)
    await audit(
        db,
        tenant_id=tid,
        action="payment.test_paid",
        actor_role="gateway",
        target="payment_test:" + str(row.id),
        detail={
            "source": source,
            "amount_minor": row.amount_minor,
            "provider_id": str(row.provider_id),
        },
    )
    await db.commit()
    return True


async def list_recent(db: AsyncSession, tid: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = list(
        await db.scalars(
            select(PaymentTest)
            .where(PaymentTest.tenant_id == tid)
            .order_by(PaymentTest.created_at.desc())
            .limit(limit)
        )
    )
    labels = {
        g.id: g.label
        for g in await db.scalars(select(PaymentGateway).where(PaymentGateway.tenant_id == tid))
    }
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        v = view(r, labels.get(r.provider_id))
        if r.status == "pending" and r.expires_at and r.expires_at <= now:
            v["status"] = "expired"  # display only; the next refresh persists it
        out.append(v)
    return out


async def handle_test_notify(
    db: AsyncSession, res: Resources, tid: uuid.UUID, params: dict[str, str], ip: str | None
) -> NotifyOutcome:
    """Notify for a ``T`` out_trade_no. Same checks as an order notify; settles nothing else."""
    from app.modules.payments.notify import _security_event, requery_mismatch

    ref = params.get("out_trade_no", "")
    row = await db.scalar(
        select(PaymentTest).where(PaymentTest.gateway_ref == ref, PaymentTest.tenant_id == tid)
    )
    gw = (
        await db.scalar(
            select(PaymentGateway).where(
                PaymentGateway.id == row.provider_id, PaymentGateway.tenant_id == tid
            )
        )
        if row is not None
        else None
    )
    if row is None or gw is None:
        await _security_event(
            res.redis,
            db,
            tid,
            "payment.notify_unknown_order",
            ip,
            {"out_trade_no": ref[:32]},
        )
        return NotifyOutcome(FAIL)
    zpay = client_for(res, gw)
    res = replace(res, zpay=zpay)
    test_id, amount_minor = row.id, row.amount_minor
    if not zpay.verify_signature(params):
        await _security_event(
            res.redis,
            db,
            tid,
            "payment.notify_bad_signature",
            ip,
            {"out_trade_no": ref[:32]},
        )
        return NotifyOutcome(FAIL)
    expected = epay.ExpectedOrder(
        out_trade_no=ref, merchant_id=zpay.config.pid, amount=to_decimal(amount_minor)
    )
    try:
        trade_no = zpay.verify_notify(params, expected)
    except epay.NotifyRejected as rej:
        detail = {"reason": rej.reason, "params": safe_params(params)}
        if rej.reason == "not_paid":
            await db.rollback()
            return NotifyOutcome(SUCCESS)
        if rej.reason in ("merchant_mismatch", "order_mismatch", "bad_signature"):
            await _security_event(res.redis, db, tid, "payment.notify_rejected", ip, detail)
            return NotifyOutcome(FAIL)
        # amount mismatch etc.: a test never settles on a wrong amount; keep the evidence
        await db.rollback()
        await audit(
            db,
            tenant_id=tid,
            action="payment.test_rejected",
            actor_role="gateway",
            target="payment_test:" + str(test_id),
            ip=ip,
            detail=detail,
            severity="warning",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS)
    await db.rollback()  # no locks across the re-query
    try:
        bad = await requery_mismatch(res, params, trade_no, Decimal(params["money"]))
    except ZPayQueryError:
        return NotifyOutcome(FAIL)  # let the gateway retry
    if bad is not None:
        await audit(
            db,
            tenant_id=tid,
            action="payment.test_rejected",
            actor_role="gateway",
            target="payment_test:" + str(test_id),
            ip=ip,
            detail=bad,
            severity="warning",
        )
        await db.commit()
        return NotifyOutcome(SUCCESS)
    await mark_paid(db, tid=tid, test_id=test_id, trade_no=trade_no, source="notify")
    return NotifyOutcome(SUCCESS)
