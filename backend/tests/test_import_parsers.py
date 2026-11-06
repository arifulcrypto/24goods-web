"""Importer parsers, fixtures captured 2026-11-06 (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.settings_v5 import section
from app.modules.catalog import importer
from app.modules.catalog.importer import dom, extract, sites
from app.modules.catalog.importer.price import find_price, suggest_price, to_minor

FIX = Path(__file__).parent / "fixtures" / "import"
CFG = section({}, "import")  # percent 200 permille, round to 10


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_gmailbuy_fixture():
    url = "https://gmailbuy.com/home/goods.html?id=8&sid=8"
    d = importer.parse_pasted(text=None, html=fx("gmailbuy.html"), base_url=url, cfg=CFG)
    assert d["title"] == "【优质】全新个人谷歌Gmail邮箱(首登不弹二维码验证，推荐)"
    assert (d["price_minor"], d["currency"]) == (480, "CNY")
    assert d["suggested_price_minor"] == 580  # 4.80 * 1.2 = 5.76 -> up to 5.80
    assert d["site"] == "gmailbuy.com" and d["source_url"] == url
    urls = [i["url"] for i in d["images"]]
    assert urls[0] == "https://gmailbuy.com/images/202208270632159720586.jpg"
    assert "https://gmailbuy.com/images/goods/8f5e7c4dfe6d4dd7a39aef6603f96312.png" in urls
    md = d["description_md"]
    assert md.startswith("### ") and "格式：账号-密码-2Fa-国家" in md and "**产品售后：**" in md
    assert "立即购买" not in md and "<" not in md  # nav / buttons / tags dropped
    assert d["warnings"] == []


def test_maitg_fixture():
    url = "https://maitg.com/item?id=164"
    d = importer.parse_pasted(text=None, html=fx("maitg.html"), base_url=url, cfg=CFG)
    assert d["title"].startswith("Telegram飞机号/电报 | 880孟加拉国")
    assert d["price_minor"] == 320  # "from" price of the cheapest SKU, not the related items
    assert d["suggested_price_minor"] == 390
    assert d["images"][0]["url"] == "https://flagcdn.com/120x90/bd.webp"
    assert any(i["url"].startswith("https://sc04.alicdn.com/") for i in d["images"])
    assert "[查看登录教程 →](https://maitg.com/how-to-login-tg)" in d["description_md"]
    assert "**设置 → 隐私与安全**" in d["description_md"]


def test_og_image_concatenation_repaired():
    assert (
        dom.clean_url("https://maitg.comhttps://flagcdn.com/a.webp", "https://maitg.com/")
        == "https://flagcdn.com/a.webp"
    )
    assert dom.clean_url("/x.png", "https://a.test/p/q") == "https://a.test/x.png"
    assert dom.clean_url("data:image/png;base64,AAA", None) is None
    assert dom.clean_url("javascript:alert(1)", "https://a.test/") is None


def test_ka8_api_fixture():
    assert (
        sites.ka8_api_url("https://go.ka8.ai/products/ChatGPT-Plus")
        == "https://go.ka8.ai/api/v1/public/products/ChatGPT-Plus"
    )
    assert sites.ka8_api_url("https://go.ka8.ai/blog/x") is None
    assert sites.ka8_api_url("https://evil.test/products/x") is None
    p = sites.ka8_from_json(fx("ka8_api.json"), "https://go.ka8.ai/api/v1/public/products/x")
    assert p is not None
    assert p.title == "ChatGPT-Plus官方充值-自助卡密充值-菲区"
    assert p.price_minor == 10800
    assert "- **菲律宾官方代充，正规资金，正规卡代付**" in p.description_md
    assert all(i["url"].startswith("https://go.ka8.ai/uploads/") for i in p.images)
    assert sites.ka8_from_json("<html>blocked</html>", "https://go.ka8.ai/") is None


def test_ka8_spa_shell_gets_og_only_and_no_price():
    d = importer.parse_pasted(
        text=None,
        html=fx("ka8_shell.html"),
        base_url="https://go.ka8.ai/products/ChatGPT-Plus",
        cfg=CFG,
    )
    assert d["title"].startswith("ChatGPT-Plus官方充值")
    assert d["price_minor"] is None and "no_price" in d["warnings"]


def test_json_ld_product():
    html = """<html><head><title>Shop</title><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"Product","name":"Netflix 1M",
    "description":"Premium plan","image":["https://cdn.test/a.jpg"],
    "offers":{"@type":"Offer","price":"12.50","priceCurrency":"USD"}}]}
    </script></head><body><h1>ignored by ld</h1></body></html>"""
    d = importer.parse_pasted(text=None, html=html, base_url="https://s.test/p", cfg=CFG)
    assert d["title"] == "Netflix 1M"
    assert (d["price_minor"], d["currency"]) == (1250, "USD")
    assert d["images"] == [{"url": "https://cdn.test/a.jpg", "alt": "Netflix 1M"}]
    assert d["description_md"] == "Premium plan"


def test_next_data_and_opengraph():
    html = """<html><head><meta property="og:title" content="OG Title - Site">
    <meta property="og:image" content="/img/og.png"></head><body>
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"product":{"title":"Next Product","price":"9.9","description":"Hello"}}}}
    </script></body></html>"""
    d = importer.parse_pasted(text=None, html=html, base_url="https://s.test/", cfg=CFG)
    assert d["title"] == "Next Product" and d["price_minor"] == 990
    assert d["images"][0]["url"] == "https://s.test/img/og.png"


def test_heuristics_h1_price_regex_and_dropped_tags():
    html = """<body><nav><a href="/">Home</a></nav><h1>Steam Wallet $20</h1>
    <div class="box">价格：￥ 145.50</div>
    <div class="description"><p>Fast <b>delivery</b>.<br>Read <a href="/faq">FAQ</a></p>
    <ul><li>one</li><li>two</li></ul><script>evil()</script><style>x{}</style>
    <form><input name=q></form></div><footer>copyright</footer></body>"""
    d = importer.parse_pasted(text=None, html=html, base_url="https://s.test/item", cfg=CFG)
    assert d["title"] == "Steam Wallet $20"
    assert (d["price_minor"], d["currency"]) == (14550, "CNY")
    md = d["description_md"]
    assert "Fast **delivery**." in md and "[FAQ](https://s.test/faq)" in md
    assert "- one\n- two" in md
    for bad in ("evil", "copyright", "Home", "<"):
        assert bad not in md


def test_markdown_converter_inline_and_images():
    root = dom.parse_dom(
        "<h2>Title</h2><p>a <strong> b </strong> <em>c</em></p>"
        '<img src="/x.png" alt="pic"><img src="data:image/png;base64,AA">'
        '<a href="javascript:x()">bad</a><ol><li>x</li><li>y</li></ol>'
    )
    md, imgs = dom.to_markdown(root, "https://s.test/")
    assert "## Title" in md and "a **b** *c*" in md and "![pic](https://s.test/x.png)" in md
    assert "javascript" not in md and "1. x\n2. y" in md
    assert imgs == [{"url": "https://s.test/x.png", "alt": "pic"}]


def test_deeply_nested_html_does_not_blow_up():
    html = "<div>" * 5000 + "deep" + "</div>" * 5000
    md, _ = dom.to_markdown(dom.parse_dom(html), None)
    assert "deep" in md


def test_plain_text_parser():
    t = "## ChatGPT Plus 1 month\n\n价格：¥ 108\n\nShared by email.\nInstant delivery."
    d = importer.parse_pasted(text=t, html=None, base_url=None, cfg=CFG)
    assert d["title"] == "ChatGPT Plus 1 month"
    assert d["price_minor"] == 10800
    assert d["description_md"] == "价格：¥ 108\n\nShared by email.\nInstant delivery."
    p = extract.parse_text("¥ 5.00\nCheap thing\nbody")
    assert p.title == "Cheap thing" and p.price_minor == 500


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¥4.80", (480, "CNY")),
        ("￥ 12", (1200, "CNY")),
        ("$ 3.5", (350, "USD")),
        ("售价: 20.00", (2000, "CNY")),
        ("35元", (3500, "CNY")),
        ("Price: $1,299.99", (129999, "USD")),
        ("no price here", None),
        ("¥0", None),
    ],
)
def test_price_regex(text, expected):
    assert find_price(text) == expected


def test_to_minor_rejects_garbage():
    assert to_minor("abc") is None and to_minor("-1") is None and to_minor("1e30") is None
    assert to_minor("NaN") is None and to_minor("4.8") == 480


@pytest.mark.parametrize(
    ("cost", "cfg", "expected"),
    [
        (480, {"markup_kind": "percent", "markup_value": 200, "round_to_minor": 10}, 580),
        (1000, {"markup_kind": "percent", "markup_value": 200, "round_to_minor": 10}, 1200),
        (1001, {"markup_kind": "percent", "markup_value": 200, "round_to_minor": 10}, 1210),
        (333, {"markup_kind": "percent", "markup_value": 333, "round_to_minor": 1}, 444),
        (480, {"markup_kind": "fixed", "markup_value": 101, "round_to_minor": 100}, 600),
        (480, {"markup_kind": "fixed", "markup_value": 120, "round_to_minor": 100}, 600),
        (500, {"markup_kind": "fixed", "markup_value": 0, "round_to_minor": 100}, 500),
        (None, {"markup_kind": "fixed", "markup_value": 0, "round_to_minor": 1}, None),
    ],
)
def test_markup_rounds_up(cost, cfg, expected):
    assert suggest_price(cost, cfg) == expected
