"""Site settings, announcement, articles CMS, sanitizer, product delivery fields,
recommendations and ids lookup, text-file uploads."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.markdown import render
from tests.helpers import buy, delivered_order, open_conversation, pay_order
from tests.test_chat import png_bytes


# ---------------------------------------------------------------- sanitizer
@pytest.mark.parametrize(
    ("src", "forbidden"),
    [
        ("<script>alert(1)</script>", "<script"),
        ("<img src=x onerror=alert(1)>", "<img"),
        ("[x](javascript:alert(1))", 'href="javascript'),
        ("[x](data:text/html;base64,AAA)", 'href="data'),
        ("![x](javascript:alert(1))", 'src="javascript'),
        ("![x](http://evil.test/a.png)", 'src="http'),
        ("![x](//evil.test/a.png)", 'src="//'),
        ("![x](/etc/passwd)", 'src="/etc'),
        ("<a href='http://a' onclick='x()'>t</a>", "<a "),
        ("<iframe src='https://x'></iframe>", "<iframe"),
        ("<style>*{}</style>", "<style"),
        ("<svg onload=alert(1)>", "<svg"),
        ("# Big", "<h1"),
        ("<details open ontoggle=alert(1)>", "<details"),
        ('[a](https://ok.test "t"onmouseover="x")', "<a "),
    ],
)
def test_sanitizer_blocks_xss(src, forbidden):
    out = render(src).lower()
    assert forbidden.lower() not in out


def test_sanitizer_allows_the_allowlist():
    html = render(
        "## T\n\n**b** *i* `c`\n\n- a\n- b\n\n1. x\n\n> q\n\n---\n\n"
        "[l](https://ok.test/p) [m](mailto:a@b.co)\n\n"
        "![i](https://cdn.test/a.png) ![f](/api/v1/files/12345678-1234-1234-1234-123456789012)\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```"
    )
    for tag in (
        "<h2>",
        "<strong>",
        "<em>",
        "<code>",
        "<ul>",
        "<ol>",
        "<blockquote>",
        "<hr>",
        "<table>",
        "<pre>",
        "<th>",
        "<td>",
    ):
        assert tag in html, tag
    assert 'rel="noopener noreferrer nofollow"' in html and 'target="_blank"' in html
    assert 'href="https://ok.test/p"' in html and 'href="mailto:a@b.co"' in html
    assert "cdn.test" not in html  # external images are dropped (beacon / IP leak)
    assert 'src="/api/v1/files/12345678-1234-1234-1234-123456789012"' in html


# ---------------------------------------------------------------- site + announcement
async def test_site_defaults(env):
    r = await env.client().get("/api/v1/site")
    assert r.status_code == 200
    s = r.json()
    assert s["brand"] == "Kesiv" and s["announcement"] is None and s["pages"] == []
    assert s["after_sales_default_hours"] == 672 and s["support_hours"] == ""


async def test_announcement_version_bumps_on_edit(env):
    admin, _ = await env.login("admin")
    r = await admin.get("/api/v1/admin/site")
    assert r.json()["announcement"]["version"] == 0
    body = {
        "announcement": {
            "enabled": True,
            "title_i18n": {"en": "Hello", "zh-CN": "你好"},
            "body_i18n": {"en": "**Read** <script>x</script>", "zh-CN": "请阅读"},
        },
        "after_sales_default_hours": 48,
        "auto_confirm_hours": {"manual": 10, "auto_card": 5},
        "support_hours": {"en": "9-18 CST", "zh-CN": "9-18 点"},
    }
    r = await admin.put("/api/v1/admin/site", json=body)
    assert r.status_code == 200, r.text
    a = r.json()["announcement"]
    assert a["version"] == 1 and a["enabled"] is True
    assert (
        "<strong>Read</strong>" in a["body_html_i18n"]["en"]
        and "<script" not in a["body_html_i18n"]["en"]
    )
    pub = (await env.client().get("/api/v1/site", params={"lang": "zh-CN"})).json()
    assert pub["announcement"] == {
        "version": 1,
        "title": "你好",
        "body_html": "<p>请阅读</p>\n",
        "enabled": True,
    }
    assert pub["after_sales_default_hours"] == 48 and pub["support_hours"] == "9-18 点"
    # re-saving identical content does not bump; editing the body does
    r = await admin.put("/api/v1/admin/site", json=body)
    assert r.json()["announcement"]["version"] == 1
    body["announcement"]["body_i18n"]["en"] = "changed"
    assert (await admin.put("/api/v1/admin/site", json=body)).json()["announcement"]["version"] == 2
    # disabling hides it for buyers; re-enabling bumps
    r = await admin.put("/api/v1/admin/site", json={"announcement": {"enabled": False}})
    assert r.json()["announcement"]["version"] == 2
    assert (await env.client().get("/api/v1/site")).json()["announcement"] is None
    r = await admin.put("/api/v1/admin/site", json={"announcement": {"enabled": True}})
    assert r.json()["announcement"]["version"] == 3
    assert r.json()["auto_confirm_hours"] == {"manual": 10, "auto_card": 5}
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action='site.updated'") == 5


async def test_site_admin_only_and_validated(env):
    agent, _ = await env.login("agent")
    assert (await agent.get("/api/v1/admin/site")).status_code == 403
    assert (await agent.put("/api/v1/admin/site", json={})).status_code == 403
    assert (await env.client().put("/api/v1/admin/site", json={})).status_code == 401
    admin, _ = await env.login("admin")
    assert (
        await admin.put("/api/v1/admin/site", json={"after_sales_default_hours": -1})
    ).status_code == 422
    assert (
        await admin.put("/api/v1/admin/site", json={"support_hours": {"fr": "x"}})
    ).status_code == 422


async def test_site_settings_are_per_tenant(env):
    admin, _ = await env.login("admin")
    await admin.put("/api/v1/admin/site", json={"after_sales_default_hours": 12})
    async with env.owner_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT settings FROM tenants WHERE slug='other'")) == {}
    # RLS: app_api in tenant A cannot rewrite tenant B's settings row
    async with env.api_session() as db:
        res = await db.execute(
            text("UPDATE tenants SET settings = CAST(:s AS jsonb) WHERE slug='other'"),
            {"s": '{"x": 1}'},
        )
        assert res.rowcount == 0
        await db.rollback()


# ---------------------------------------------------------------- articles
ART = {
    "slug": "how-to-redeem",
    "category": "tutorial",
    "title_i18n": {"en": "How to redeem", "zh-CN": "如何兑换"},
    "body_i18n": {"en": "## Steps\n\n1. Open", "zh-CN": "## 步骤"},
    "status": "published",
    "pinned": True,
}


async def test_articles_crud_and_public_listing(env):
    admin, _ = await env.login("admin")
    r = await admin.post("/api/v1/admin/articles", json=ART)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["published_at"] and "<h2>Steps</h2>" in a["body_html_i18n"]["en"]
    draft = (
        await admin.post(
            "/api/v1/admin/articles",
            json={**ART, "slug": "draft", "status": "draft", "pinned": False},
        )
    ).json()
    assert draft["published_at"] is None
    assert (await admin.post("/api/v1/admin/articles", json=ART)).status_code == 409  # slug taken
    assert (
        await admin.post("/api/v1/admin/articles", json={**ART, "slug": "Bad Slug"})
    ).status_code == 422
    pub = env.client()
    lst = (await pub.get("/api/v1/articles", params={"lang": "zh-CN"})).json()
    assert (
        lst["total"] == 1 and lst["items"][0]["title"] == "如何兑换" and lst["items"][0]["pinned"]
    )
    assert (await pub.get("/api/v1/articles", params={"category": "nope"})).json()["total"] == 0
    assert (await pub.get("/api/v1/articles/draft")).status_code == 404  # drafts are hidden
    one = (await pub.get("/api/v1/articles/how-to-redeem")).json()
    assert one["kind"] == "article" and one["views"] == 1 and "<ol>" in one["body_html"]
    assert (await pub.get("/api/v1/articles/how-to-redeem")).json()[
        "views"
    ] == 1  # same viewer, 10 min
    r = await admin.patch(
        f"/api/v1/admin/articles/{draft['id']}", json={"status": "published", "pinned": True}
    )
    assert r.status_code == 200 and r.json()["published_at"]
    assert (await pub.get("/api/v1/articles")).json()["total"] == 2
    assert (await admin.get("/api/v1/admin/articles", params={"status": "draft"})).json()[
        "total"
    ] == 0
    r = await admin.patch(
        f"/api/v1/admin/articles/{a['id']}",
        json={"body_i18n": {"en": "<b>x</b> [j](javascript:1)"}},
    )
    assert (
        "<script" not in r.json()["body_html_i18n"]["en"]
        and "<b>" not in r.json()["body_html_i18n"]["en"]
        and 'href="javascript' not in r.json()["body_html_i18n"]["en"]
    )
    found = (await admin.get("/api/v1/admin/articles", params={"q": "DRAFT"})).json()
    assert [i["slug"] for i in found["items"]] == ["draft"]
    assert (await admin.get("/api/v1/admin/articles", params={"q": "100%"})).json()["total"] == 0
    assert (await admin.delete(f"/api/v1/admin/articles/{draft['id']}")).status_code == 204
    assert (await admin.get(f"/api/v1/admin/articles/{draft['id']}")).status_code == 404
    assert await env.scalar("SELECT count(*) FROM audit_log WHERE action LIKE 'article.%'") == 5


async def test_pages_listed_in_site_and_not_in_articles(env):
    admin, _ = await env.login("admin")
    await admin.post(
        "/api/v1/admin/articles",
        json={
            **ART,
            "slug": "rules",
            "kind": "page",
            "category": "page",
            "title_i18n": {"en": "Rules", "zh-CN": "规则"},
        },
    )
    pub = env.client()
    assert (await pub.get("/api/v1/site")).json()["pages"] == [{"slug": "rules", "title": "Rules"}]
    assert (await pub.get("/api/v1/articles")).json()["total"] == 0
    assert (await pub.get("/api/v1/articles/rules")).json()["kind"] == "page"
    pid = (await admin.get("/api/v1/admin/articles", params={"kind": "page"})).json()["items"][0][
        "id"
    ]
    assert (await admin.delete(f"/api/v1/admin/articles/{pid}")).status_code == 422


async def test_articles_admin_only_and_cross_tenant(env):
    agent, _ = await env.login("agent")
    assert (await agent.get("/api/v1/admin/articles")).status_code == 403
    assert (await agent.post("/api/v1/admin/articles", json=ART)).status_code == 403
    admin, _ = await env.login("admin")
    other_admin, _ = await env.login("admin", tenant_id=env.other_tenant_id)
    created = (await admin.post("/api/v1/admin/articles", json=ART)).json()
    # the same slug is free in another tenant, and the other tenant sees nothing of ours
    async with env.api_session(env.other_tenant_id) as db:
        assert await db.scalar(text("SELECT count(*) FROM articles")) == 0
        res = await db.execute(
            text("UPDATE articles SET title_i18n='{}' WHERE id=:i"), {"i": created["id"]}
        )
        assert res.rowcount == 0
        res = await db.execute(text("DELETE FROM articles WHERE id=:i"), {"i": created["id"]})
        assert res.rowcount == 0
        await db.rollback()
    _ = other_admin


# ---------------------------------------------------------------- product delivery fields
PROD = {
    "title_i18n": {"en": "Gmail", "zh-CN": "谷歌邮箱"},
    "price": "5.00",
    "floor_price": "4.00",
    "delivery_mode": "auto_card",
    "category": "accounts",
    "delivery_format": "Account----Password----2FA----Notes",
    "delivery_separator": "----",
    "redeem_url": "https://mail.google.com/",
    "usage_i18n": {"en": "Log in **within 24h**. <script>x</script>", "zh-CN": "24 小时内登录"},
    "after_sales_hours": 24,
    "group_sort": 3,
}


async def test_product_delivery_fields_roundtrip(env):
    admin, _ = await env.login("admin")
    r = await admin.post("/api/v1/admin/products", json=PROD)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["delivery_format"] == PROD["delivery_format"] and p["after_sales_hours"] == 24
    assert p["usage_i18n"]["en"].startswith("Log in") and p["group_sort"] == 3
    pub = env.client()
    d = (await pub.get(f"/api/v1/products/{p['id']}")).json()
    assert d["redeem_url"] == "https://mail.google.com/" and d["delivery_separator"] == "----"
    assert "<strong>within 24h</strong>" in d["usage_html"] and "<script" not in d["usage_html"]
    assert d["after_sales_hours"] == 24
    lst = (await pub.get("/api/v1/products")).json()["items"][0]
    assert lst["after_sales_hours"] == 24 and "usage_html" not in lst
    # no override -> site default (and a tenant override flows through)
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"after_sales_hours": None})
    assert r.json()["after_sales_hours"] is None
    assert (await pub.get(f"/api/v1/products/{p['id']}")).json()["after_sales_hours"] == 672
    await admin.put("/api/v1/admin/site", json={"after_sales_default_hours": 100})
    assert (await pub.get(f"/api/v1/products/{p['id']}")).json()["after_sales_hours"] == 100
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"after_sales_hours": 0})
    assert (await pub.get(f"/api/v1/products/{p['id']}")).json()["after_sales_hours"] == 0
    r = await admin.patch(f"/api/v1/admin/products/{p['id']}", json={"usage_i18n": {"en": "new"}})
    assert (await pub.get(f"/api/v1/products/{p['id']}", params={"lang": "en"})).json()[
        "usage_html"
    ] == "<p>new</p>\n"


@pytest.mark.parametrize(
    "url",
    [
        "http://insecure.test/",
        "javascript:alert(1)",
        "//evil.test/x",
        "https://u:p@evil.test/",
        "https://" + "a" * 500,
        "ftp://x",
        "https://a b.test",
        "data:text/html,x",
    ],
)
async def test_redeem_url_validation(env, url):
    admin, _ = await env.login("admin")
    r = await admin.post("/api/v1/admin/products", json={**PROD, "redeem_url": url})
    assert r.status_code == 422, url


async def test_redeem_url_relative_ok_and_bad_separator(env):
    admin, _ = await env.login("admin")
    assert (
        await admin.post("/api/v1/admin/products", json={**PROD, "redeem_url": "/help/redeem"})
    ).status_code == 200
    assert (
        await admin.post("/api/v1/admin/products", json={**PROD, "delivery_separator": ";;"})
    ).status_code == 422
    assert (
        await admin.post("/api/v1/admin/products", json={**PROD, "delivery_separator": "\t"})
    ).status_code == 200
    assert (
        await admin.post("/api/v1/admin/products", json={**PROD, "after_sales_hours": 9999})
    ).status_code == 422


async def test_order_delivery_info(env):
    admin, _ = await env.login("admin")
    p = (await admin.post("/api/v1/admin/products", json=PROD)).json()
    pm = await env.product(mode="auto_card")
    _ = pm
    buyer, _ = await env.login("buyer")
    r = await buyer.post("/api/v1/orders", json={"product_id": p["id"], "qty": 1})
    # out of stock first
    assert r.status_code == 409
    from app.models import Product

    async with env.api_session() as db:
        prod = await db.get(Product, __import__("uuid").UUID(p["id"]))
    await env.import_codes(prod, ["g@x.com----pw----2fa----note"])
    order = await buy(buyer, prod)
    info = order["delivery_info"]
    assert info["format_labels"] == ["Account", "Password", "2FA", "Notes"]
    assert info["separator"] == "----" and info["redeem_url"] == "https://mail.google.com/"
    assert "<strong>within 24h</strong>" in info["usage_html"]
    assert order["after_sales_until"] is None and order["after_sales"] is None


# ---------------------------------------------------------------- ids + recommendations
async def test_products_by_ids_keeps_order_and_skips_unknown(env):
    a = await env.product(title="A")
    b = await env.product(title="B", mode="manual")
    draft = await env.product(title="D", status="draft")
    c = env.client()
    ids = f"{b.id},{a.id},{draft.id},00000000-0000-0000-0000-000000000000"
    items = (await c.get("/api/v1/products", params={"ids": ids})).json()["items"]
    assert [i["title"] for i in items] == ["B", "A"]
    assert "after_sales_hours" in items[0] and "cover" in items[0]
    assert (await c.get("/api/v1/products", params={"ids": "not-a-uuid"})).status_code == 422
    too_many = ",".join(str(__import__("uuid").uuid4()) for _ in range(21))
    assert (await c.get("/api/v1/products", params={"ids": too_many})).status_code == 422
    assert (await c.get("/api/v1/products", params={"ids": ""})).json() == {"items": []}


async def test_recommended_scoring_exclusions_and_cache(env):
    ctx = await env.product(title="Ctx", category="gift_cards", mode="manual")
    same = await env.product(title="SameCat", category="gift_cards", mode="manual")
    other = await env.product(title="Other", category="game_topup", mode="manual")
    third = await env.product(title="Third", category="game_topup", mode="manual")
    _ = third
    empty = await env.product(title="NoStock", category="gift_cards", mode="auto_card")
    draft = await env.product(title="Draft", category="gift_cards", mode="manual", status="draft")
    _ = (empty, draft)
    c = env.client()
    r = await c.get("/api/v1/products/recommended", params={"for": str(ctx.id), "limit": 8})
    assert r.status_code == 200
    titles = [i["title"] for i in r.json()["items"]]
    assert titles[0] == "SameCat" and "Other" in titles
    assert "Ctx" not in titles and "NoStock" not in titles and "Draft" not in titles
    # co-purchase beats everything else: someone bought ctx and `other`
    _buyer, bu = await env.login("buyer")
    async with env.owner_session() as db:
        await db.execute(
            text(
                "INSERT INTO orders (tenant_id, order_no, buyer_id, product_id, title, qty, "
                "amount_minor, status, delivery_mode, paid_at) "
                "SELECT :t, 'GCOPURCHASE' || x, :b, p, 'x', 1, 100, 'delivered', 'manual', now() "
                "FROM unnest(CAST(:ps AS uuid[])) WITH ORDINALITY AS u(p, x)"
            ),
            {"t": env.tenant_id, "b": bu.id, "ps": [ctx.id, other.id]},
        )
        await db.commit()
    await env.s.redis.flushdb()
    items = (await c.get("/api/v1/products/recommended", params={"for": str(ctx.id)})).json()[
        "items"
    ]
    titles = [i["title"] for i in items]
    assert titles.index("Other") < titles.index("Third")  # co-purchase lifts "Other" above "Third"
    assert titles[0] == "SameCat"  # 3 * same-category still leads
    # `recent` acts as context too; cached for 5 minutes
    assert await env.s.redis.keys("rec:*")
    keys = await env.s.redis.keys("rec:*")
    ttl = await env.s.redis.ttl(keys[0])
    assert 0 < ttl <= 300
    r = await c.get(
        "/api/v1/products/recommended", params={"recent": f"{ctx.id},{same.id}", "limit": 1}
    )
    assert len(r.json()["items"]) == 1
    assert (await c.get("/api/v1/products/recommended", params={"limit": 99})).status_code == 422
    assert (await c.get("/api/v1/products/recommended", params={"for": "x"})).status_code == 422


# ---------------------------------------------------------------- text-file uploads + chat
async def test_text_file_upload_and_file_message(env):
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    body = ("hello 你好\nline two " * 40).encode()
    r = await buyer.post(
        "/api/v1/uploads",
        files={"file": ("../../etc/pass wd<b>.TXT", body, "text/html")},
        data={"kind": "file"},
    )
    assert r.status_code == 200, r.text
    att = r.json()
    assert att["kind"] == "file" and att["mime"] == "text/plain" and att["bytes"] == len(body)
    assert att["name"] == "pass wd_b_.txt"
    m = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "f-1", "type": "file", "attachment_id": att["id"]},
    )
    assert m.status_code == 200, m.text
    msg = m.json()
    assert msg["type"] == "file" and msg["text"] is None
    assert msg["payload"]["name"] == "pass wd_b_.txt" and msg["payload"]["bytes"] == len(body)
    assert (
        msg["payload"]["preview"] == body.decode()[:160] and len(msg["payload"]["preview"]) == 160
    )
    assert msg["attachment"]["id"] == att["id"]
    # served as an attachment, never rendered
    f = await buyer.get(att["url"])
    h = f.headers
    assert f.status_code == 200 and f.content == body
    assert h["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert h["x-content-type-options"] == "nosniff" and h["content-security-policy"] == "sandbox"
    assert h["content-type"].startswith("text/plain")
    assert (await staff.get(att["url"])).status_code == 200
    assert (await env.client().get(att["url"])).status_code == 401
    stranger, _ = await env.login("buyer")
    assert (await stranger.get(att["url"])).status_code == 404
    # staff can send files too
    up = await staff.post(
        "/api/v1/uploads", files={"file": ("n.txt", b"ok", "text/plain")}, data={"kind": "file"}
    )
    m = await staff.post(
        f"/api/v1/admin/conversations/{conv['id']}/messages",
        json={"client_msg_id": "f-2", "type": "file", "attachment_id": up.json()["id"]},
    )
    assert m.status_code == 200 and m.json()["payload"]["preview"] == "ok"


@pytest.mark.parametrize(
    ("data", "name"),
    [
        (b"", "a.txt"),
        (b"abc\x00def", "a.txt"),
        (b"\xff\xfe\xfa", "a.txt"),
        (b"x" * (256 * 1024 + 1), "a.txt"),
        (png_bytes(), "a.txt"),
    ],
)
async def test_text_upload_rejections(env, data, name):
    buyer, _ = await env.login("buyer")
    r = await buyer.post(
        "/api/v1/uploads", files={"file": (name, data, "text/plain")}, data={"kind": "file"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "upload_rejected"


async def test_file_exactly_at_limit_ok_and_image_cannot_be_sent_as_file(env):
    buyer, _ = await env.login("buyer")
    conv = await open_conversation(buyer)
    r = await buyer.post(
        "/api/v1/uploads",
        files={"file": ("a.txt", b"x" * (256 * 1024), "text/plain")},
        data={"kind": "file"},
    )
    assert r.status_code == 200
    img = (
        await buyer.post(
            "/api/v1/uploads",
            files={"file": ("a.png", png_bytes(), "image/png")},
            data={"kind": "image"},
        )
    ).json()
    m = await buyer.post(
        f"/api/v1/conversations/{conv['id']}/messages",
        json={"client_msg_id": "f-3", "type": "file", "attachment_id": img["id"]},
    )
    assert m.status_code == 404


async def test_manual_delivery_system_message_carries_delivered_at_and_order_id(env):
    p = await env.product(mode="manual")
    buyer, _ = await env.login("buyer")
    staff, _ = await env.login("agent")
    conv = await open_conversation(buyer)
    order = await delivered_order(env, buyer, p, staff=staff)
    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    ev = [m for m in msgs if m["type"] == "system" and m["payload"]["event"] == "delivered"]
    assert len(ev) == 1
    assert ev[0]["payload"]["order_id"] == order["id"]
    assert ev[0]["payload"]["delivered_at"] == order["delivered_at"]


async def test_auto_card_delivered_message_carries_order_id(env):
    p = await env.product(mode="auto_card")
    await env.import_codes(p, ["Z-1"])
    buyer, _ = await env.login("buyer")
    conv = await open_conversation(buyer)
    order = await delivered_order(env, buyer, p)
    msgs = (await buyer.get(f"/api/v1/conversations/{conv['id']}/messages")).json()
    ev = [m for m in msgs if m["type"] == "system" and m["payload"]["event"] == "delivered"]
    assert ev[0]["payload"]["order_id"] == order["id"]
    _ = pay_order
