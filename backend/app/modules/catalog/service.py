from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import Select, Text, bindparam, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY as PGARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page
from app.core.errors import NotFound, Validation
from app.core.storefront import Storefront
from app.models import AgentStatus, CardStock, Product, User
from app.modules.catalog import variants
from app.modules.catalog.categories import disabled_slugs
from app.modules.common.views import iso, product_summary, product_view
from app.modules.content.site import tenant_settings
from app.modules.reseller.pricing import Pricer, reprice

STATUS_RANK = {"online": 0, "on_hold": 1, "away": 2, "offline": 3}


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def stock_counts(db: AsyncSession, product_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    ids = list(product_ids)
    if not ids:
        return {}
    rows = await db.execute(
        select(CardStock.product_id, func.count(CardStock.id))
        .where(CardStock.product_id.in_(ids), *claimable())
        .group_by(CardStock.product_id)
    )
    counts = {pid: 0 for pid in ids}
    counts.update({pid: int(n) for pid, n in rows.all()})
    return counts


def claimable() -> tuple:
    """WHERE parts for stock a buyer can still be delivered: available and not expired."""
    t = CardStock.__table__.c
    return (t.status == "available", or_(t.expires_at.is_(None), t.expires_at > func.now()))


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
    db: AsyncSession,
    lang: str,
    page: Page,
    q: str | None,
    category: str | None,
    tenant_id: uuid.UUID,
    storefront: Storefront | None = None,
) -> dict:
    stmt = select(Product).where(Product.status == "active")
    ts = await tenant_settings(db, tenant_id)
    pricer = await Pricer.load(db, storefront)
    if pricer is not None and pricer.hidden_ids():  # products this reseller hides
        stmt = stmt.where(Product.id.not_in(pricer.hidden_ids()))
    hidden = await disabled_slugs(db, tenant_id)  # products of disabled categories are unlisted
    if hidden:
        stmt = stmt.where(Product.category.not_in(hidden))
    if category:
        stmt = stmt.where(Product.category == category)
    stmt = apply_search(stmt, q)
    await prepare_search(db, q)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = [Product.group_sort, Product.sold.desc(), Product.created_at.desc(), Product.id]
    if q and q.strip():
        order.insert(0, func.word_similarity(q.strip().lower(), Product.search_text).desc())
    rows = (await db.scalars(stmt.order_by(*order).offset(page.offset).limit(page.page_size))).all()
    stock = await stock_counts(db, [p.id for p in rows if p.delivery_mode == "auto_card"])
    outs = [reprice(product_summary(p, lang, stock.get(p.id), ts), p, pricer) for p in rows]
    await variants.decorate(db, tenant_id, list(zip(rows, outs, strict=True)), lang, ts, pricer)
    return page.wrap(outs, int(total))


async def get_active_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    p = await db.scalar(select(Product).where(Product.id == product_id, Product.status == "active"))
    if p is None:
        raise NotFound("Product not found")
    return p


async def product_detail(
    db: AsyncSession,
    lang: str,
    product_id: uuid.UUID,
    tenant_id: uuid.UUID,
    storefront: Storefront | None = None,
) -> dict:
    p = await get_active_product(db, product_id)
    pricer = await Pricer.load(db, storefront)
    if pricer is not None and pricer.is_hidden(p.id):
        raise NotFound("Product not found")
    stock = (await stock_counts(db, [p.id])).get(p.id) if p.delivery_mode == "auto_card" else None
    ts = await tenant_settings(db, tenant_id)
    out = reprice(product_view(p, lang, stock, ts), p, pricer)
    await variants.decorate(db, tenant_id, [(p, out)], lang, ts, pricer)
    return out


MAX_IDS = 20
REC_TTL_S = 300

