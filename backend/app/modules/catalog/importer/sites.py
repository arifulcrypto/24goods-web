"""Per-site adapters. Each takes the parsed DOM and refines a :class:`Partial`."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlsplit

from . import dom as D
from .extract import Partial
from .price import find_price, to_minor

SPA_HOSTS = {"go.ka8.ai": "ka8"}


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _first_text(root: D.Node, tag: str | None, cls: str | None) -> str:
    n = root.find(tag, cls)
    return n.text() if n else ""


def gmailbuy(root: D.Node, base: str | None, p: Partial) -> None:
    title = _first_text(root, "h3", r"good-title") or _first_text(root, None, r"good-title")
    h3 = root.find("div", r"good-title")
    if h3 is not None and h3.find("h3") is not None:
        title = h3.find("h3").text()  # type: ignore[union-attr]
    if title:
        p.title = title
    price = root.find("span", r"goods_price")
    if price is not None and (m := to_minor(price.text())):
        p.price_minor, p.currency = m, "CNY"
    det = root.find(None, r"goods_details")
    if det is not None:
        p.description_md, imgs = D.to_markdown(det, base)
        p.images = [i for i in p.images if "goods_image" in i["url"]]
        main = root.find("img", r"goods_image")
        if main is not None and (u := D.clean_url(main.attrs.get("src", ""), base)):
            p.images.insert(0, {"url": u, "alt": main.attrs.get("alt", "")[:200]})
        p.add_images(imgs)


def maitg(root: D.Node, base: str | None, p: Partial) -> None:
    h1 = root.find("h1")
    if h1 is not None and h1.text():
        p.title = h1.text()
    prices = [
        m for n in root.find_all(None, r"sku-item") if (m := to_minor(n.attrs.get("data-price")))
    ]
    if prices:  # "from" price = cheapest SKU
        p.price_minor, p.currency = min(prices), "CNY"
    elif (amt := root.find(None, r"item-amount")) is not None:
        if f := find_price(amt.text()):
            p.price_minor, p.currency = f
    body = root.find(None, r"introduce-content") or root.find(None, r"bz-item-detail-body")
    if body is not None:
        p.description_md, imgs = D.to_markdown(body, base)
        thumb = root.find("img", r"item-image-show")
        p.images = []
        if thumb is not None and (u := D.clean_url(thumb.attrs.get("src", ""), base)):
            p.images.append({"url": u, "alt": thumb.attrs.get("alt", "")[:200]})
        p.add_images(imgs)


HTML_ADAPTERS: dict[str, Callable[[D.Node, str | None, Partial], None]] = {
    "gmailbuy.com": gmailbuy,
    "maitg.com": maitg,
}


def apply_html_adapter(host: str, root: D.Node, base: str | None, p: Partial) -> str | None:
    for domain, fn in HTML_ADAPTERS.items():
        if host_matches(host, domain):
            fn(root, base, p)
            return domain
    return None


# ---------------------------------------------------------------- go.ka8.ai (SPA + JSON API)
def ka8_api_url(url: str) -> str | None:
    """``https://go.ka8.ai/products/<slug>`` → its public JSON API (found in the SPA bundle)."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if (parts.hostname or "") in SPA_HOSTS and len(segs) == 2 and segs[0] == "products":
        return f"{parts.scheme}://{parts.hostname}/api/v1/public/products/{segs[1]}"
    return None


def _i18n(v: object) -> str:
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("zh-CN", "en-US", *v.keys()):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
    return ""


def ka8_from_json(body: str, base: str) -> Partial | None:
    try:
        data = json.loads(body)["data"]
        assert isinstance(data, dict)
    except (ValueError, KeyError, TypeError, AssertionError):
        return None
    p = Partial(title=_i18n(data.get("title")), currency="CNY")
    content = _i18n(data.get("content")) or _i18n(data.get("description"))
    p.description_md, imgs = D.to_markdown(D.parse_dom(content), base)
    prices = [
        to_minor(s.get("price_amount")) for s in data.get("skus") or [] if isinstance(s, dict)
    ]
    prices = [x for x in prices if x]
    p.price_minor = min(prices) if prices else to_minor(data.get("price_amount"))
    for im in data.get("images") or []:
        u = D.clean_url(im.get("url") if isinstance(im, dict) else str(im), base)
        if u:
            p.images.append({"url": u, "alt": p.title[:200]})
    p.add_images(imgs)
    return p if p.title else None
