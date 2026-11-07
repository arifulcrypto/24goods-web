"""Crawler / first-paint HTML for the buyer SPA (review-v5.2 §6, ADR-024).

Pure functions: the SPA's built ``index.html`` is the template; per route we replace the head
metadata and put a pre-rendered content block inside ``<div id="app">`` (Vue replaces it on
mount). Every dynamic value goes through :func:`esc`; JSON-LD through :func:`ld_json`.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import nh3

LANGS = ("en", "zh-CN")
APP_DIV = '<div id="app"></div>'

_HEAD_STRIP = re.compile(
    r"<title\b.*?</title>"
    r"|<meta\b[^>]*\b(?:name|property)\s*=\s*[\"'](?:description|robots|og:[^\"']*|twitter:[^\"']*)[\"'][^>]*>"
    r"|<link\b[^>]*\brel\s*=\s*[\"'](?:canonical|alternate)[\"'][^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_LANG = re.compile(r"<html\b([^>]*)\blang\s*=\s*\"[^\"]*\"", re.IGNORECASE)
_WS = re.compile(r"\s+")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def ld_json(data: dict[str, Any] | list[Any]) -> str:
    """JSON for a ``<script type="application/ld+json">`` body: no ``</script>`` breakout."""
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def plain(html_or_text: str, limit: int = 160) -> str:
    """Sanitized HTML -> single-line plain text, cut at a word boundary."""
    text = html.unescape(nh3.clean(html_or_text or "", tags=set()))
    text = _WS.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


def url_for(base: str, path: str, lang: str = "en", **query: str) -> str:
    q = {k: v for k, v in query.items() if v}
    if lang != "en":
        q = {"lang": lang, **q}
    return base + path + ("?" + urlencode(q, quote_via=quote) if q else "")


@dataclass
class Page:
    """Everything the head + body of one SEO document needs."""

    path: str  # canonical path, e.g. /p/<id>
    lang: str
    base: str
    title: str
    description: str
    site_name: str
    index: bool = True  # false -> noindex
    follow: bool = True
    og_type: str = "website"
    image: str | None = None
    body_html: str = ""  # trusted HTML (already escaped / sanitized by the builder)
    ld: list[dict[str, Any]] = field(default_factory=list)
    status: int = 200
    alternates: bool = True


def head_block(p: Page) -> str:
    canon = url_for(p.base, p.path, p.lang)
    robots = f"{'index' if p.index else 'noindex'},{'follow' if p.follow else 'nofollow'}"
    out = [
        f"<title>{esc(p.title)}</title>",
        f'<meta name="description" content="{esc(p.description)}" />',
        f'<meta name="robots" content="{robots}" />',
    ]
    if p.status == 200:
        out.append(f'<link rel="canonical" href="{esc(canon)}" />')
        if p.alternates:
            for lang in LANGS:
                out.append(
                    f'<link rel="alternate" hreflang="{lang}" '
                    f'href="{esc(url_for(p.base, p.path, lang))}" />'
                )
            out.append(
                f'<link rel="alternate" hreflang="x-default" '
                f'href="{esc(url_for(p.base, p.path, "en"))}" />'
            )
        og = {
            "og:site_name": p.site_name,
            "og:type": p.og_type,
            "og:title": p.title,
            "og:description": p.description,
            "og:url": canon,
            "og:locale": "zh_CN" if p.lang == "zh-CN" else "en_US",
        }
        if p.image:
            og["og:image"] = p.image
        for k, v in og.items():
            out.append(f'<meta property="{k}" content="{esc(v)}" />')
        tw = {
            "twitter:card": "summary_large_image" if p.image else "summary",
            "twitter:title": p.title,
            "twitter:description": p.description,
        }
        if p.image:
            tw["twitter:image"] = p.image
        for k, v in tw.items():
            out.append(f'<meta name="{k}" content="{esc(v)}" />')
        for obj in p.ld:
            out.append(f'<script type="application/ld+json">{ld_json(obj)}</script>')
    return "\n    ".join(out)


def inject(template: str, p: Page) -> str:
    """Template ``index.html`` -> the full document for ``p``."""
    doc = _HEAD_STRIP.sub("", template)
    doc = _HTML_LANG.sub(lambda m: f'<html{m.group(1)}lang="{esc(p.lang)}"', doc, count=1)
    doc = doc.replace("</head>", f"    {head_block(p)}\n  </head>", 1)
    block = f'<div id="app"><main id="seo-content">{p.body_html}</main></div>'
    if APP_DIV in doc:
        doc = doc.replace(APP_DIV, block, 1)
    else:  # an unexpected template: still hand crawlers the content
        fallback = block.replace('id="app"', 'id="seo"')
        doc = doc.replace("</body>", f"{fallback}</body>", 1)
    return doc


# ----------------------------------------------------------------- body fragments
def link(base: str, path: str, text: str, lang: str) -> str:
    return f'<a href="{esc(url_for(base, path, lang))}">{esc(text)}</a>'


def lang_links(p_base: str, path: str, current: str) -> str:
    names = {"en": "English", "zh-CN": "简体中文"}
    items = [
        f'<a href="{esc(url_for(p_base, path, lg))}" hreflang="{lg}" lang="{lg}">{names[lg]}</a>'
        for lg in LANGS
        if lg != current
    ]
    return f"<p>{' · '.join(items)}</p>"