_RECOMMEND_SQL = text(
    """
WITH ctx AS (
    SELECT id, category FROM products WHERE tenant_id = :tid AND id = ANY(:ctx)
),
paid AS (
    SELECT buyer_id, product_id, qty, paid_at FROM orders
    WHERE tenant_id = :tid AND paid_at IS NOT NULL AND product_id IS NOT NULL
      AND paid_at > now() - interval '90 days'
),
co AS (
    SELECT b.product_id, count(DISTINCT b.buyer_id) AS n
    FROM paid a JOIN paid b ON a.buyer_id = b.buyer_id AND b.product_id <> a.product_id
    WHERE a.product_id IN (SELECT id FROM ctx) GROUP BY b.product_id
),
s30 AS (
    SELECT product_id, sum(qty) AS n FROM paid
    WHERE paid_at > now() - interval '30 days' GROUP BY product_id
)
SELECT p.id FROM products p
LEFT JOIN co ON co.product_id = p.id
LEFT JOIN s30 ON s30.product_id = p.id
WHERE p.tenant_id = :tid AND p.status = 'active'
  AND NOT (p.id = ANY(:excl))
  AND NOT (p.category = ANY(:dis))
  AND (p.delivery_mode = 'manual' OR EXISTS (
        SELECT 1 FROM card_stock cs WHERE cs.product_id = p.id AND cs.status = 'available'
          AND (cs.expires_at IS NULL OR cs.expires_at > now())))
ORDER BY (3 * (p.category IN (SELECT category FROM ctx))::int
          + 2 * coalesce(co.n, 0)
          + ln(1 + coalesce(s30.n, 0))
          + 0.5 * (p.created_at > now() - interval '14 days')::int) DESC,
         p.sold DESC, p.id
LIMIT :lim
"""
).bindparams(
    bindparam("ctx", type_=PGARRAY(PGUUID(as_uuid=True))),
    bindparam("excl", type_=PGARRAY(PGUUID(as_uuid=True))),
    bindparam("dis", type_=PGARRAY(Text)),
)


def parse_ids(raw: str | None, limit: int = MAX_IDS) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            u = uuid.UUID(part)
        except ValueError:
            raise Validation("ids must be comma-separated UUIDs") from None
        if u not in out:
            out.append(u)
    if len(out) > limit:
        raise Validation(f"at most {limit} ids")
    return out


async def products_by_ids(
    db: AsyncSession,
    lang: str,
    ids: list[uuid.UUID],
    tenant_id: uuid.UUID,
    storefront: Storefront | None = None,
    *,
    hide_disabled: bool = False,
) -> list[dict]:
    """Active products in the order of ``ids`` (unknown / draft / archived ids are skipped)."""
    if not ids:
        return []
    stmt = select(Product).where(Product.id.in_(ids), Product.status == "active")
    if hide_disabled and (hidden := await disabled_slugs(db, tenant_id)):
        stmt = stmt.where(Product.category.not_in(hidden))
    rows = {p.id: p for p in await db.scalars(stmt)}
    pricer = await Pricer.load(db, storefront)
    if pricer is not None:
        rows = {i: p for i, p in rows.items() if not pricer.is_hidden(i)}
    stock = await stock_counts(db, [p.id for p in rows.values() if p.delivery_mode == "auto_card"])
    ts = await tenant_settings(db, tenant_id)
    kept = [i for i in ids if i in rows]
    outs = [
        reprice(product_summary(rows[i], lang, stock.get(i), ts), rows[i], pricer) for i in kept
    ]
    await variants.decorate(
        db, tenant_id, [(rows[i], o) for i, o in zip(kept, outs, strict=True)], lang, ts, pricer
    )
    return outs


async def recommended(
    db: AsyncSession,
    redis: Redis,
    lang: str,
    tenant_id: uuid.UUID,
    for_id: uuid.UUID | None,
    recent: list[uuid.UUID],
    limit: int,
    storefront: Storefront | None = None,
) -> list[dict]:
    ctx = list(dict.fromkeys(([for_id] if for_id else []) + recent))
    key = "rec:{}:{}:{}:{}".format(
        tenant_id,
        for_id or "-",
        hashlib.sha256(",".join(map(str, recent)).encode()).hexdigest()[:16],
        limit,
    )
    cached = await redis.get(key)
    ids: list[uuid.UUID]
    if cached:
        ids = [uuid.UUID(x) for x in json.loads(cached)]
    else:
        rows = await db.execute(
            _RECOMMEND_SQL,
            {
                "tid": tenant_id,
                "ctx": ctx,
                "excl": [for_id] if for_id else [],
                "dis": await disabled_slugs(db, tenant_id),
                "lim": limit,
            },
        )
        ids = [r[0] for r in rows.all()]
        await redis.set(key, json.dumps([str(i) for i in ids]), ex=REC_TTL_S)
    return await products_by_ids(db, lang, ids, tenant_id, storefront, hide_disabled=True)


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
