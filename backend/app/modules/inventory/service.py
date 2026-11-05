"""Card-stock import (seal-only) and masked listing.

The API holds only a :class:`CardSealer`. Rows are inserted with Core
``insert(...).returning(<non-blob columns>)``: role ``app_api`` has no SELECT on
``card_stock.blob``, so the ORM must never load or refresh it here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.cardvault import CardContext, CardSealer
from app.core.deps import Page
from app.core.errors import NotFound, Validation
from app.models import CardStock, Product
from app.modules.common.views import iso

MAX_LINES = 5000
MAX_CODE_LEN = 512
CHUNK = 500


@dataclass(frozen=True)
class ImportResult:
    imported: int
    duplicates: int
    rejected: int


def _valid_code(code: str) -> bool:
    return 0 < len(code) <= MAX_CODE_LEN and all(ord(c) >= 32 and ord(c) != 127 for c in code)


def masked(fingerprint: str) -> str:
    """Display handle derived from the keyed fingerprint: reveals nothing about the code."""
    return "••••-" + fingerprint[:8].upper()


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
    for code in codes:
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
                "kid": sealer.active_kid,
                "status": "available",
                "created_by": actor_id,
            }
        )
    del codes, raw_lines  # drop plaintext references as early as possible
    imported = 0
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
        },
    )
    await db.commit()
    return ImportResult(imported, duplicates, rejected)


async def list_cards(
    db: AsyncSession, product_id: uuid.UUID, status: str | None, page: Page
) -> dict:
    if await db.get(Product, product_id) is None:
        raise NotFound("Product not found")
    cols = CardStock.public_columns()
    t = CardStock.__table__
    base = select(*cols).where(t.c.product_id == product_id)
    if status:
        base = base.where(t.c.status == status)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.execute(
            base.order_by(t.c.created_at.desc(), t.c.id).offset(page.offset).limit(page.page_size)
        )
    ).mappings()
    items = [
        {
            "id": str(r["id"]),
            "masked": masked(r["fingerprint"]),
            "status": r["status"],
            "order_id": str(r["order_id"]) if r["order_id"] else None,
            "created_at": iso(r["created_at"]),
        }
        for r in rows
    ]
    return page.wrap(items, int(total))
