from __future__ import annotations

from app.core.config import get_settings
from app.modules.reseller.service import store_url


def test_store_url_query_default_and_subdomain() -> None:
    base = get_settings().public_base_url.rstrip("/")
    rcfg = {"base_domain": "kesiv.com", "link_mode": "query"}
    assert store_url("my-shop", rcfg) == f"{base}/?store=my-shop"
    assert store_url("my-shop", {"base_domain": "kesiv.com"}) == f"{base}/?store=my-shop"
    assert store_url("my-shop", {**rcfg, "link_mode": "subdomain"}) == "https://my-shop.kesiv.com/"
