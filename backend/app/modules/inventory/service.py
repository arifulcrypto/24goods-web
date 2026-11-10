"""Card-stock import (seal-only), masked listing, find, void, replace, batches.

The API holds only a :class:`CardSealer`. Rows are inserted with Core
``insert(...).returning(<non-blob columns>)``: role ``app_api`` has no SELECT on
``card_stock.blob``, so the ORM must never load or refresh it here. Nothing in this module can
decrypt: "find by content" recomputes the keyed fingerprint of what the staff member pastes.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.cardvault import CardContext, CardSealer, first_field
from app.core.deps import Page
from app.core.errors import Conflict, NotFound, Validation
from app.core.scoped import get_scoped, session_tenant
from app.models import CardBatch, CardStock, Order, Product
from app.modules.common.views import iso

MAX_LINES = 5000
MAX_CODE_LEN = 512
CHUNK = 500
MAX_FIND_LINES = 100
MAX_VOID_IDS = 1000
_EXP_SUFFIX = re.compile(r"\|exp=(\d{4}-\d{2}-\d{2})\s*$")


@dataclass(frozen=True)
class ImportResult:
    imported: int
    duplicates: int
    rejected: int
    batch_id: uuid.UUID | None = None


def _valid_code(code: str) -> bool:
    return 0 < len(code) <= MAX_CODE_LEN and all(ord(c) >= 32 and ord(c) != 127 for c in code)


def masked(fingerprint: str) -> str:
    """Display handle derived from the keyed fingerprint: reveals nothing about the code."""
    return "••••-" + fingerprint[:8].upper()


def end_of_day(d: date) -> datetime:
    """A date means "valid through that day" (UTC): expiry is the start of the next day."""
    return datetime.combine(d + timedelta(days=1), time.min, tzinfo=timezone.utc)


def split_expiry(line: str) -> tuple[str, datetime | None] | None:
    """``code|exp=YYYY-MM-DD`` -> (code, expiry). None when the date is invalid."""
    m = _EXP_SUFFIX.search(line)
    if m is None:
        return line, None
    try:
        d = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return line[: m.start()].rstrip(), end_of_day(d)


async def import_cards(
    db: AsyncSession,
    sealer: CardSealer,
    *,
    tenant_id: uuid.UUID,
    product: Product,
    lines: str,
    actor_id: uuid.UUID | None,
    actor_role: str,
    ip: str | None,
    note: str | None = None,
    expires_at: datetime | None = None,
) -> ImportResult:
    if product.delivery_mode != "auto_card":
        raise Validation("Cards can only be imported for auto_card products")
    raw_lines = [ln.strip() for ln in lines.splitlines()]
    codes = [ln for ln in raw_lines if ln]
    if len(codes) > MAX_LINES:
        raise Validation(f"At most {MAX_LINES} lines per request")
    rejected = 0
    duplicates = 0
    seen: set[str] = set()
    rows: list[dict] = []
    batch_id = uuid.uuid4()
    sep = product.delivery_separator or "----"
    for line in codes:
        parsed = split_expiry(line)
        if parsed is None:
            rejected += 1
            continue
        code, line_exp = parsed  # the |exp= suffix is NOT part of the secret or its fingerprint
        if not _valid_code(code):
            rejected += 1
            continue
        fp = sealer.fingerprint(str(tenant_id), code)
        if fp in seen:
            duplicates += 1
            continue
        seen.add(fp)
        card_id = uuid.uuid4()
        ctx = CardContext(str(tenant_id), str(product.id), str(card_id))
        rows.append(
            {
                "id": card_id,
                "tenant_id": tenant_id,
                "product_id": product.id,
                "blob": sealer.seal(code, ctx),
                "fingerprint": fp,
                "lookup_fp": sealer.lookup_fingerprint(str(tenant_id), first_field(code, sep)),
                "kid": sealer.active_kid,
                "status": "available",
                "created_by": actor_id,
                "batch_id": batch_id,
                "expires_at": line_exp or expires_at,
            }
        )
    del codes, raw_lines  # drop plaintext references as early as possible
    if rows:  # drop what already exists, so an all-duplicate import leaves no empty batch
        existing = set(
            await db.scalars(
                select(CardStock.__table__.c.fingerprint).where(
                    CardStock.__table__.c.tenant_id == tenant_id,
                    CardStock.__table__.c.fingerprint.in_([r["fingerprint"] for r in rows]),
                )
            )
        )
        duplicates += sum(1 for r in rows if r["fingerprint"] in existing)
        rows = [r for r in rows if r["fingerprint"] not in existing]
    imported = 0
    if rows:
        db.add(
            CardBatch(
                id=batch_id,
                tenant_id=tenant_id,
                product_id=product.id,
                note=note,
                count=0,
                expires_at=expires_at,
                created_by=actor_id,
            )
        )
        await db.flush()
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        stmt = (
            insert(CardStock)
            .values(chunk)
            .on_conflict_do_nothing(constraint="uq_card_stock_tenant_fingerprint")
            .returning(CardStock.__table__.c.id)
        )
        inserted = len((await db.execute(stmt)).all())
        imported += inserted
        duplicates += len(chunk) - inserted
    if rows:
        await db.execute(update(CardBatch).where(CardBatch.id == batch_id).values(count=imported))
    await audit(
        db,
        tenant_id=tenant_id,
        action="cards.import",
        actor_id=actor_id,
        actor_role=actor_role,
        target=f"product:{product.id}",
        ip=ip,
        detail={
            "imported": imported,
            "duplicates": duplicates,
            "rejected": rejected,
            "kid": sealer.active_kid,
            "batch_id": str(batch_id) if rows else None,
            "expires_at": iso(expires_at),
        },
    )
    await db.commit()
    return ImportResult(imported, duplicates, rejected, batch_id if rows else None)


def _card_rows(rows: list[Any], order_nos: dict[uuid.UUID, str]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(r["id"]),
            "masked": masked(r["fingerprint"]),
            "status": r["status"],
            "batch_id": str(r["batch_id"]) if r["batch_id"] else None,
            "expires_at": iso(r["expires_at"]),
            "void_reason": r["void_reason"],
            "order_id": str(r["order_id"]) if r["order_id"] else None,
            "order_no": order_nos.get(r["order_id"]) if r["order_id"] else None,
            "created_at": iso(r["created_at"]),
        }
        for r in rows
    ]


async def _order_nos(db: AsyncSession, rows: list[Any]) -> dict[uuid.UUID, str]:
    ids = {r["order_id"] for r in rows if r["order_id"]}
    if not ids:
        return {}
    return {
        oid: no
        for oid, no in (
            await db.execute(select(Order.id, Order.order_no).where(Order.id.in_(ids)))
        ).all()
    }


async def list_cards(
    db: AsyncSession,
    product_id: uuid.UUID,
    status: str | None,
    page: Page,
    *,
    batch_id: uuid.UUID | None = None,
    expiring_days: int | None = None,
    order_no: str | None = None,
) -> dict:
    if await get_scoped(db, Product, product_id, session_tenant(db)) is None:
        raise NotFound("Product not found")
    cols = CardStock.public_columns()
    t = CardStock.__table__
    base = select(*cols).where(t.c.product_id == product_id)
    if status:
        base = base.where(t.c.status == status)
    if batch_id:
        base = base.where(t.c.batch_id == batch_id)
    if expiring_days is not None:
        base = base.where(
            t.c.expires_at.is_not(None),
            t.c.status.in_(("available", "reserved")),
            t.c.expires_at <= func.now() + timedelta(days=expiring_days),
        )
    if order_no:
        base = base.where(
            t.c.order_id.in_(select(Order.id).where(Order.order_no == order_no.strip().upper()))
        )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        (
            await db.execute(
                base.order_by(t.c.created_at.desc(), t.c.id)
                .offset(page.offset)
                .limit(page.page_size)
            )
        )
        .mappings()
        .all()
    )
    return page.wrap(_card_rows(list(rows), await _order_nos(db, list(rows))), int(total))


async def batches(db: AsyncSession, product_id: uuid.UUID) -> list[dict[str, Any]]:
    if await get_scoped(db, Product, product_id, session_tenant(db)) is None:
        raise NotFound("Product not found")
    t = CardStock.__table__
    counts: dict[uuid.UUID, dict[str, int]] = {}
    soon = func.now() + timedelta(days=7)
    for bid, status, n, expiring in (
        await db.execute(
            select(
                t.c.batch_id,
                t.c.status,
                func.count(),
                func.count().filter(
                    t.c.expires_at.is_not(None), t.c.expires_at <= soon, t.c.status == "available"
                ),
            )
            .where(t.c.product_id == product_id, t.c.batch_id.is_not(None))
            .group_by(t.c.batch_id, t.c.status)
        )
    ).all():
        c = counts.setdefault(bid, {})
        c[status] = int(n)
        c["expiring_7d"] = c.get("expiring_7d", 0) + int(expiring)
    rows = await db.scalars(
        select(CardBatch)
        .where(CardBatch.product_id == product_id)
        .order_by(CardBatch.created_at.desc(), CardBatch.id)
    )
    out = []
    for b in rows:
        c = counts.get(b.id, {})
        out.append(
            {
                "id": str(b.id),
                "note": b.note,
                "count": b.count,
                "expires_at": iso(b.expires_at),
                "created_by": str(b.created_by) if b.created_by else None,
                "created_at": iso(b.created_at),
                "available": c.get("available", 0),
                "reserved": c.get("reserved", 0),
                "sold": c.get("sold", 0),
                "void": c.get("void", 0),
                "expiring_7d": c.get("expiring_7d", 0),
            }
        )
    return out


async def find_cards(
    db: AsyncSession, sealer: CardSealer, tenant_id: uuid.UUID, lines: list[str], by: str
) -> dict[str, Any]:
    """Match pasted codes (or first fields) to stock by keyed fingerprint. No decryption."""
    cleaned = [ln.strip() for ln in lines if ln and ln.strip()]
    if not cleaned:
        raise Validation("lines must not be empty")
    if len(cleaned) > MAX_FIND_LINES:
        raise Validation(f"At most {MAX_FIND_LINES} lines")
    t = CardStock.__table__
    wanted: dict[str, int] = {}
    for i, ln in enumerate(cleaned, start=1):
        if by == "content":
            parsed = split_expiry(ln)
            code = parsed[0] if parsed else ln
            wanted.setdefault(sealer.fingerprint(str(tenant_id), code), i)
        else:
            wanted.setdefault(sealer.lookup_fingerprint(str(tenant_id), ln), i)
    col = t.c.fingerprint if by == "content" else t.c.lookup_fp
    rows = (
        (
            await db.execute(
                select(*CardStock.public_columns())
                .where(t.c.tenant_id == tenant_id, col.in_(list(wanted)))
                .order_by(t.c.created_at.desc(), t.c.id)
                .limit(500)
            )
        )
        .mappings()
        .all()
    )
    rows = list(rows)
    products = {
        pid: title
        for pid, title in (
            await db.execute(
                select(Product.id, Product.title_i18n).where(
                    Product.id.in_({r["product_id"] for r in rows})
                )
            )
        ).all()
    }
    items = _card_rows(rows, await _order_nos(db, rows))
    hit_keys = set()
    for item, r in zip(items, rows, strict=True):
        key = r["fingerprint"] if by == "content" else r["lookup_fp"]
        hit_keys.add(key)
        item["line"] = wanted.get(key)
        item["product_id"] = str(r["product_id"])
        item["product_title_i18n"] = products.get(r["product_id"])
    return {"items": items, "missing": len([k for k in wanted if k not in hit_keys])}


async def void_cards(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    ids: list[uuid.UUID] | None,
    batch_id: uuid.UUID | None,
    reason: str,
    actor_id: uuid.UUID,
    actor_role: str,
    ip: str | None,
) -> int:
    if bool(ids) == bool(batch_id):
        raise Validation("Provide either ids or batch_id")
    if ids and len(ids) > MAX_VOID_IDS:
        raise Validation(f"At most {MAX_VOID_IDS} ids")
    t = CardStock.__table__
    cond = t.c.id.in_(ids) if ids else t.c.batch_id == batch_id
    voided = (
        await db.execute(
            update(CardStock)
            .where(cond, t.c.tenant_id == tenant_id, t.c.status.in_(("available", "reserved")))
            .values(status="void", void_reason=reason, voided_at=func.now(), voided_by=actor_id)
            .returning(t.c.id)
        )
    ).all()
    await audit(
        db,
        tenant_id=tenant_id,
        action="cards.void",
        actor_id=actor_id,
        actor_role=actor_role,
        target=f"batch:{batch_id}" if batch_id else "cards",
        ip=ip,
        detail={
            "voided": len(voided),
            "reason": reason,
            "requested": len(ids) if ids else None,
            "sample_ids": [str(r[0]) for r in voided[:20]],
        },
    )
    await db.commit()
    return len(voided)


async def replace_card(
    db: AsyncSession,
    sealer: CardSealer,
    *,
    tenant_id: uuid.UUID,
    card_id: uuid.UUID,
    code: str,
    actor_id: uuid.UUID,
    actor_role: str,
    ip: str | None,
) -> dict[str, Any]:
    """Seal ``code`` as a new card and void the old one (``replaced_by``). Records are immutable."""
    t = CardStock.__table__
    old = (
        (
            await db.execute(
                select(*CardStock.public_columns())
                .where(t.c.id == card_id, t.c.tenant_id == tenant_id)
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if old is None:
        raise NotFound("Card not found")
    if old["status"] not in ("available", "reserved"):
        raise Conflict("Only available cards can be replaced; sold cards go through after-sales")
    product = await get_scoped(db, Product, old["product_id"], session_tenant(db))
    assert product is not None
    code = code.strip()
    parsed = split_expiry(code)
    if parsed is None or not _valid_code(parsed[0]):
        raise Validation("invalid card code")
    code, line_exp = parsed
    new_id = uuid.uuid4()
    ctx = CardContext(str(tenant_id), str(product.id), str(new_id))
    sep = product.delivery_separator or "----"
    try:
        new_row = (
            (
                await db.execute(
                    insert(CardStock)
                    .values(
                        id=new_id,
                        tenant_id=tenant_id,
                        product_id=product.id,
                        blob=sealer.seal(code, ctx),
                        fingerprint=sealer.fingerprint(str(tenant_id), code),
                        lookup_fp=sealer.lookup_fingerprint(str(tenant_id), first_field(code, sep)),
                        kid=sealer.active_kid,
                        status="available",
                        created_by=actor_id,
                        batch_id=old["batch_id"],
                        expires_at=line_exp or old["expires_at"],
                    )
                    .returning(*CardStock.public_columns())
                )
            )
            .mappings()
            .one()
        )
    except IntegrityError:
        await db.rollback()
        raise Conflict("This card already exists") from None
    await db.execute(
        update(CardStock)
        .where(t.c.id == card_id)
        .values(
            status="void",
            void_reason="replaced",
            voided_at=func.now(),
            voided_by=actor_id,
            replaced_by=new_id,
        )
    )
    await audit(
        db,
        tenant_id=tenant_id,
        action="cards.replace",
        actor_id=actor_id,
        actor_role=actor_role,
        target=f"card:{card_id}",
        ip=ip,
        detail={"replaced_by": str(new_id), "product_id": str(product.id)},
    )
    await db.commit()
    (item,) = _card_rows([new_row], {})
    return item


async def void_expired(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Hourly job: expired, still-available cards -> void (reason ``expired``)."""
    t = CardStock.__table__
    voided = (
        await db.execute(
            update(CardStock)
            .where(
                t.c.tenant_id == tenant_id,
                t.c.status == "available",
                t.c.expires_at.is_not(None),
                t.c.expires_at <= func.now(),
            )
            .values(status="void", void_reason="expired", voided_at=func.now())
            .returning(t.c.id)
        )
    ).all()
    if voided:
        await audit(
            db,
            tenant_id=tenant_id,
            action="cards.expired",
            target="cards",
            detail={"voided": len(voided)},
        )
    await db.commit()
    return len(voided)
