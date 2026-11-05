from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page
from app.core.errors import NotFound
from app.models import AgentStatus, CardStock, Product, User
from app.modules.common.views import iso, product_summary, product_view

STATUS_RANK = {"online": 0, "on_hold": 1, "away": 2, "offline": 3}


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def stock_counts(db: AsyncSession, product_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    ids = list(product_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(CardStock.product_id, func.count(CardStock.id))
        .where(CardStock.product_id.in_(ids), CardStock.status == "available")
        .group_by(CardStock.product_id)
    )
    counts = {pid: 0 for pid in ids}
    counts.update({pid: int(n) for pid, n in rows.all()})
    return counts


def apply_search(stmt: Select, q: str | None) -> Select:
    if not q:
        return stmt
    q = q.strip()[:100]
    if not q:
        return stmt
    lowered = q.lower()
    return stmt.where(
        or_(
            Product.search_tsv.op("@@")(func.plainto_tsquery("simple", q)),
            Product.search_text.ilike(f"%{escape_like(lowered)}%", escape="\\"),
            # `search_text %> q` == `q <% search_text`: word-similarity operator served by the
            # GIN gin_trgm_ops index (a word_similarity() > x comparison cannot use it).
            Product.search_text.op("%>")(lowered),
        )
    )


WORD_SIMILARITY_THRESHOLD = "0.4"


async def prepare_search(db: AsyncSession, q: str | None) -> None:
    """Transaction-local trigram threshold for the ``%>`` operator (default 0.6 is strict)."""
    if q and q.strip():
        await db.execute(
            text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
            {"t": WORD_SIMILARITY_THRESHOLD},
        )


async def list_products(
    db: AsyncSession, lang: str, page: Page, q: str | None, category: str | None
) -> dict:
    stmt = select(Product).where(Product.status == "active")
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = apply_search(stmt, q)
    await prepare_search(db, q)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = [Product.sold.desc(), Product.created_at.desc(), Product.id]
    if q and q.strip():
        order.insert(0, func.word_similarity(q.strip().lower(), Product.search_text).desc())
    rows = (await db.scalars(stmt.order_by(*order).offset(page.offset).limit(page.page_size))).all()
    stock = await stock_counts(db, [p.id for p in rows if p.delivery_mode == "auto_card"])
    return page.wrap([product_summary(p, lang, stock.get(p.id)) for p in rows], int(total))


async def get_active_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    p = await db.scalar(select(Product).where(Product.id == product_id, Product.status == "active"))
    if p is None:
        raise NotFound("Product not found")
    return p


async def product_detail(db: AsyncSession, lang: str, product_id: uuid.UUID) -> dict:
    p = await get_active_product(db, product_id)
    stock = (await stock_counts(db, [p.id])).get(p.id) if p.delivery_mode == "auto_card" else None
    return product_view(p, lang, stock)


async def support_status(db: AsyncSession) -> dict:
    rows = (
        (
            await db.execute(
                select(AgentStatus)
                .join(User, User.id == AgentStatus.user_id)
                .where(User.role.in_(("agent", "admin")), User.status == "active")
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"status": "offline", "message": None, "updated_at": iso(datetime.now(timezone.utc))}
    best = min(rows, key=lambda r: (STATUS_RANK.get(r.status, 9), -r.updated_at.timestamp()))
    return {"status": best.status, "message": best.message, "updated_at": iso(best.updated_at)}
