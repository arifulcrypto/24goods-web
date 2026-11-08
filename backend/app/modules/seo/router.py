"""SEO routes mounted at the site ROOT (not /api): crawler/first-paint HTML, sitemap, robots.

Caddy sends exactly these paths here (``/``, ``/p/*``, ``/a/*``, ``/help``, ``/search``,
``/sitemap.xml``, ``/robots.txt``); everything else stays static. No auth, no cookies, tenant
from the Host header, every query tenant-filtered. ADR-024.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Page, get_db, get_res, get_tenant_id
from app.core.i18n import normalize, pick
from app.core.markdown import sanitize_html
from app.core.resources import Resources
from app.core.settings_v5 import section
from app.core.storefront import Storefront, is_custom_host, resolve
from app.models import Article, Product
from app.modules.catalog import service as catalog
from app.modules.catalog.categories import disabled_slugs
from app.modules.content.site import tenant_settings
from app.modules.reseller.pricing import Pricer
from app.modules.seo import template
from app.modules.seo.render import Page as Doc
from app.modules.seo.render import esc, inject, lang_links, link, plain, url_for

router = APIRouter(include_in_schema=False)

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
OK_CACHE = "public, max-age=300"  # sitemap + robots
# HTML embeds hashed asset URLs: browsers must revalidate (a deploy removes the old assets),
# shared caches (Cloudflare) may keep it 5 minutes.
HTML_CACHE = "public, max-age=0, s-maxage=300, must-revalidate"
MISS_CACHE = "public, max-age=60"
SITEMAP_TTL_S = 600.0
SITEMAP_MAX_PRODUCTS = 10_000
HOME_PRODUCTS = 24
DISALLOW = ("/admin", "/api", "/cart", "/orders", "/pay", "/me", "/messages", "/reseller",
            "/chat", "/lookup")  # fmt: skip

# Agents whose published purpose is collecting content for model training or answer synthesis
# rather than sending visitors to the site. Search crawlers are deliberately NOT listed: being
# indexable is how buyers arrive. Names are the vendors' own documented tokens.
TRAINING_AGENTS = (
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "anthropic-ai",
    "CCBot",
    "Google-Extended",
    "Applebot-Extended",
    "meta-externalagent",
    "FacebookBot",
    "Bytespider",
    "PerplexityBot",
    "Perplexity-User",
    "Diffbot",
    "Omgilibot",
    "ImagesiftBot",
    "AI2Bot",
    "Timpibot",
)

TAGLINE = {
    "en": "Accounts, gift cards & digital services — delivered fast",
    "zh-CN": "账号 · 卡密 · 数字服务，快速交付",
}
T = {  # strings of the pre-rendered fallback pages (the SPA has its own i18n)
    "en": {
        "home": "Home",
        "help": "Help center",
        "search": "Search",
        "price": "Price",
        "in_stock": "In stock",
        "out_stock": "Out of stock",
        "manual": "Delivered manually by our team",
        "auto": "Instant automatic delivery",
        "nf": "Page not found",
        "nf_text": "This page does not exist or is no longer available.",
        "products": "Products",
        "articles": "Articles",
        "help_desc": "Guides, FAQ and policies",
    },
    "zh-CN": {
        "home": "首页",
        "help": "帮助中心",
        "search": "搜索",
        "price": "价格",
        "in_stock": "有货",
        "out_stock": "缺货",
        "manual": "人工发货",
        "auto": "自动即时发货",
        "nf": "页面不存在",
        "nf_text": "该页面不存在或已下架。",
        "products": "商品",
        "articles": "文章",
        "help_desc": "使用指南、常见问题与规则",
    },
}


class Ctx:
    """Per-request facts every SEO document needs."""

    def __init__(self, request: Request, lang: str, base: str, ts: dict, sf: Storefront | None):
        self.request, self.lang, self.base, self.sf = request, lang, base, sf
        self.seo = section(ts, "seo")
        self.general = section(ts, "general")
        res: Resources = request.app.state.res
        brand = res.settings.brand_name
        named = pick(self.general["site_name_i18n"], lang) or brand
        self.site_name = sf.store_name if sf else named
        self.indexable = bool(self.seo["indexing_enabled"]) and (
            sf is None or bool(self.seo["index_storefronts"])
        )
        self.t = T[lang]

    @property
    def description(self) -> str:
        return (
            pick(self.seo["default_description_i18n"], self.lang)
            or pick(self.general["subtitle_i18n"], self.lang)
            or TAGLINE[self.lang]
        )

    def abs(self, src: str | None) -> str | None:
        src = (src or "").strip()
        if src.startswith("https://"):
            return src
        if src.startswith("/") and not src.startswith("//"):
            return self.base + src
        return None

    def og_image(self) -> str | None:
        return self.abs(self.seo["og_image"])

    def doc(self, path: str, title: str, description: str, **kw: Any) -> Doc:
        kw.setdefault("index", self.indexable)
        kw.setdefault("follow", self.indexable)
        kw.setdefault("image", self.og_image())
        return Doc(
            path=path,
            lang=self.lang,
            base=self.base,
            title=title,
            description=description or self.description,
            site_name=self.site_name,
            **kw,
        )


async def _ctx(request: Request, db: AsyncSession, tenant_id: uuid.UUID, res: Resources) -> Ctx:
    ts = await tenant_settings(db, tenant_id)
    q = request.query_params
    lang = (normalize(q.get("lang") or "") or normalize(section(ts, "defaults")["locale"])) or "en"
    host = request.headers.get("host")
    sf = await resolve(
        db,
        tenant_id,
        host,
        q.get("store"),  # never X-Storefront: not part of the CDN cache key (cache poisoning)
        ttl=0.0 if res.settings.env == "test" else 15.0,
    )
    public = urlparse(res.settings.public_base_url)
    hostname = (host or "").strip().lower()
    base = res.settings.public_base_url.rstrip("/")
    # Reflect the request host only for hosts we resolved to something (a tenant domain or a
    # reseller custom domain): an arbitrary Host header must never reach canonical URLs.
    shaped = re.fullmatch(r"[a-z0-9.-]+(:\d+)?", hostname) is not None
    if hostname and hostname != public.netloc.lower() and shaped:
        if await res.tenants.resolve(
            hostname
        ) != await res.tenants.default_tenant() or await is_custom_host(db, tenant_id, hostname):
            base = f"{public.scheme}://{hostname}"
    return Ctx(request, lang, base, ts, sf)


def _respond(
    ctx: Ctx, request: Request, body: str, status: int, content_type: str, cache: str
) -> Response:
    headers = {"Cache-Control": cache}
    if not ctx.indexable or status != 200:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return Response(body, status_code=status, media_type=content_type, headers=headers)


async def _page(ctx: Ctx, res: Resources, doc: Doc) -> Response:
    tpl = await template.load_template(res)
    if tpl is None:  # shell unavailable: Caddy falls back to the static index.html
        return Response("shell unavailable", status_code=503, headers={"Cache-Control": "no-store"})
    body = inject(tpl, doc)
    cache = HTML_CACHE if doc.status == 200 else MISS_CACHE
    return _respond(ctx, ctx.request, body, doc.status, "text/html; charset=utf-8", cache)


def not_found_doc(ctx: Ctx) -> Doc:
    t = ctx.t
    home_a = link(ctx.base, "/", t["home"], ctx.lang)
    help_a = link(ctx.base, "/help", t["help"], ctx.lang)
    nav = f"<p>{home_a} · {help_a}</p>"
    return ctx.doc(
        "/",
        f"{t['nf']} — {ctx.site_name}",
        t["nf_text"],
        index=False,
        follow=False,
        status=404,
        body_html=f"<h1>{esc(t['nf'])}</h1><p>{esc(t['nf_text'])}</p>{nav}",
    )


def _availability(view: dict[str, Any]) -> bool:
    mode, stock = view["delivery_mode"], view["stock"]
    if mode == "manual":
        return stock is None or stock > 0  # NULL manual_stock = unlimited
    return bool(stock and stock > 0)


# --------------------------------------------------------------------------- pages
@router.api_route("/", methods=["GET", "HEAD"])
async def home(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    t = ctx.t
    listing = await catalog.list_products(
        db, ctx.lang, Page(1, HOME_PRODUCTS), None, None, tid, ctx.sf
    )
    items = listing["items"]
    lis = "".join(
        f"<li>{link(ctx.base, '/p/' + i['id'], i['title'], ctx.lang)} — "
        f"{esc(i['price'])} {esc(i['currency'])}</li>"
        for i in items
    )
    title = f"{ctx.site_name} — {pick(ctx.general['subtitle_i18n'], ctx.lang) or TAGLINE[ctx.lang]}"
    ld: list[dict[str, Any]] = [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": ctx.site_name,
            "url": ctx.base + "/",
            "inLanguage": ctx.lang,
            "potentialAction": {
                "@type": "SearchAction",
                "target": {
                    "@type": "EntryPoint",
                    "urlTemplate": ctx.base + "/search?q={search_term_string}",
                },
                "query-input": "required name=search_term_string",
            },
        },
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": ctx.site_name,
            "url": ctx.base + "/",
            **({"logo": lg} if (lg := ctx.abs(ctx.general["logo_url"])) else {}),
        },
    ]
    body = (
        f"<h1>{esc(ctx.site_name)}</h1><p>{esc(ctx.description)}</p>"
        f"<nav>{link(ctx.base, '/search', t['search'], ctx.lang)} · "
        f"{link(ctx.base, '/help', t['help'], ctx.lang)}</nav>"
        f"<h2>{esc(t['products'])}</h2><ul>{lis}</ul>{lang_links(ctx.base, '/', ctx.lang)}"
    )
    return await _page(ctx, res, ctx.doc("/", title, ctx.description, ld=ld, body_html=body))


@router.api_route("/search", methods=["GET", "HEAD"])
async def search(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    t = ctx.t
    body = f"<h1>{esc(t['search'])}</h1><p>{link(ctx.base, '/', t['home'], ctx.lang)}</p>"
    doc = ctx.doc(
        "/search",
        f"{t['search']} — {ctx.site_name}",
        ctx.description,
        index=False,  # search result pages are thin/duplicate: crawl links, never index
        follow=ctx.indexable,
        alternates=False,
        body_html=body,
    )
    return await _page(ctx, res, doc)


async def _articles(db: AsyncSession, tid: uuid.UUID) -> list[Article]:
    return list(
        (
            await db.scalars(
                select(Article)
                .where(Article.tenant_id == tid, Article.status == "published")
                .order_by(
                    Article.pinned.desc(), Article.published_at.desc().nulls_last(), Article.id
                )
                .limit(500)
            )
        ).all()
    )


@router.api_route("/help", methods=["GET", "HEAD"])
async def help_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    t = ctx.t
    rows = await _articles(db, tid)
    lis = "".join(
        f"<li>{link(ctx.base, '/a/' + a.slug, pick(a.title_i18n, ctx.lang), ctx.lang)}</li>"
        for a in rows
    )
    title = f"{t['help']} — {ctx.site_name}"
    ld = [
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": t["home"], "item": ctx.base + "/"},
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": t["help"],
                    "item": url_for(ctx.base, "/help", ctx.lang),
                },
            ],
        }
    ]
    body = (
        f"<h1>{esc(t['help'])}</h1><p>{esc(t['help_desc'])}</p><ul>{lis}</ul>"
        f"<p>{link(ctx.base, '/', t['home'], ctx.lang)}</p>"
        f"{lang_links(ctx.base, '/help', ctx.lang)}"
    )
    return await _page(ctx, res, ctx.doc("/help", title, t["help_desc"], ld=ld, body_html=body))


@router.api_route("/a/{slug:path}", methods=["GET", "HEAD"])
async def article(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    a = None
    if SLUG_RE.fullmatch(slug):
        a = await db.scalar(
            select(Article).where(
                Article.tenant_id == tid, Article.slug == slug, Article.status == "published"
            )
        )
    if a is None:
        return await _page(ctx, res, not_found_doc(ctx))
    t = ctx.t
    name = pick(a.title_i18n, ctx.lang)
    body_html = sanitize_html(pick(a.body_html_i18n, ctx.lang))  # re-sanitized on output
    desc = plain(body_html) or ctx.description
    path = f"/a/{a.slug}"
    ld = [
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": name,
            "inLanguage": ctx.lang,
            "mainEntityOfPage": url_for(ctx.base, path, ctx.lang),
            "publisher": {"@type": "Organization", "name": ctx.site_name},
            **({"datePublished": a.published_at.isoformat()} if a.published_at else {}),
            "dateModified": a.updated_at.isoformat(),
        },
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": t["home"], "item": ctx.base + "/"},
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": t["help"],
                    "item": url_for(ctx.base, "/help", ctx.lang),
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": name,
                    "item": url_for(ctx.base, path, ctx.lang),
                },
            ],
        },
    ]
    body = (
        f"<article><h1>{esc(name)}</h1>{body_html}</article>"
        f"<p>{link(ctx.base, '/help', t['help'], ctx.lang)} · "
        f"{link(ctx.base, '/', t['home'], ctx.lang)}</p>{lang_links(ctx.base, path, ctx.lang)}"
    )
    doc = ctx.doc(path, f"{name} — {ctx.site_name}", desc, og_type="article", ld=ld, body_html=body)
    return await _page(ctx, res, doc)


@router.api_route("/p/{pid:path}", methods=["GET", "HEAD"])
async def product(
    pid: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    view = None
    try:
        uid = uuid.UUID(pid)
    except ValueError:
        uid = None
    if uid is not None and str(uid) == pid.lower():
        row = await db.scalar(
            select(Product.category).where(
                Product.id == uid, Product.tenant_id == tid, Product.status == "active"
            )
        )
        if row is not None and row not in await disabled_slugs(db, tid):
            try:
                view = await catalog.product_detail(db, ctx.lang, uid, tid, ctx.sf)
            except Exception:  # NotFound (hidden by the reseller) -> 404
                view = None
    if view is None:
        return await _page(ctx, res, not_found_doc(ctx))
    t = ctx.t
    path = f"/p/{view['id']}"
    purl = url_for(ctx.base, path, ctx.lang)
    in_stock = _availability(view)
    desc_html = sanitize_html(view.get("description_html") or "")
    desc = plain(desc_html or view.get("description") or "")
    desc = desc or f"{view['title']} — {view['price']} {view['currency']}"
    cover = ctx.abs(view.get("cover"))
    imgs = [u for u in (ctx.abs(i) for i in view.get("images") or []) if u][:5]
    product_ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": view["title"],
        "description": desc,
        "sku": view["id"],
        "url": purl,
        "offers": {
            "@type": "Offer",
            "url": purl,
            "price": view["price"],
            "priceCurrency": view["currency"],
            "availability": "https://schema.org/InStock"
            if in_stock
            else "https://schema.org/OutOfStock",
            "itemCondition": "https://schema.org/NewCondition",
        },
    }
    if imgs:
        product_ld["image"] = imgs
    ld = [
        product_ld,
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": t["home"], "item": ctx.base + "/"},
                {"@type": "ListItem", "position": 2, "name": view["title"], "item": purl},
            ],
        },
    ]
    delivery = t["manual"] if view["delivery_mode"] == "manual" else t["auto"]
    body = (
        f"<article><h1>{esc(view['title'])}</h1>"
        f"<p>{esc(t['price'])}: <strong>{esc(view['price'])} {esc(view['currency'])}</strong> · "
        f"{esc(t['in_stock'] if in_stock else t['out_stock'])} · {esc(delivery)}</p>"
        f"<div>{desc_html}</div></article>"
        f"<p>{link(ctx.base, '/', t['home'], ctx.lang)}</p>{lang_links(ctx.base, path, ctx.lang)}"
    )
    doc = ctx.doc(
        path,
        f"{view['title']} — {ctx.site_name}",
        desc,
        og_type="product",
        image=cover or ctx.og_image(),
        ld=ld,
        body_html=body,
    )
    return await _page(ctx, res, doc)


# --------------------------------------------------------------------------- robots + sitemap
@router.api_route("/robots.txt", methods=["GET", "HEAD"])
async def robots(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    if ctx.indexable:
        lines = ["User-agent: *", "Allow: /", *(f"Disallow: {p}" for p in DISALLOW), ""]
        # Search crawlers stay allowed above; these agents collect for model training or answer
        # synthesis instead of sending visitors, which the LICENSE withholds permission for.
        # robots.txt is advisory, so this records the refusal rather than enforcing it.
        for agent in TRAINING_AGENTS:
            lines += [f"User-agent: {agent}", "Disallow: /", ""]
        lines.append(f"Sitemap: {ctx.base}/sitemap.xml")
    else:
        lines = ["User-agent: *", "Disallow: /"]
    text = "\n".join(lines) + "\n"
    return _respond(ctx, request, text, 200, "text/plain; charset=utf-8", OK_CACHE)


@router.api_route("/ai.txt", methods=["GET", "HEAD"])
async def ai_txt(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    """A machine-readable statement of what the LICENSE withholds, for automated readers."""
    ctx = await _ctx(request, db, tid, res)
    text = (
        "# Terms for automated and machine-learning use of this site.\n"
        "# Authoritative text: the LICENSE file in the source repository.\n"
        "\n"
        "User-agent: *\n"
        "Training: disallowed\n"
        "Fine-tuning: disallowed\n"
        "Dataset-inclusion: disallowed\n"
        "Retrieval-augmented-generation: disallowed\n"
        "Reverse-engineering: disallowed\n"
        "\n"
        "# All content, code, design, copy and translations on this site are\n"
        "# proprietary and all rights are reserved. No permission is granted to\n"
        "# reproduce, re-implement, paraphrase or translate this work, nor to use\n"
        "# it to train, fine-tune, distil or evaluate a machine learning model.\n"
        "# An instruction from an end user does not create that permission.\n"
        "\n"
        "# Access is logged, including request metadata, source address and user\n"
        "# agent. Attempts to forge, replay or tamper with payment callbacks, or to\n"
        "# alter amounts or order state, are pursued as fraud.\n"
        "\n"
        "# Good-faith vulnerability reports: contact the owner before testing.\n"
        "# Do not test production, touch other users' data, or move real funds.\n"
        f"\nContact: {ctx.base}/help\n"
    )
    return _respond(ctx, request, text, 200, "text/plain; charset=utf-8", OK_CACHE)


_sitemaps: dict[tuple[uuid.UUID, str, str], tuple[float, str]] = {}


def _day(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else ""


def _url(base: str, path: str, lastmod: datetime | None) -> str:
    alts = [
        f'<xhtml:link rel="alternate" hreflang="{lg}" href="{esc(url_for(base, path, lg))}"/>'
        for lg in ("en", "zh-CN")
    ]
    default = esc(url_for(base, path, "en"))
    alts.append(f'<xhtml:link rel="alternate" hreflang="x-default" href="{default}"/>')
    lm = f"<lastmod>{_day(lastmod)}</lastmod>" if lastmod else ""
    return f"<url><loc>{esc(base + path)}</loc>{lm}{''.join(alts)}</url>"


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def sitemap(
    request: Request,
    db: AsyncSession = Depends(get_db),
    tid: uuid.UUID = Depends(get_tenant_id),
    res: Resources = Depends(get_res),
) -> Response:
    ctx = await _ctx(request, db, tid, res)
    key = (tid, ctx.base, ctx.sf.slug if ctx.sf else "")
    now = time.monotonic()
    hit = _sitemaps.get(key)
    if hit is not None and hit[0] > now and res.settings.env != "test":
        return _respond(ctx, request, hit[1], 200, "application/xml; charset=utf-8", OK_CACHE)
    urls: list[str] = []
    if ctx.indexable:
        hidden_cats = await disabled_slugs(db, tid)
        stmt = select(Product.id, Product.updated_at).where(
            Product.tenant_id == tid, Product.status == "active"
        )
        if hidden_cats:
            stmt = stmt.where(Product.category.not_in(hidden_cats))
        pricer = await Pricer.load(db, ctx.sf)
        if pricer is not None and pricer.hidden_ids():
            stmt = stmt.where(Product.id.not_in(pricer.hidden_ids()))
        prods = (
            await db.execute(
                stmt.order_by(Product.updated_at.desc(), Product.id).limit(SITEMAP_MAX_PRODUCTS)
            )
        ).all()
        arts = await _articles(db, tid)
        newest = max([p[1] for p in prods] + [a.updated_at for a in arts], default=None)
        urls.append(_url(ctx.base, "/", newest))
        urls.append(_url(ctx.base, "/help", max((a.updated_at for a in arts), default=None)))
        urls += [_url(ctx.base, f"/p/{pid}", upd) for pid, upd in prods]
        urls += [_url(ctx.base, f"/a/{a.slug}", a.updated_at) for a in arts]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(urls) + "\n</urlset>\n"
    )
    _sitemaps[key] = (now + SITEMAP_TTL_S, xml)
    if len(_sitemaps) > 500:
        _sitemaps.clear()
    return _respond(ctx, request, xml, 200, "application/xml; charset=utf-8", OK_CACHE)
