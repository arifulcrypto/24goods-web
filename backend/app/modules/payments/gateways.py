"""Immutable tenant EPay configurations, reservations and original-provider routing."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox
from app.core.audit import audit
from app.core.errors import ChannelUnreachable, InvalidState, Unavailable, Validation
from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import Checkout, LegacyPayUrl, Order, PaymentAttempt, PaymentGateway, Tenant
from app.modules.content.site import tenant_settings
from app.modules.operations.service import record_event
from app.payments.zpay import (
    ZPayClient,
    ZPayConfig,
    ZPayQueryError,
    ZPayRefused,
    ZPayUnreachable,
)


def normalize_gateway(value: str) -> str:
    """Accept base or complete EPay endpoint, never credentials/query/fragment."""
    try:
        p = urlsplit(value.strip())
        port = p.port
    except ValueError:
        raise Validation("Invalid gateway URL") from None
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or port not in (None, 443)
        or any(ord(c) < 33 for c in value)
    ):
        raise Validation("A public HTTPS EPay gateway is required")
    path = p.path.rstrip("/")
    for suffix in ("/submit.php", "/mapi.php", "/api.php"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path and (".." in path or "\\" in path or "%" in path or "//" in path):
        raise Validation("Invalid gateway path")
    return urlunsplit(("https", p.netloc.lower(), path, "", ""))


async def configured(db: AsyncSession, res: Resources, tid: uuid.UUID) -> bool:
    ts = await tenant_settings(db, tid)
    if (ts.get("payments") or {}).get("epay_provider_id") or (
        res.settings.zpay_pid and res.settings.zpay_key
    ):
        return True
    return bool(
        await db.scalar(
            select(PaymentGateway.id)
            .where(PaymentGateway.tenant_id == tid, PaymentGateway.enabled.is_(True))
            .limit(1)
        )
    )


async def active_gateway(db: AsyncSession, tid: uuid.UUID) -> PaymentGateway | None:
    ts = await tenant_settings(db, tid)
    ref = (ts.get("payments") or {}).get("epay_provider_id")
    if not ref:
        return None
    row = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == uuid.UUID(ref), PaymentGateway.tenant_id == tid
        )
    )
    if row is None:
        raise Unavailable("Configured payment gateway is unavailable")
    return row


def client_for(res: Resources, row: PaymentGateway) -> ZPayClient:
    key = secretbox.unseal(
        res.settings.settings_secret(),
        row.tenant_id,
        "payment_gateway:" + str(row.id),
        row.key_ciphertext,
    )
    if not key:
        raise Unavailable("Payment credentials are unavailable")
    return ZPayClient(
        ZPayConfig(
            pid=row.merchant_id,
            key=key,
            gateway=row.gateway,
            api_base=row.gateway,
            notify_url=res.settings.notify_url,
            return_url=res.settings.return_url,
            safe_network=True,
            instance_id=str(row.id),
        ),
        res.http,
    )


async def routed_resources(
    db: AsyncSession, res: Resources, tid: uuid.UUID, gateway_ref: str
) -> tuple[Resources, str]:
    att = await db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.gateway_ref == gateway_ref, PaymentAttempt.tenant_id == tid
        )
    )
    if att is None or att.provider_id is None:
        if not await legacy_allowed(db, tid, gateway_ref):
            raise Unavailable("An original provider attempt is required")
        return res, gateway_ref
    row = await db.scalar(
        select(PaymentGateway).where(
            PaymentGateway.id == att.provider_id, PaymentGateway.tenant_id == tid
        )
    )
    if row is None:
        raise Unavailable("Original payment gateway unavailable")
    return replace(res, zpay=client_for(res, row)), att.local_ref


ROUTING_MODES = ("sticky", "priority")


def order_candidates(
    rows: list[PaymentGateway], mode: str, primary_at: datetime | None = None
) -> list[PaymentGateway]:
    """Enabled gateways, best first. ``priority``: by priority number. ``sticky``: the gateway that
    last succeeded goes first (unless it failed since, or an admin made another one primary after
    it), the rest by priority."""
    live = sorted((r for r in rows if r.enabled), key=lambda r: (r.priority, r.created_at, r.id))
    if mode != "sticky":
        return live
    sticky = [
        r
        for r in live
        if r.last_ok_at is not None
        and (r.last_fail_at is None or r.last_fail_at < r.last_ok_at)
        and (primary_at is None or r.last_ok_at > primary_at)
    ]
    if not sticky:
        return live
    best = max(sticky, key=lambda r: r.last_ok_at or datetime.min.replace(tzinfo=timezone.utc))
    return [best] + [r for r in live if r.id != best.id]


async def routing_mode(db: AsyncSession, tid: uuid.UUID) -> str:
    mode = ((await tenant_settings(db, tid)).get("payments") or {}).get("epay_routing")
    return mode if mode in ROUTING_MODES else "sticky"


async def candidates(db: AsyncSession, tid: uuid.UUID) -> list[PaymentGateway]:
    pay = (await tenant_settings(db, tid)).get("payments") or {}
    mode = pay.get("epay_routing")
    primary = pay.get("epay_primary_at")
    rows = list(
        await db.scalars(select(PaymentGateway).where(PaymentGateway.tenant_id == tid).limit(100))
    )
    return order_candidates(
        rows,
        mode if mode in ROUTING_MODES else "sticky",
        datetime.fromisoformat(primary) if primary else None,
    )


async def _gateway_failed(db: AsyncSession, tid: uuid.UUID, pid: uuid.UUID, reason: str) -> None:
    await db.execute(
        update(PaymentGateway)
        .where(PaymentGateway.id == pid, PaymentGateway.tenant_id == tid)
        .values(last_fail_at=func.now(), last_fail_reason=reason)
    )


async def mark_gateway_ok(db: AsyncSession, tid: uuid.UUID, pid: uuid.UUID) -> None:
    await db.execute(
        update(PaymentGateway)
        .where(PaymentGateway.id == pid, PaymentGateway.tenant_id == tid)
        .values(last_ok_at=func.now())
    )


async def prepare(
    db: AsyncSession,
    res: Resources,
    *,
    tid: uuid.UUID,
    no: str,
    title: str,
    amount_minor: int,
    currency: str,
    ip: str | None,
) -> str | None:
    """Caller released order locks. A transaction advisory lock serializes reservations only.

    Candidates are tried in routing order. A try that provably created nothing at the gateway
    (connect failure / explicit refusal) fails its own attempt and falls through to the next
    gateway; anything ambiguous stops here with an ``unknown`` attempt (review-v10 §A.3)."""
    cands = await candidates(db, tid)
    if not cands:
        has_rows = await db.scalar(
            select(PaymentGateway.id).where(PaymentGateway.tenant_id == tid).limit(1)
        )
        managed = ((await tenant_settings(db, tid)).get("payments") or {}).get("epay_provider_id")
        if has_rows or managed:
            # managed tenant with nothing enabled: never fall back to the environment key
            raise Unavailable("No payment gateway is enabled")
        return None  # existing environment-configured integration remains compatible
    if currency != "CNY":
        raise Unavailable("This gateway accepts CNY only")
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": str(tid) + ":epay:" + no},
    )
    att = await db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.tenant_id == tid,
            PaymentAttempt.local_ref == no,
            PaymentAttempt.provider_id.is_not(None),
            PaymentAttempt.status.in_(("preparing", "ready", "unknown")),
        )
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    model, col = (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
    target = await db.scalar(
        select(model).where(col == no, model.tenant_id == tid).with_for_update()
    )
    if (
        target is None
        or target.status != "pending_payment"
        or target.amount_minor != amount_minor
        or target.channel != "zpay_alipay"
        or (target.expires_at and target.expires_at <= now)
    ):
        raise InvalidState("Order changed before payment reservation")
    target_version = str(target.updated_at)
    if att is not None:
        if att.status in ("unknown", "preparing"):
            await db.rollback()
            raise Unavailable("Payment outcome is unknown; reconciliation is required")
        if (
            att.amount_minor == amount_minor
            and att.provider_id in {c.id for c in cands}  # its gateway is still enabled
            and att.currency == currency
            and att.expires_at
            and att.expires_at > now
        ):
            await db.commit()
            return "/pay/epay/" + no
        att.status = "superseded"
        await db.flush()
    # plain values: every attempt commits, which expires the ORM rows
    plan = [(c.id, client_for(res, c), c.mode) for c in cands]
    for index, (pid, client, mode) in enumerate(plan):
        outcome = await _try_gateway(
            db,
            tid=tid,
            no=no,
            title=title,
            amount_minor=amount_minor,
            currency=currency,
            ip=ip,
            pid=pid,
            client=client,
            mode=mode,
            model=model,
            col=col,
            target_version=target_version,
            retry=index > 0,
        )
        if outcome in ("ok", "taken"):
            return "/pay/epay/" + no
    raise ChannelUnreachable()


async def _try_gateway(
    db: AsyncSession,
    *,
    tid: uuid.UUID,
    no: str,
    title: str,
    amount_minor: int,
    currency: str,
    ip: str | None,
    pid: uuid.UUID,
    client: ZPayClient,
    mode: str,
    model: Any,
    col: Any,
    target_version: str,
    retry: bool = False,
) -> str:
    """One reservation + gateway call. Returns ``"ok"`` or ``"failover"``; every other outcome
    raises (and so ends the pay request)."""
    if retry:
        # the previous try's commit released the reservation lock: take it again and make sure no
        # concurrent pay request opened a live attempt for this payable in the meantime
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": str(tid) + ":epay:" + no},
        )
        live = await db.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_not(None),
                PaymentAttempt.status.in_(("preparing", "ready", "unknown")),
            )
            .limit(1)
        )
        if live is not None:
            live_status = live.status
            await db.rollback()
            if live_status == "ready":
                return "taken"
            raise Unavailable("A payment is already being prepared; retry shortly")
    now = datetime.now(timezone.utc)
    att = PaymentAttempt(
        tenant_id=tid,
        local_ref=no,
        gateway_ref=no + "-" + secrets.token_hex(6),
        provider_id=pid,
        channel="zpay_alipay",
        amount_minor=amount_minor,
        currency=currency,
        status="preparing",
        data={},
        expires_at=now + timedelta(minutes=10),
    )
    db.add(att)
    await db.flush()
    att_id, remote_no = att.id, att.gateway_ref
    await audit(
        db,
        tenant_id=tid,
        action="payment.attempt_reserved",
        target="payment_attempt:" + str(att_id),
        detail={"provider_id": str(pid)},
    )
    await db.commit()
    try:
        presentation = (
            {
                "kind": "redirect",
                "payload": client.build_pay_url(remote_no, title, to_decimal(amount_minor)),
            }
            if mode == "redirect"
            else await client.create_payment(remote_no, title, to_decimal(amount_minor), ip or "")
        )
    except (ZPayUnreachable, ZPayRefused) as exc:
        # The request provably created nothing at the gateway (never sent / explicitly refused):
        # a clean failure, not an unknown outcome. The payable stays payable.
        reason = "gateway_refused" if isinstance(exc, ZPayRefused) else "gateway_unreachable"
        current = await db.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == att_id, PaymentAttempt.tenant_id == tid)
            .with_for_update()
            .execution_options(populate_existing=True)  # see a release committed meanwhile
        )
        # only while still ours: staff may have released the attempt meanwhile (keep that record)
        if current and current.status == "preparing":
            current.status = "failed"
            current.data = {"reason": reason}
            detail: dict[str, Any] = {"reason": reason, "provider_id": str(pid)}
            if isinstance(exc, ZPayRefused):
                detail["gateway_message"] = exc.gateway_message
            await audit(
                db,
                tenant_id=tid,
                action="payment.attempt_failed",
                target="payment_attempt:" + str(att_id),
                severity="warning",
                detail=detail,
            )
            await _gateway_failed(db, tid, pid, reason)
            await record_event(
                db,
                tenant_id=tid,
                environment="payment",
                family="payment",
                action="payment.gateway_failed",
                outcome="failed",
                reason=reason,
                resource_id=str(pid),
                ip=ip,
            )
            await db.commit()
        return "failover"
    except (ZPayQueryError, asyncio.TimeoutError):
        current = await db.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == att_id, PaymentAttempt.tenant_id == tid)
            .with_for_update()
            .execution_options(populate_existing=True)  # see a release committed meanwhile
        )
        if current and current.status == "preparing":
            current.status = "unknown"
            await audit(
                db,
                tenant_id=tid,
                action="payment.attempt_unknown",
                target="payment_attempt:" + str(att_id),
                severity="warning",
            )
            await db.commit()
        raise Unavailable(
            "Payment preparation is uncertain; do not create another payment"
        ) from None
    current = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == att_id, PaymentAttempt.tenant_id == tid)
        .with_for_update()
        .execution_options(populate_existing=True)  # see a release committed meanwhile
    )
    if current is None:
        raise Unavailable("Payment attempt unavailable")
    if current.status != "preparing":
        # staff released (or another path settled) it while the gateway call was in flight: the
        # result is not recorded and the pay URL is never handed out
        await db.rollback()
        raise ChannelUnreachable()
    target = await db.scalar(
        select(model)
        .where(col == no, model.tenant_id == tid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current.data = presentation
    changed = (
        target is None
        or target.status != "pending_payment"
        or target.amount_minor != amount_minor
        or target.channel != "zpay_alipay"
        or str(target.updated_at) != target_version
    )
    current.status = "superseded" if changed else "ready"
    await mark_gateway_ok(db, tid, pid)
    await audit(
        db, tenant_id=tid, action="payment.attempt_ready", target="payment_attempt:" + str(att_id)
    )
    await db.commit()
    if changed:
        raise InvalidState("Order changed while payment was being prepared")
    return "ok"


async def set_active(db: AsyncSession, tid: uuid.UUID, ref: uuid.UUID | None) -> None:
    """Legacy "activate": enable ``ref``, make it the highest-priority gateway and the primary one
    (sticky mode ignores earlier successes of the others). The rest stay as configured."""
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tid).with_for_update())
    if tenant is None:
        raise Validation("Unknown tenant")
    if ref is not None:
        lowest = await db.scalar(
            select(func.min(PaymentGateway.priority)).where(
                PaymentGateway.tenant_id == tid, PaymentGateway.id != ref
            )
        )
        await db.execute(
            update(PaymentGateway)
            .where(PaymentGateway.id == ref, PaymentGateway.tenant_id == tid)
            .values(enabled=True, priority=min(10, (lowest if lowest is not None else 11) - 1))
        )
    settings = dict(tenant.settings or {})
    settings["payments"] = {
        **(settings.get("payments") or {}),
        "epay_primary_at": datetime.now(timezone.utc).isoformat(),
        "epay_provider_id": str(ref) if ref else None,
        "epay_legacy_cutover": (settings.get("payments") or {}).get("epay_legacy_cutover")
        or datetime.now(timezone.utc).isoformat(),
    }
    tenant.settings = settings


async def set_routing_mode(db: AsyncSession, tid: uuid.UUID, mode: str) -> str:
    """Persist the tenant's routing mode; returns the previous one."""
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tid).with_for_update())
    if tenant is None or mode not in ROUTING_MODES:
        raise Validation("Unknown tenant or routing mode")
    settings = dict(tenant.settings or {})
    pay = settings.get("payments") or {}
    old = pay.get("epay_routing") if pay.get("epay_routing") in ROUTING_MODES else "sticky"
    settings["payments"] = {**pay, "epay_routing": mode}
    tenant.settings = settings
    return str(old)


