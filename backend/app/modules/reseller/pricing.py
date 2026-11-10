"""Storefront pricing (review-v5 s7). Integer maths only.

MONEY: ``unit = price_minor + markup``; percent markup = ``ceil(price * permille / 1000)``,
fixed markup = ``+value``. The markup value is clamped to the program maximum
(``resellers.max_markup_permille`` / ``max_markup_fixed_minor``) at PRICING time, so lowering
the cap later (or a hostile PUT) can never create an out-of-policy price. Markup is always
>= 0: a storefront cannot undercut the base price.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import fmt
from app.core.storefront import Storefront
from app.models import Product, ResellerPrice


def markup_per_unit(price_minor: int, kind: str, value: int, sf: Storefront) -> int:
    value = max(0, int(value))
    if kind == "percent":
        value = min(value, sf.max_permille)
        return (price_minor * value + 999) // 1000  # ceil
    return min(value, sf.max_fixed_minor)


@dataclass(frozen=True)
class Override:
    kind: str
    value: int
    hidden: bool


class Pricer:
    """Per-request storefront price calculator (overrides loaded once)."""

    def __init__(self, sf: Storefront, overrides: dict[uuid.UUID, Override]):
        self.sf = sf
        self._ov = overrides

    @classmethod
    async def load(cls, db: AsyncSession, sf: Storefront | None) -> Pricer | None:
        if sf is None:
            return None
        rows = await db.scalars(
            select(ResellerPrice).where(
                ResellerPrice.reseller_id == sf.id, ResellerPrice.tenant_id == sf.tenant_id
            )
        )
        return cls(
            sf, {r.product_id: Override(r.markup_kind, int(r.markup_value), r.hidden) for r in rows}
        )

    def hidden_ids(self) -> list[uuid.UUID]:
        return [pid for pid, o in self._ov.items() if o.hidden]

    def is_hidden(self, product_id: uuid.UUID) -> bool:
        o = self._ov.get(product_id)
        return bool(o and o.hidden)

    def markup(self, p: Product, base: int | None = None) -> int:
        """``base`` = the variant's price when buying a variant (default: the product's)."""
        o = self._ov.get(p.id)
        kind, value = (o.kind, o.value) if o else (self.sf.markup_kind, self.sf.markup_value)
        return markup_per_unit(p.price_minor if base is None else base, kind, value, self.sf)

    def unit(self, p: Product, base: int | None = None) -> int:
        return (p.price_minor if base is None else base) + self.markup(p, base)


def reprice(out: dict[str, Any], p: Product, pricer: Pricer | None) -> dict[str, Any]:
    """Overwrite the ``price`` of a catalog payload with the storefront price."""
    if pricer is not None:
        out["price"] = fmt(pricer.unit(p))
    return out
