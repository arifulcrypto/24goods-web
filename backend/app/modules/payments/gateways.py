"""Immutable tenant EPay configurations, reservations and original-provider routing."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox
from app.core.audit import audit
from app.core.errors import ChannelUnreachable, InvalidState, Unavailable, Validation
from app.core.money import to_decimal
from app.core.resources import Resources
from app.models import Checkout, LegacyPayUrl, Order, PaymentAttempt, PaymentGateway, Tenant
from app.modules.content.site import tenant_settings
from app.payments.zpay import ZPayClient, ZPayConfig, ZPayQueryError, ZPayUnreachable


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
    return bool(
        (ts.get("payments") or {}).get("epay_provider_id")
        or (res.settings.zpay_pid and res.settings.zpay_key)
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
    """Caller released order locks. A transaction advisory lock serializes reservations only."""
    row = await active_gateway(db, tid)
    if row is None:
        return None  # existing environment-configured integration remains compatible
    if currency != "CNY":
        raise Unavailable("This gateway accepts CNY only")
    client, pid, mode = client_for(res, row), row.id, row.mode
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
            and att.provider_id == pid
            and att.currency == currency
            and att.expires_at
            and att.expires_at > now
        ):
            await db.commit()
            return "/pay/epay/" + no
        att.status = "superseded"
        await db.flush()
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
    except ZPayUnreachable:
        # The request provably never reached the gateway: nothing can have been created there, so
        # this is a clean failure, not an unknown outcome. The payable stays payable.
        current = await db.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == att_id, PaymentAttempt.tenant_id == tid)
            .with_for_update()
        )
        if current:
            current.status = "failed"
            current.data = {"reason": "gateway_unreachable"}
            await audit(
                db,
                tenant_id=tid,
                action="payment.attempt_failed",
                target="payment_attempt:" + str(att_id),
                severity="warning",
                detail={"reason": "gateway_unreachable", "provider_id": str(pid)},
            )
            await db.commit()
        raise ChannelUnreachable() from None
    except (ZPayQueryError, asyncio.TimeoutError):
        current = await db.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == att_id, PaymentAttempt.tenant_id == tid)
            .with_for_update()
        )
        if current:
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
    )
    if current is None:
        raise Unavailable("Payment attempt unavailable")
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
    await audit(
        db, tenant_id=tid, action="payment.attempt_ready", target="payment_attempt:" + str(att_id)
    )
    await db.commit()
    if changed:
        raise InvalidState("Order changed while payment was being prepared")
    return "/pay/epay/" + no


async def set_active(db: AsyncSession, tid: uuid.UUID, ref: uuid.UUID | None) -> None:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tid).with_for_update())
    if tenant is None:
        raise Validation("Unknown tenant")
    settings = dict(tenant.settings or {})
    settings["payments"] = {
        **(settings.get("payments") or {}),
        "epay_provider_id": str(ref) if ref else None,
        "epay_legacy_cutover": (settings.get("payments") or {}).get("epay_legacy_cutover")
        or datetime.now(timezone.utc).isoformat(),
    }
    tenant.settings = settings


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