async def legacy_allowed(db: AsyncSession, tid: uuid.UUID, no: str) -> bool:
    """An old environment key cannot route around a durable/versioned attempt."""
    from sqlalchemy import exists

    managed = await db.scalar(
        select(
            exists().where(
                PaymentAttempt.tenant_id == tid,
                PaymentAttempt.local_ref == no,
                PaymentAttempt.provider_id.is_not(None),
            )
        )
    )
    if managed:
        return False
    cutover = ((await tenant_settings(db, tid)).get("payments") or {}).get("epay_legacy_cutover")
    if not cutover:
        return True
    model, col = (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
    created = await db.scalar(select(model.created_at).where(col == no, model.tenant_id == tid))
    return bool(created and created < datetime.fromisoformat(cutover))


async def record_legacy_url(db: AsyncSession, tid: uuid.UUID, no: str) -> None:
    """Record that an environment-key pay URL is about to be handed out for ``no`` (issue #32).

    Call it BEFORE the URL leaves the server, in the transaction that commits before returning it:
    if that commit fails the buyer never receives the URL, and if it succeeds the evidence exists.
    Idempotent: a payable re-opening its pay page keeps its first marker.
    """
    from sqlalchemy.dialects.postgresql import insert

    await db.execute(
        insert(LegacyPayUrl).values(tenant_id=tid, local_ref=no).on_conflict_do_nothing()
    )


async def legacy_url_issued(db: AsyncSession, tid: uuid.UUID, no: str) -> bool:
    """Whether a legacy env-key URL was ever handed out for this payable.

    This is the only reliable answer to "can money arrive through a URL we cannot see?". The
    current channel, key presence, cutover time and provider history can all change after a URL
    is issued, so none of them may stand in for this record.
    """
    from sqlalchemy import exists

    return bool(
        await db.scalar(
            select(exists().where(LegacyPayUrl.tenant_id == tid, LegacyPayUrl.local_ref == no))
        )
    )


async def ensure_still_payable(db: AsyncSession, tid: uuid.UUID, no: str) -> None:
    """Refuse to hand out a legacy URL for a payable a concurrent cancel already closed.

    The pay flow commits (releasing its row lock) before ``prepare`` and the marker write, so a
    cancel can complete in between: it finds no marker, queries "unpaid" and cancels. Checking
    again AFTER the marker commits closes that window -- a cancel that finished first is caught
    here, and one that starts later sees the marker and queries the URL.
    """
    model, col = (Checkout, Checkout.checkout_no) if no.startswith("C") else (Order, Order.order_no)
    status = await db.scalar(select(model.status).where(col == no, model.tenant_id == tid))
    if status != "pending_payment":
        raise InvalidState("This order is no longer awaiting payment")
