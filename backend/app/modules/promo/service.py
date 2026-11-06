"""Promo codes (review-v5 s6): evaluation, allocation, redemption lifecycle, admin CRUD.

MONEY RULES (read by the payment auditor):

* Eligible subtotal = lines matching ``product_ids`` OR ``category_slugs`` (both empty = all).
* percent: ``floor(eligible * permille / 1000)``; fixed: ``min(value, eligible)``; then capped at
  ``max_discount_minor``; ``min_subtotal_minor`` is checked on the ELIGIBLE subtotal.
* Every order must keep ``0 <= discount < subtotal`` (DB CHECK). The total is therefore clamped
  to ``sum(subtotal_i - 1)`` over eligible lines, and spread with
  :func:`allocate` (largest remainder, per-line caps, overflow re-spread).
* The channel fee is computed afterwards on ``subtotal - discount`` per order
  (``channels.split_amount``).
* Limits (``usage_limit``, ``per_user_limit``) are checked under ``SELECT ... FOR UPDATE`` of the
  promo row, and ``used_count`` is bumped in the SAME transaction that creates the order /
  checkout, so concurrent buyers cannot oversubscribe.
* Redemption: ``reserved`` (+used_count) at creation -> ``used`` on settlement -> ``released``
  (-used_count) on cancel/expiry. A refund after payment keeps it ``used``.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.errors import ApiError, Conflict, NotFound, Validation
from app.core.money import fmt
from app.models import Product, PromoCode, PromoRedemption, User

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,31}$")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class PromoError(ApiError):
    """Promo code rejected"""

    status, code = 422, "promo_invalid"

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code.replace("_", " "), code=code)


def normalize_code(raw: str | None) -> str:
    return (raw or "").strip().upper()


def generate_code() -> str:
    """8 chars, Crockford base32 (no I L O U)."""
    return "".join(secrets.choice(_CROCKFORD) for _ in range(8))


# ----------------------------------------------------------------------------- pure maths
@dataclass(frozen=True)
class PromoLine:
    product_id: uuid.UUID
    category: str
    subtotal_minor: int  # price * qty of the order this line becomes


@dataclass
class Evaluation:
    promo: PromoCode
    discount_minor: int
    eligible_minor: int
    allocation: list[int]  # per input line (0 for ineligible lines)


def allocate(total: int, weights: list[int], caps: list[int]) -> list[int]:
    """Split ``total`` over lines proportionally to ``weights`` (largest remainder), never
    giving a line more than its cap; overflow is re-spread over lines with room left.
    Requires ``0 <= total <= sum(caps)`` and ``caps[i] <= weights[i]``-ish positive weights.
    Deterministic (ties broken by index). Sum of the result is exactly ``total``."""
    n = len(weights)
    shares = [0] * n
    remaining = total
    active = [i for i in range(n) if caps[i] > 0 and weights[i] > 0]
    while remaining > 0 and active:
        w = sum(weights[i] for i in active)
        floors = {i: remaining * weights[i] // w for i in active}
        rems = {i: remaining * weights[i] % w for i in active}
        leftover = remaining - sum(floors.values())
        for i in sorted(active, key=lambda i: (-rems[i], i))[:leftover]:
            floors[i] += 1
        overflow = 0
        for i in active:
            shares[i] += floors[i]
            if shares[i] > caps[i]:
                overflow += shares[i] - caps[i]
                shares[i] = caps[i]
        remaining = overflow
        active = [i for i in active if shares[i] < caps[i]]
    if remaining:  # unreachable when total <= sum(caps)
        raise PromoError("promo_not_applicable")
    return shares


def compute(promo: PromoCode, lines: list[PromoLine], currency: str) -> tuple[int, int, list[int]]:
    """``(discount, eligible_subtotal, per-line allocation)`` or raise a :class:`PromoError`."""
    if promo.currency != currency:
        raise PromoError("promo_not_applicable")
    scoped = bool(promo.product_ids or promo.category_slugs)
    elig = [
        i
        for i, ln in enumerate(lines)
        if not scoped or ln.product_id in promo.product_ids or ln.category in promo.category_slugs
    ]
    eligible = sum(lines[i].subtotal_minor for i in elig)
    if eligible <= 0:
        raise PromoError("promo_not_applicable")
    if eligible < promo.min_subtotal_minor:
        raise PromoError("promo_min_subtotal")
    raw = eligible * promo.value // 1000 if promo.kind == "percent" else min(promo.value, eligible)
    if promo.max_discount_minor is not None:
        raw = min(raw, promo.max_discount_minor)
    caps = [lines[i].subtotal_minor - 1 for i in elig]  # each order keeps >= 1 minor unit
    total = min(raw, sum(caps))
    if total <= 0:
        raise PromoError("promo_not_applicable")
    shares = allocate(total, [lines[i].subtotal_minor for i in elig], caps)
    alloc = [0] * len(lines)
    for i, s in zip(elig, shares, strict=True):
        alloc[i] = s
    assert sum(alloc) == total and all(
        0 <= a < ln.subtotal_minor for a, ln in zip(alloc, lines, strict=True)
    )
    return total, eligible, alloc


# ----------------------------------------------------------------------------- evaluation
async def evaluate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user: User | None,
    code: str | None,
    lines: list[PromoLine],
    currency: str,
    lock: bool,
) -> Evaluation:
    """Validate ``code`` for ``lines``. ``lock=True`` (order creation) takes the promo row
    ``FOR UPDATE`` so the limit checks and the later ``reserve`` are atomic."""
    c = normalize_code(code)
    if not CODE_RE.match(c):
        raise PromoError("promo_invalid")
    stmt = select(PromoCode).where(PromoCode.tenant_id == tenant_id, PromoCode.code == c)
    if lock:
        stmt = stmt.with_for_update()
    promo = await db.scalar(stmt)
    if promo is None or not promo.enabled:
        raise PromoError("promo_invalid")
    now = datetime.now(timezone.utc)
    if promo.starts_at is not None and promo.starts_at > now:
        raise PromoError("promo_not_started")
    if promo.ends_at is not None and promo.ends_at <= now:
        raise PromoError("promo_expired")
    if promo.usage_limit is not None and promo.used_count >= promo.usage_limit:
        raise PromoError("promo_exhausted")
    if user is not None:
        used = await db.scalar(
            select(func.count(PromoRedemption.id)).where(
                PromoRedemption.promo_code_id == promo.id,
                PromoRedemption.user_id == user.id,
                PromoRedemption.status != "released",
                PromoRedemption.tenant_id == tenant_id,
            )
        )
        if int(used or 0) >= promo.per_user_limit:
            raise PromoError("promo_user_limit")
    total, eligible, alloc = compute(promo, lines, currency)
    return Evaluation(promo, total, eligible, alloc)


async def lines_for(
    db: AsyncSession, tenant_id: uuid.UUID, items: list[tuple[uuid.UUID, int]]
) -> list[PromoLine]:
    """Lines (product_id, base price*qty) for ``POST /promo/validate``; unknown/inactive -> 404."""
    ids = list({pid for pid, _ in items})
    prods = {
        p.id: p
        for p in await db.scalars(
            select(Product).where(
                Product.id.in_(ids), Product.tenant_id == tenant_id, Product.status == "active"
            )
        )
    }
    if len(prods) != len(ids):
        raise NotFound("Product not found")
    return [PromoLine(pid, prods[pid].category, prods[pid].price_minor * qty) for pid, qty in items]


# ----------------------------------------------------------------------------- lifecycle
async def reserve(
    db: AsyncSession,
    ev: Evaluation,
    user: User,
    *,
    order_id: uuid.UUID | None = None,
    checkout_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> None:
    """Create the ``reserved`` redemption and bump ``used_count``. The caller holds the promo
    row lock from :func:`evaluate` and commits order + redemption together."""
    assert (order_id is None) != (checkout_id is None)
    db.add(
        PromoRedemption(
            tenant_id=user.tenant_id,
            promo_code_id=ev.promo.id,
            user_id=user.id,
            order_id=order_id,
            checkout_id=checkout_id,
            discount_minor=ev.discount_minor,
            status="reserved",
        )
    )
    await db.execute(
        update(PromoCode)
        .where(PromoCode.id == ev.promo.id, PromoCode.tenant_id == user.tenant_id)
        .values(used_count=PromoCode.used_count + 1)
    )
    await audit(
        db,
        tenant_id=user.tenant_id,
        action="promo.reserved",
        actor_id=user.id,
        actor_role=user.role,
        target=f"promo:{ev.promo.id}",
        ip=ip,
        detail={
            "order_id": str(order_id) if order_id else None,
            "checkout_id": str(checkout_id) if checkout_id else None,
            "discount_minor": ev.discount_minor,
        },
    )


async def _redemption(
    db: AsyncSession, order_id: uuid.UUID | None, checkout_id: uuid.UUID | None
) -> PromoRedemption | None:
    cond = (
        PromoRedemption.checkout_id == checkout_id
        if checkout_id is not None
        else PromoRedemption.order_id == order_id
    )
    return await db.scalar(select(PromoRedemption).where(cond).with_for_update())


async def release(
    db: AsyncSession, *, order_id: uuid.UUID | None = None, checkout_id: uuid.UUID | None = None
) -> bool:
    """reserved -> released (-used_count) when the payable is cancelled or expires unpaid.
    Idempotent: only a ``reserved`` row is touched."""
    red = await _redemption(db, order_id, checkout_id)
    if red is None or red.status != "reserved":
        return False
    red.status = "released"
    await db.execute(
        update(PromoCode)
        .where(PromoCode.id == red.promo_code_id, PromoCode.tenant_id == red.tenant_id)
        .values(used_count=func.greatest(PromoCode.used_count - 1, 0))
    )
    await audit(
        db,
        tenant_id=red.tenant_id,
        action="promo.released",
        target=f"promo:{red.promo_code_id}",
        detail={
            "order_id": str(order_id) if order_id else None,
            "checkout_id": str(checkout_id) if checkout_id else None,
        },
    )
    return True


async def mark_used(
    db: AsyncSession, *, order_id: uuid.UUID | None = None, checkout_id: uuid.UUID | None = None
) -> None:
    """reserved -> used on settlement. A redemption already ``released`` (late payment of an
    expired order: the money WAS received at the discounted price) is re-counted."""
    red = await _redemption(db, order_id, checkout_id)
    if red is None or red.status == "used":
        return
    if red.status == "released":
        await db.execute(
            update(PromoCode)
            .where(PromoCode.id == red.promo_code_id, PromoCode.tenant_id == red.tenant_id)
            .values(used_count=PromoCode.used_count + 1)
        )
    red.status = "used"
    await audit(
        db,
        tenant_id=red.tenant_id,
        action="promo.used",
        target=f"promo:{red.promo_code_id}",
        detail={
            "order_id": str(order_id) if order_id else None,
            "checkout_id": str(checkout_id) if checkout_id else None,
            "discount_minor": red.discount_minor,
        },
    )


# ----------------------------------------------------------------------------- admin
def admin_view(p: PromoCode) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "code": p.code,
        "kind": p.kind,
        "value": int(p.value),
        "currency": p.currency,
        "min_subtotal_minor": int(p.min_subtotal_minor),
        "max_discount_minor": p.max_discount_minor,
        "starts_at": p.starts_at.isoformat().replace("+00:00", "Z") if p.starts_at else None,
        "ends_at": p.ends_at.isoformat().replace("+00:00", "Z") if p.ends_at else None,
        "usage_limit": p.usage_limit,
        "per_user_limit": p.per_user_limit,
        "used_count": p.used_count,
        "product_ids": [str(x) for x in p.product_ids],
        "category_slugs": list(p.category_slugs),
        "enabled": p.enabled,
        "note": p.note,
        "created_at": p.created_at.isoformat().replace("+00:00", "Z"),
    }


def describe(p: PromoCode) -> str:
    """Short machine-neutral description for the buyer (the SPA localises around it)."""
    if p.kind == "percent":
        v = p.value / 10
        return f"{v:g}% off"
    return f"{fmt(p.value)} off"


async def _check_scope(db: AsyncSession, tenant_id: uuid.UUID, data: dict[str, Any]) -> None:
    pids = data.get("product_ids") or []
    if pids:
        n = await db.scalar(
            select(func.count(Product.id)).where(
                Product.id.in_(pids), Product.tenant_id == tenant_id
            )
        )
        if int(n or 0) != len(set(pids)):
            raise Validation("product_ids: unknown product")


async def create(
    db: AsyncSession, admin: User, data: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    code = normalize_code(data.pop("code", None)) or generate_code()
    if not CODE_RE.match(code):
        raise Validation("code: 3-32 chars A-Z 0-9 _ -")
    await _check_scope(db, admin.tenant_id, data)
    if await db.scalar(
        select(PromoCode.id).where(PromoCode.tenant_id == admin.tenant_id, PromoCode.code == code)
    ):
        raise Conflict("Promo code already exists")
    p = PromoCode(tenant_id=admin.tenant_id, code=code, created_by=admin.id, used_count=0, **data)
    db.add(p)
    await db.flush()
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="promo.created",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"promo:{p.id}",
        after=admin_view(p),
        ip=ip,
    )
    await db.commit()
    await db.refresh(p)
    return admin_view(p)


async def update_promo(
    db: AsyncSession, admin: User, promo_id: uuid.UUID, data: dict[str, Any], ip: str | None
) -> dict[str, Any]:
    p = await db.scalar(
        select(PromoCode)
        .where(PromoCode.id == promo_id, PromoCode.tenant_id == admin.tenant_id)
        .with_for_update()
    )
    if p is None:
        raise NotFound("Promo code not found")
    await _check_scope(db, admin.tenant_id, data)
    before = admin_view(p)
    for k, v in data.items():
        setattr(p, k, v)
    if p.kind == "percent" and p.value > 1000:
        raise Validation("percent value is per-mille (1..1000)")
    if p.starts_at and p.ends_at and p.starts_at >= p.ends_at:
        raise Validation("starts_at must be before ends_at")
    p.updated_at = datetime.now(timezone.utc)
    await audit(
        db,
        tenant_id=admin.tenant_id,
        action="promo.updated",
        actor_id=admin.id,
        actor_role=admin.role,
        target=f"promo:{p.id}",
        before=before,
        after=admin_view(p),
        ip=ip,
    )
    await db.commit()
    await db.refresh(p)
    return admin_view(p)
