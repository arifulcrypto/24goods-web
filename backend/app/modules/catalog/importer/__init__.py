"""Product import (review-v5 §8): turn a supplier page / pasted text into an ``ImportDraft``."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.core import safefetch

from . import extract, sites
from .extract import Partial
from .price import suggest_price

# Warning codes (the admin UI maps these to i18n keys)
W_NO_TITLE, W_NO_PRICE, W_NO_DESC = "no_title", "no_price", "no_description"
W_NO_IMAGES, W_PASTE_PAGE = "no_images", "paste_page"


def _draft(
    p: Partial, source_url: str | None, site: str | None, cfg: dict[str, Any], warns: list[str]
) -> dict[str, Any]:
    title = " ".join(p.title.split())[:200]
    if not title:
        warns.append(W_NO_TITLE)
    if not p.price_minor:
        warns.append(W_NO_PRICE)
    if not p.description_md:
        warns.append(W_NO_DESC)
    if not p.images:
        warns.append(W_NO_IMAGES)
    return {
        "title": title,
        "description_md": p.description_md[:20000],
        "images": p.images[: extract.MAX_IMAGES],
        "price_minor": p.price_minor,
        "currency": p.currency if len(p.currency) == 3 else "CNY",
        "source_url": source_url,
        "site": site,
        "suggested_price_minor": suggest_price(p.price_minor, cfg),
        "warnings": list(dict.fromkeys(warns)),
    }


def parse_pasted(
    *, text: str | None, html: str | None, base_url: str | None, cfg: dict[str, Any]
) -> dict[str, Any]:
    base = base_url if base_url and base_url.startswith(("http://", "https://")) else None
    host = (urlsplit(base).hostname or "") if base else ""
    warns: list[str] = []
    if html and html.strip():
        p, root = extract.parse_html(html, base)
        if host:
            sites.apply_html_adapter(host, root, base, p)
        if not p.title and text and text.strip():  # clipboard html was useless; fall back
            p = extract.parse_text(text)
    else:
        p = extract.parse_text(text or "")
    return _draft(p, base, host or None, cfg, warns)


async def import_from_url(
    url: str,
    cfg: dict[str, Any],
    *,
    resolver: safefetch.Resolver | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    warns: list[str] = []
    api = sites.ka8_api_url(url)
    if api:
        p = None
        try:
            got = await safefetch.fetch_page(
                api, resolver=resolver, transport=transport, accept="application/json"
            )
            p = sites.ka8_from_json(got.text(), got.url)
        except safefetch.UnsafeURL:
            raise
        except safefetch.FetchError:
            pass  # API unreachable (the site may block bots): fall through to the page
        if p is not None:
            host = urlsplit(url).hostname or ""
            return _draft(p, url, host, cfg, warns)
        warns.append(W_PASTE_PAGE)
    page = await safefetch.fetch_page(url, resolver=resolver, transport=transport)
    final = page.url
    host = urlsplit(final).hostname or ""
    if "json" in page.content_type:
        raise safefetch.FetchError("URL is not a product page")
    p, root = extract.parse_html(page.text(), final)
    sites.apply_html_adapter(host, root, final, p)
    # client-rendered shell: nothing but <title>/meta and an empty mount point
    if not p.price_minor and root.find("h1") is None:
        warns.append(W_PASTE_PAGE)
    return _draft(p, url, host, cfg, warns)
