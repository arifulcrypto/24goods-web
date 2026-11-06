"""Tiny DOM + HTML→markdown converter on ``html.parser`` (no third-party dependency)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

VOID = {"br", "img", "hr", "meta", "link", "input", "source", "track", "wbr", "area", "base", "col"}
DROP = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "form",
    "noscript",
    "svg",
    "iframe",
    "button",
    "select",
    "template",
    "video",
    "audio",
    "object",
    "embed",
    "head",
    "aside",
    "textarea",
    "canvas",
}
BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "ul",
    "ol",
    "table",
    "tr",
    "blockquote",
    "figure",
    "figcaption",
    "pre",
    "dl",
    "dt",
    "dd",
    "hr",
}
MAX_DEPTH = 80
MAX_NODES = 200_000
_WS = re.compile(r"[ \t\r\n\f ]+")


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = None

    def classes(self) -> str:
        return f"{self.attrs.get('class', '')} {self.attrs.get('id', '')}".lower()

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list[Node]:
        rx = re.compile(cls) if cls else None
        return [
            n
            for n in self.walk()
            if (tag is None or n.tag == tag) and (rx is None or rx.search(n.classes()))
        ]

    def find(self, tag: str | None = None, cls: str | None = None) -> Node | None:
        r = self.find_all(tag, cls)
        return r[0] if r else None

    def text(self) -> str:
        parts: list[str] = []
        stack: list[Node | str] = [self]
        while stack:
            n = stack.pop()
            if isinstance(n, str):
                parts.append(n)
            elif n.tag not in ("script", "style"):
                stack.extend(reversed(n.children))
        return _WS.sub(" ", "".join(parts)).strip()

    def has_ancestor(self, cls: str) -> bool:
        rx = re.compile(cls)
        p = self.parent
        while p is not None:
            if rx.search(p.classes()):
                return True
            p = p.parent
        return False


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root
        self.depth = 0
        self.ignored: list[str] = []  # tags opened beyond MAX_DEPTH
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.count += 1
        if self.count > MAX_NODES:
            return
        a = {k: (v or "") for k, v in attrs}
        if tag in ("p", "li", "tr", "td", "th") and self.cur.tag == tag:
            self._close(tag)
        node = Node(tag, a, parent=self.cur)
        self.cur.children.append(node)
        if tag in VOID:
            return
        if self.depth >= MAX_DEPTH:
            self.ignored.append(tag)
            return
        self.cur = node
        self.depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def _close(self, tag: str) -> None:
        n: Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is None or n.parent is None:
            return
        while self.cur is not n.parent:
            assert self.cur.parent is not None
            self.cur = self.cur.parent
            self.depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if self.ignored and self.ignored[-1] == tag:
            self.ignored.pop()
            return
        self._close(tag)

    def handle_data(self, data: str) -> None:
        if self.count <= MAX_NODES:
            self.cur.children.append(data)


def parse_dom(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


# ---------------------------------------------------------------- URLs
def clean_url(raw: str, base: str | None) -> str | None:
    """Absolute http(s) URL or None. Repairs ``https://hosthttps://cdn/x`` style concatenation."""
    raw = (raw or "").strip()
    if not raw or raw.startswith(("data:", "javascript:", "blob:", "#")):
        return None
    i = raw.rfind("http://"), raw.rfind("https://")
    j = max(i)
    if j > 0:
        raw = raw[j:]
    if raw.startswith("//"):
        raw = "https:" + raw
    url = urljoin(base, raw) if base else raw
    return url if url.startswith(("http://", "https://")) and len(url) <= 2000 else None


