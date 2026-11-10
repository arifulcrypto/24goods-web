"""Storefront product ordering (review-v11 W3). Pinned products are first in every sort."""

from __future__ import annotations

from typing import Any

from app.models import Product

SORTS = ("manual", "sales", "price_asc", "price_desc", "newest")


def default_sort(ts: dict[str, Any] | None) -> str:
    raw = (ts or {}).get("catalog")
    value = raw.get("default_sort") if isinstance(raw, dict) else None
    return value if value in SORTS else "manual"


def order_by(sort: str) -> list[Any]:
    pinned = Product.pinned_at.desc().nullslast()
    tail: list[Any]
    if sort == "sales":
        tail = [Product.sold.desc(), Product.group_sort, Product.created_at.desc()]
    elif sort == "price_asc":
        tail = [Product.price_minor.asc(), Product.group_sort, Product.created_at.desc()]
    elif sort == "price_desc":
        tail = [Product.price_minor.desc(), Product.group_sort, Product.created_at.desc()]
    elif sort == "newest":
        tail = [Product.created_at.desc(), Product.group_sort]
    else:  # manual: the staff-set order, best sellers first among equals
        tail = [Product.group_sort, Product.sold.desc(), Product.created_at.desc()]
    return [pinned, *tail, Product.id]
