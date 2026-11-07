"""review-v5.2 §6: crawler/first-paint HTML, sitemap and robots rendered by the API."""

from __future__ import annotations

import json
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from app.core import globallimit
from app.models import Article, CustomDomain
from app.modules.seo import router as seo_router
from app.modules.seo import template
from tests.test_commerce_v4 import set_settings
from tests.test_commerce_v5 import make_reseller

SHELL = (
    '<!doctype html><html lang="en"><head><meta charset="UTF-8" /><title>Old</title>'
    '<meta name="description" content="old" /><meta property="og:title" content="old" />'
    '<meta name="robots" content="index" /></head>'
    '<body><div id="app"></div><script type="module" src="/assets/x.js"></script></body></html>'
)
LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@pytest.fixture(autouse=True)
def shell(monkeypatch):
    async def load(_res):
        return SHELL

    monkeypatch.setattr(template, "load_template", load)
    seo_router._sitemaps.clear()


def lds(html: str) -> list[dict]:
    return [json.loads(m.replace("\\u003c", "<")) for m in LD.findall(html)]


async def article(env, slug="terms", status="published", tenant_id=None, title="Terms"):
    tid = tenant_id or env.tenant_id
    async with env.owner_session(tid) as db:
        db.add(
            Article(
                tenant_id=tid,
                slug=slug,
                kind="page",
                title_i18n={"en": title, "zh-CN": "条款"},
                body_i18n={"en": "x"},
                body_html_i18n={"en": "<p>Be <strong>nice</strong>.</p>", "zh-CN": "<p>请友好</p>"},
                status=status,
                published_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def test_product_page_meta_and_jsonld(env):
    p = await env.product(price=1990, title="Steam Wallet", desc="Instant **code**", mode="manual")
    r = await env.client().get(f"/p/{p.id}")
    assert r.status_code == 200
    h = r.text
    assert "<title>Steam Wallet — Kesiv</title>" in h
    assert "Old</title>" not in h and 'content="old"' not in h  # template metadata replaced
    assert 'content="index,follow"' in h
    assert f'<link rel="canonical" href="http://testserver/p/{p.id}" />' in h
    assert f'hreflang="zh-CN" href="http://testserver/p/{p.id}?lang=zh-CN"' in h
    assert f'hreflang="x-default" href="http://testserver/p/{p.id}"' in h
    assert 'property="og:title" content="Steam Wallet — Kesiv"' in h
    assert 'name="twitter:card"' in h
    assert "<h1>Steam Wallet</h1>" in h and "19.90 CNY" in h  # pre-rendered body
    assert r.headers["cache-control"] == "public, max-age=0, s-maxage=300, must-revalidate"
    product, crumbs = lds(h)
    offer = product["offers"]
    assert product["@type"] == "Product" and product["name"] == "Steam Wallet"
    assert offer["price"] == "19.90" and offer["priceCurrency"] == "CNY"
    assert offer["availability"] == "https://schema.org/InStock"  # manual, NULL stock = unlimited
    assert crumbs["@type"] == "BreadcrumbList"
    # zh-CN: own canonical, localized title
    z = await env.client().get(f"/p/{p.id}", params={"lang": "zh-CN"})
    assert "Steam 钱包 ¥20" in z.text
    assert f'rel="canonical" href="http://testserver/p/{p.id}?lang=zh-CN"' in z.text
    assert '<html lang="zh-CN"' in z.text


async def test_availability_manual_stock_and_auto_cards(env):
    manual = await env.product(mode="manual")
    async with env.owner_session() as db:
        m = await db.get(type(manual), manual.id)
        m.manual_stock = 0
        await db.commit()
    c = env.client()
    h = (await c.get(f"/p/{manual.id}")).text
    assert lds(h)[0]["offers"]["availability"] == "https://schema.org/OutOfStock"
    auto = await env.product(mode="auto_card")
    h = (await c.get(f"/p/{auto.id}")).text
    assert lds(h)[0]["offers"]["availability"] == "https://schema.org/OutOfStock"  # no cards
    await env.import_codes(auto, ["AAAA-1111-2222"])
    h = (await c.get(f"/p/{auto.id}")).text
    assert lds(h)[0]["offers"]["availability"] == "https://schema.org/InStock"
    assert "AAAA-1111-2222" not in h


async def test_hostile_title_is_escaped(env):
    evil = '</script><script>alert(1)</script>"><img src=x onerror=alert(2)>'
    p = await env.product(title=evil, desc=evil)
    r = await env.client().get(f"/p/{p.id}")
    h = r.text
    assert "</script><script>alert" not in h
    assert "<script>alert" not in h and "<img src=x" not in h
    assert h.count("</script>") == 3  # 2 ld+json + the shell's module script
    assert lds(h)[0]["name"] == evil  # JSON-LD round-trips the raw value
    z = await env.client().get("/", params={"lang": "en"})
    assert "<script>alert" not in z.text


async def test_unknown_hidden_and_invalid_products_are_404_noindex(env):
    c = env.client()
    hidden = await env.product(status="archived")
    for pid in (uuid.uuid4(), hidden.id, "not-a-uuid", "x/y"):
        r = await c.get(f"/p/{pid}")
        assert r.status_code == 404, pid
        assert 'content="noindex,nofollow"' in r.text
        assert "canonical" not in r.text and "application/ld+json" not in r.text
        assert r.headers["x-robots-tag"] == "noindex, nofollow"
        assert 'id="app"' in r.text  # SPA shell stays, the SPA renders its 404 page
    assert (await c.get("/a/nope")).status_code == 404
    await article(env, "draft-one", status="draft")
    assert (await c.get("/a/draft-one")).status_code == 404
    assert (await c.get("/a/Bad_Slug!")).status_code == 404


async def test_home_help_article_search(env):
    await env.product(title="Netflix Gift")
    await article(env, "terms")
    c = env.client()
    h = (await c.get("/")).text
    assert "Netflix Gift" in h and "<h1>Kesiv</h1>" in h
    types = [x["@type"] for x in lds(h)]
    assert types == ["WebSite", "Organization"]
    assert lds(h)[0]["potentialAction"]["@type"] == "SearchAction"
    assert 'rel="canonical" href="http://testserver/"' in h
    h = (await c.get("/help")).text
    assert 'href="http://testserver/a/terms"' in h
    a = await c.get("/a/terms")
    assert a.status_code == 200 and "<strong>nice</strong>" in a.text
    assert "<title>Terms — Kesiv</title>" in a.text
    assert lds(a.text)[0]["@type"] == "Article"
    s = await c.get("/search", params={"q": "x"})
    assert s.status_code == 200 and 'content="noindex,follow"' in s.text


async def test_sitemap_lists_published_only_and_is_valid_xml(env):
    live = await env.product(title="Live")
    await env.product(title="Gone", status="archived")
    other = await env.product(title="Other", tenant_id=env.other_tenant_id)
    await article(env, "terms")
    await article(env, "wip", status="draft")
    r = await env.client().get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert r.headers["cache-control"] == "public, max-age=300"
    root = ET.fromstring(r.text)  # noqa: S314 - our own output; valid XML
    locs = [e.text for e in root.findall("s:url/s:loc", NS)]
    assert f"http://testserver/p/{live.id}" in locs
    assert "http://testserver/a/terms" in locs
    assert "http://testserver/" in locs and "http://testserver/help" in locs
    assert not any("wip" in x or str(other.id) in x for x in locs)
    assert len(locs) == 4  # home, help, 1 product, 1 article (no archived / draft / foreign)
    first = root.find("s:url", NS)
    assert first.find("s:lastmod", NS) is not None
    alts = r.text.count('hreflang="zh-CN"')
    assert alts == 4


async def test_robots_txt_and_indexing_toggle(env):
    p = await env.product()
    c = env.client()
    r = await c.get("/robots.txt")
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert "Allow: /" in lines and "Sitemap: http://testserver/sitemap.xml" in lines
    for path in ("/admin", "/api", "/cart", "/orders", "/pay", "/me", "/messages", "/reseller"):
        assert f"Disallow: {path}" in lines
    assert "Disallow: /chat" in lines and "Disallow: /lookup" in lines
    # indexing off: noindex everywhere, robots = Disallow: /, empty sitemap
    await set_settings(env, {"seo": {"indexing_enabled": False}})
    r = await c.get("/robots.txt")
    assert r.text == "User-agent: *\nDisallow: /\n"
    for url in ("/", f"/p/{p.id}", "/help"):
        r = await c.get(url)
        assert 'content="noindex,nofollow"' in r.text
        assert r.headers["x-robots-tag"] == "noindex, nofollow"
    assert "<url>" not in (await c.get("/sitemap.xml")).text


async def test_white_label_storefront_noindex_unless_enabled(env):
    p = await env.product()
    owner = await env.user("buyer")
    await make_reseller(env, owner, slug="shopa")
    await set_settings(env, {"features": {"resellers": True}})
    c = env.client()
    q = {"store": "shopa"}
    r = await c.get(f"/p/{p.id}", params=q)
    assert r.status_code == 200 and "Store shopa" in r.text
    assert 'content="noindex,nofollow"' in r.text
    # the canonical stays on the platform host and never carries ?store=
    assert f'rel="canonical" href="http://testserver/p/{p.id}"' in r.text
    assert (await c.get("/robots.txt", params=q)).text == "User-agent: *\nDisallow: /\n"
    assert "<url>" not in (await c.get("/sitemap.xml", params=q)).text
    # main store unaffected
    assert 'content="index,follow"' in (await c.get(f"/p/{p.id}")).text
    await set_settings(env, {"seo": {"index_storefronts": True}})
    r = await c.get(f"/p/{p.id}", params=q)
    assert 'content="index,follow"' in r.text


async def test_x_storefront_header_is_ignored(env):
    """CDN cache poisoning: the header is not in the cache key, so it must not change output."""
    p = await env.product()
    owner = await env.user("buyer")
    await make_reseller(env, owner, slug="shopa")
    await set_settings(env, {"features": {"resellers": True}})
    c = env.client()
    plain_ = await c.get(f"/p/{p.id}")
    hdr = await c.get(f"/p/{p.id}", headers={"X-Storefront": "shopa"})
    assert plain_.text == hdr.text and "Store shopa" not in hdr.text
    assert (await c.get("/robots.txt", headers={"X-Storefront": "shopa"})).text.startswith(
        "User-agent: *\nAllow: /"
    )


async def test_stored_html_is_resanitized(env):
    p = await env.product()
    evil = '<p>ok</p><script>alert(1)</script><img src="https://evil.test/x.png" onerror="x()">'
    async with env.owner_session() as db:
        row = await db.get(type(p), p.id)
        row.description_html_i18n = {"en": evil}
        await db.commit()
    await article(env, "bad")
    async with env.owner_session() as db:
        from sqlalchemy import update

        await db.execute(update(Article).values(body_html_i18n={"en": evil}))
        await db.commit()
    c = env.client()
    for url in (f"/p/{p.id}", "/a/bad"):
        h = (await c.get(url)).text
        assert "<p>ok</p>" in h
        assert "<script>alert" not in h and "onerror" not in h and "evil.test" not in h


async def test_cross_tenant_product_not_rendered_on_other_host(env):
    mine = await env.product(title="Mine")
    theirs = await env.product(title="Theirs", tenant_id=env.other_tenant_id)
    async with env.owner_session(env.other_tenant_id) as db:
        db.add(
            CustomDomain(
                tenant_id=env.other_tenant_id,
                hostname="shop.reseller.test",
                verify_token="t",
                status="active",
                verified_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    other = env.client("shop.reseller.test")
    assert (await other.get(f"/p/{mine.id}")).status_code == 404
    r = await other.get(f"/p/{theirs.id}")
    assert r.status_code == 200 and "Theirs" in r.text
    assert f'href="http://shop.reseller.test/p/{theirs.id}"' in r.text  # own host in canonicals
    assert (await env.client().get(f"/p/{theirs.id}")).status_code == 404
    sm = (await other.get("/sitemap.xml")).text
    assert str(theirs.id) in sm and str(mine.id) not in sm


async def test_unknown_host_never_reaches_canonical(env):
    p = await env.product()
    r = await env.client("evil.example").get(f"/p/{p.id}")
    assert "evil.example" not in r.text and "http://testserver/p/" in r.text


async def test_seo_settings_roundtrip_and_validation(env):
    admin, _ = await env.login("admin")
    r = await admin.put(
        "/api/v1/admin/site",
        json={
            "seo": {
                "default_description_i18n": {"en": "Custom <b>desc</b>", "zh-CN": "自定义"},
                "og_image": "https://cdn.example.com/og.png",
            }
        },
    )
    assert r.status_code == 200 and r.json()["seo"]["indexing_enabled"] is True
    for bad in (
        "javascript:alert(1)",
        "data:text/html,x",
        "//evil.com/x.png",
        "/\\evil.com",
        'https://x/"onerror=',
    ):
        r = await admin.put("/api/v1/admin/site", json={"seo": {"og_image": bad}})
        assert r.status_code == 422, bad
    h = (await env.client().get("/")).text
    assert 'content="Custom &lt;b&gt;desc&lt;/b&gt;"' in h
    assert 'property="og:image" content="https://cdn.example.com/og.png"' in h


async def test_seo_paths_are_covered_by_the_global_limiter(env, monkeypatch):
    monkeypatch.setattr(globallimit, "CFG_TTL_S", 0.0)
    assert not globallimit.exempt("/") and not globallimit.exempt("/p/x")
    assert not globallimit.exempt("/sitemap.xml") and not globallimit.exempt("/robots.txt")
    assert globallimit.exempt("/assets/a.js") and globallimit.exempt("/healthz")
    await set_settings(env, {"rate_limit": {"enabled": True, "limit": 10, "window_s": 60}})
    await env.res.redis.delete(globallimit.cfg_key(env.tenant_id))
    c = env.client()
    codes = [(await c.get("/robots.txt")).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


async def test_template_unavailable_is_503(env, monkeypatch):
    async def none(_res):
        return None

    monkeypatch.setattr(template, "load_template", none)
    assert (await env.client().get("/")).status_code == 503
