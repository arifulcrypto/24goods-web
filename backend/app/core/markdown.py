"""Markdown -> sanitized HTML (server side, at save time).

``markdown-it-py`` with raw HTML disabled, then an ``nh3`` allowlist pass. The SPA renders the
result with ``v-html`` only from these ``*_html`` fields.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import nh3
from markdown_it import MarkdownIt

ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "a",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "hr",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
_FILE_SRC = re.compile(
    r"^/api/v1/files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def _img_src_ok(src: str) -> bool:
    """Only our own uploads: an external image URL would leak the reader's IP / act as a beacon."""
    return bool(_FILE_SRC.match(src))


def _attribute_filter(tag: str, attr: str, value: str) -> str | None:
    if tag == "img" and attr == "src":
        return value if _img_src_ok(value.strip()) else None
    return value


def render(markdown: str) -> str:
    html = _MD.render(markdown or "")
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes={"a": {"href"}, "img": {"src", "alt"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        attribute_filter=_attribute_filter,
        set_tag_attribute_values={"a": {"target": "_blank"}},
        strip_comments=True,
    )


def render_i18n(values: Mapping[str, str]) -> dict[str, str]:
    return {k: render(v) for k, v in values.items()}