# ---------------------------------------------------------------- markdown
class _MD:
    def __init__(self, base: str | None) -> None:
        self.base = base
        self.out: list[str] = []
        self.images: list[dict[str, str]] = []
        self.list_stack: list[list[int]] = []  # [ordered?, counter]

    def block(self) -> None:
        self.out.append("\n\n")

    def inline_wrap(self, n: Node, pre: str, post: str, fmt=None) -> None:
        start = len(self.out)
        self.children(n)
        seg = "".join(self.out[start:])
        del self.out[start:]
        core = seg.strip()
        if not core:
            self.out.append(seg)
            return
        lead = seg[: len(seg) - len(seg.lstrip())]
        trail = seg[len(seg.rstrip()) :]
        self.out.append(lead + (fmt(core) if fmt else f"{pre}{core}{post}") + trail)

    def children(self, n: Node) -> None:
        for c in n.children:
            if isinstance(c, str):
                self.out.append(_WS.sub(" ", c))
            else:
                self.node(c)

    def node(self, n: Node) -> None:
        t = n.tag
        if t in DROP:
            return
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.block()
            start = len(self.out)
            self.children(n)
            seg = _WS.sub(" ", "".join(self.out[start:])).strip().replace("**", "")
            del self.out[start:]
            if seg:
                self.out.append("#" * max(2, int(t[1])) + " " + seg)
            self.block()
        elif t == "br":
            self.out.append("  \n")
        elif t in ("strong", "b"):
            self.inline_wrap(n, "**", "**")
        elif t in ("em", "i"):
            self.inline_wrap(n, "*", "*")
        elif t == "a":
            href = clean_url(n.attrs.get("href", ""), self.base)
            if href and not href.startswith("javascript"):
                self.inline_wrap(n, "", "", lambda s: f"[{s.replace(']', ')')}]({href})")
            else:
                self.children(n)
        elif t == "img":
            src = clean_url(n.attrs.get("src") or n.attrs.get("data-src", ""), self.base)
            if src:
                alt = _WS.sub(" ", n.attrs.get("alt", "")).strip().replace("]", ")")
                self.images.append({"url": src, "alt": alt[:200]})
                self.out.append(f"![{alt}]({src})")
        elif t in ("ul", "ol"):
            self.block()
            self.list_stack.append([1 if t == "ol" else 0, 0])
            self.children(n)
            self.list_stack.pop()
            self.block()
        elif t == "li":
            ind = "  " * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1][0]:
                self.list_stack[-1][1] += 1
                mark = f"{self.list_stack[-1][1]}. "
            else:
                mark = "- "
            start = len(self.out)
            self.children(n)
            seg = "".join(self.out[start:])
            del self.out[start:]
            self.out.append("\n" + ind + mark + re.sub(r"\n{2,}", "\n", seg).strip())
        elif t in ("td", "th"):
            self.children(n)
            self.out.append(" ")
        elif t == "pre":
            self.block()
            self.out.append("```\n" + n.text() + "\n```")
            self.block()
        elif t == "blockquote":
            self.block()
            start = len(self.out)
            self.children(n)
            seg = "".join(self.out[start:]).strip()
            del self.out[start:]
            self.out.append("\n".join("> " + ln for ln in seg.splitlines() if ln.strip()))
            self.block()
        elif t == "hr":
            self.block()
            self.out.append("---")
            self.block()
        elif t in BLOCK:
            self.block()
            self.children(n)
            self.block()
        else:
            self.children(n)


def to_markdown(root: Node, base: str | None = None) -> tuple[str, list[dict[str, str]]]:
    md = _MD(base)
    if root.tag == "#root":
        md.children(root)
    else:
        md.node(root)
    lines: list[str] = []
    fence = False
    for ln in "".join(md.out).split("\n"):
        if ln.startswith("```"):
            fence = not fence
        elif not fence:
            br = ln.endswith("  ")
            item = re.match(r"^( *)(?:- |\d+\. )", ln)
            ind = item.group(1) if item else ""
            ln = ind + re.sub(r"(?<=\S) {2,}(?=\S)", " ", ln.strip(" "))
            ln = ln + "  " if br and ln else ln
        lines.append(ln)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = re.sub(r"(?m)^[!！]$\n*", "", text)
    seen: set[str] = set()
    imgs = []
    for i in md.images:
        if i["url"] not in seen:
            seen.add(i["url"])
            imgs.append(i)
    return text, imgs
