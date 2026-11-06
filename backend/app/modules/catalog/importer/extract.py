"""Generic extractors: JSON-LD, OpenGraph/meta, embedded app state, heuristics, plain text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import dom as D
from .price import bare_price, find_price, price_only, to_minor

MAX_IMAGES = 20
_JUNK_IMG = re.compile(r"favicon|sprite|/flags?/|\.svg(\?|$)|\.ico(\?|$)|pixel|spacer|1x1", re.I)
_DESC_CLS = [
    r"goods[-_]?details?",
    r"introduce[-_]?content",
    r"product[-_]?(?:detail|desc|info|content)",
    r"item[-_]?detail[-_]?body",
    r"item[-_]?(?:detail|desc)",
    r"description",
    r"detail",
]
_RELATED = r"related|recommend|similar|相关|猜你|cart|footer|header|sidebar"


@dataclass
class Partial:
    title: str = ""
    description_md: str = ""
    images: list[dict[str, str]] = field(default_factory=list)
    price_minor: int | None = None
    currency: str = "CNY"
    meta_description: str = ""

    def add_images(self, urls: list[dict[str, str]]) -> None:
        have = {i["url"] for i in self.images}
        for i in urls:
            if i["url"] not in have and not _JUNK_IMG.search(i["url"]):
                self.images.append(i)
                have.add(i["url"])
        del self.images[MAX_IMAGES:]


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _walk_json(o: Any, depth: int = 0):
    if depth > 12:
        return
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_json(v, depth + 1)
    elif isinstance(o, list):
        for v in o[:200]:
            yield from _walk_json(v, depth + 1)


def _img_list(v: Any, base: str | None, alt: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    items = v if isinstance(v, list) else [v]
    for it in items:
        u = _s(it.get("url") if isinstance(it, dict) else it)
        cu = D.clean_url(u, base) if u else None
        if cu:
            out.append({"url": cu, "alt": alt[:200]})
    return out


def from_json_ld(root: D.Node, base: str | None, p: Partial) -> None:
    for sc in root.find_all("script"):
        if "ld+json" not in sc.attrs.get("type", "").lower():
            continue
        try:
            data = json.loads("".join(c for c in sc.children if isinstance(c, str)))
        except ValueError:
            continue
        for obj in _walk_json(data):
            t = obj.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" not in types:
                continue
            p.title = p.title or _s(obj.get("name"))
            if obj.get("description") and not p.description_md:
                p.description_md = _s(obj["description"])
            p.add_images(_img_list(obj.get("image"), base, p.title))
            offers = obj.get("offers")
            for o in offers if isinstance(offers, list) else [offers]:
                if not isinstance(o, dict) or p.price_minor:
                    continue
                minor = to_minor(o.get("price") or o.get("lowPrice"))
                if minor:
                    p.price_minor = minor
                    p.currency = _s(o.get("priceCurrency")).upper()[:3] or p.currency
            return


def from_meta(root: D.Node, base: str | None, p: Partial) -> None:
    meta: dict[str, str] = {}
    for m in root.find_all("meta"):
        k = (m.attrs.get("property") or m.attrs.get("name") or "").lower()
        if k and k not in meta:
            meta[k] = m.attrs.get("content", "").strip()
    title_el = root.find("title")
    p.title = p.title or meta.get("og:title") or meta.get("twitter:title", "")
    p.meta_description = (
        meta.get("og:description") or meta.get("description") or meta.get("twitter:description", "")
    )
    img = meta.get("og:image") or meta.get("twitter:image")
    if img:
        p.add_images(_img_list(img, base, p.title))
    amount = meta.get("product:price:amount") or meta.get("og:price:amount")
    if amount and not p.price_minor:
        p.price_minor = to_minor(amount)
        p.currency = (meta.get("product:price:currency") or p.currency).upper()[:3]
    if not p.title and title_el:
        p.title = title_el.text()


def from_app_state(root: D.Node, base: str | None, p: Partial) -> None:
    """``__NEXT_DATA__`` / ``__NUXT__`` JSON: first dict that looks like a product."""
    blobs: list[Any] = []
    for sc in root.find_all("script"):
        body = "".join(c for c in sc.children if isinstance(c, str))
        if sc.attrs.get("id") == "__NEXT_DATA__":
            try:
                blobs.append(json.loads(body))
            except ValueError:
                pass
        elif "__NUXT__" in body[:60]:
            i, j = body.find("{"), body.rfind("}")
            if 0 <= i < j:
                try:
                    blobs.append(json.loads(body[i : j + 1]))
                except ValueError:
                    pass
    for blob in blobs:
        for obj in _walk_json(blob):
            name = _s(obj.get("title")) or _s(obj.get("name"))
            price = next((obj[k] for k in ("price", "price_amount", "salePrice") if k in obj), None)
            if name and price is not None and not isinstance(price, (dict, list)):
                p.title = p.title or name
                p.price_minor = p.price_minor or to_minor(price)
                desc = _s(obj.get("description")) or _s(obj.get("content"))
                if desc and not p.description_md:
                    p.description_md = desc
                p.add_images(_img_list(obj.get("image") or obj.get("images"), base, name))
                return


def pick_description(root: D.Node) -> D.Node | None:
    for pat in _DESC_CLS:
        for n in root.find_all(None, pat):
            if n.tag in ("body", "html", "#root", "script"):
                continue
            if n.has_ancestor(_RELATED) or len(n.text()) < 20:
                continue
            return n
    return root.find("main") or root.find("article")


def heuristics(root: D.Node, base: str | None, p: Partial) -> None:
    h1 = root.find("h1")
    if h1 and not p.title:
        p.title = h1.text()
    elif h1 and h1.text() and len(h1.text()) < 300 and (not p.title or p.title in h1.text()):
        p.title = h1.text()
    if not p.price_minor:
        found = None
        for n in root.find_all(None, r"price|amount"):
            if n.has_ancestor(_RELATED) or re.search(_RELATED, n.classes()):
                continue
            if n.attrs.get("data-price") and to_minor(n.attrs["data-price"]):
                found = (to_minor(n.attrs["data-price"]), "CNY")
            else:
                found = bare_price(n.text()) if len(n.text()) < 40 else None
            if found:
                break
        if not found:
            body = root.find("body") or root
            found = find_price(body.text()[:20000])
        if found and found[0]:
            p.price_minor, p.currency = found
    node = pick_description(root)
    if node is not None and not p.description_md:
        p.description_md, imgs = D.to_markdown(node, base)
        p.add_images(imgs)
    if node is None and not p.description_md:
        body = root.find("body") or root
        p.description_md, imgs = D.to_markdown(body, base)
        p.add_images(imgs)
    p.description_md = p.description_md or p.meta_description


def parse_html(html: str, base: str | None) -> tuple[Partial, D.Node]:
    root = D.parse_dom(html)
    p = Partial()
    from_json_ld(root, base, p)
    from_app_state(root, base, p)
    from_meta(root, base, p)
    heuristics(root, base, p)
    return p, root


# ---------------------------------------------------------------- plain text
_MD_LEAD = re.compile(r"^[\s#>*\-•·]+")


def parse_text(text: str) -> Partial:
    p = Partial()
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    title_idx = None
    for i, ln in enumerate(lines):
        clean = _MD_LEAD.sub("", ln).strip()
        if len(clean) >= 2 and not price_only(clean) and not re.fullmatch(r"[\d\W]+", clean):
            p.title, title_idx = clean[:200], i
            break
    pr = find_price(text)
    if pr:
        p.price_minor, p.currency = pr
    rest = [ln for i, ln in enumerate(lines) if i != title_idx]
    p.description_md = re.sub(r"\n{3,}", "\n\n", "\n".join(rest)).strip()
    return p
