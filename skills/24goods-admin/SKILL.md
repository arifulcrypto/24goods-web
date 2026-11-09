---
name: 24goods-admin
description: Manage a 24goods / Kesiv shop through its admin API with a staff API token. Use to crawl upstream goods (SPA & AJAX platforms), extract/clean copy, batch-create or update products with sourcing information (cost & supplier URL), handle product images/logos, import card codes, or manage orders and stock. Reads KESIV_BASE_URL and KESIV_API_TOKEN from the environment.
---

# 24goods admin API

Reference: `docs/agent-api.md` (auth, scopes, all endpoints). This skill is for shop operations and batch work.

## 1. Authentication & Network Rules

- Credentials come from the environment: `KESIV_BASE_URL` (e.g. `https://shop.kesiv.com`) and `KESIV_API_TOKEN` (e.g. from `/root/.secrets/KESIV_API_TOKEN`).
- **Never print, echo, log or commit the token**. Do not run `set -x` or `curl -v`. Pass it strictly via `Authorization: Bearer $KESIV_API_TOKEN`.
- **IP Allowlist (`ADMIN_ALLOW_IPS`)**: On production edges (Caddy), `/admin` and `/api/v1/admin/*` require the client IP to be allowlisted in `/opt/kesiv/deploy/.env`. If running from external boxes without allowlisted IP, execute requests via SSH directly on the host / container or add the client IP to `ADMIN_ALLOW_IPS`.
- **Scopes**: Requires `products:read`, `products:write`, `cards:import`, `uploads`. Missing scope yields `403 token_scope`.

## 2. Upstream Crawling & Extraction Patterns

When onboarding products from upstream suppliers:

### Pattern A: Modern Vue / Next SPA Storefronts (e.g. `dujiao-next`)
- SPA web pages serve a skeleton `<div id="app"></div>` with static JS assets.
- Inspect the JS chunks or query public API endpoints directly:
  - Categories: `GET /api/v1/public/categories`
  - Products: `GET /api/v1/public/products?category_id={id}`
  - Site config: `GET /api/v1/public/config`
- Extract: `title`, `description`, `content` (HTML), `price_amount`, `skus` (multi-tier pricing), `images`, `fulfillment_type` (`auto` vs `manual`), available stock.

### Pattern B: Traditional Server-Rendered / AJAX Platforms
- Extract schema markup: `<script type="application/ld+json">` (`Product`, `price`, `availability`, `image`).
- Probe AJAX data endpoints (e.g., `POST /ajax/index/getGoodsInfo.html` with `{id, sid}`).
- Extract: unit price, delivery format (e.g. `账号----密码----辅助邮箱----国家`), stock count, after-sales terms.

## 3. Copywriting, Cleaning & Formatting Standards

To maintain brand integrity and eliminate AIGC traces:

1. **Anti-AIGC & Reseller Tone**:
   - Write like an authentic, seasoned digital merchant: direct, brief, clear, professional.
   - Avoid bloated corporate AI essays or robotic bullet lists ("尊敬的客户", "欢迎光临我们倾心打造...").
2. **Negative Filter (Strictly Exclude)**:
   - **No external links or competitor card nets**: Strip `16688.com.cn`, `catfk.com`, `yopmail.com`, etc.
   - **No external contact handles**: Strip Telegram handles (e.g. `@qiujiuyi`), WeChat/QQ IDs, group invite links. Route inquiries to the store's built-in customer service chat.
   - **No region keywords**: Strip region references (e.g. "菲区", "埃及区", "土区") from titles and descriptions to avoid buyer hesitation.
   - **No third-party redeem domains**: Strip external redemption links (e.g. `966qjy.xyz`).
3. **CommonMark & Typography Rules**:
   - **Paragraph Separation**: Distinct sections/clauses MUST be separated by `\n\n`. Single newlines (`\n`) are collapsed into spaces by CommonMark.
   - **Chinese 4-Character First-Line Indentation**: Begin each Chinese paragraph with `&emsp;&emsp;` (two full-width spaces). **NEVER use 4 ASCII spaces (`    `)**, as CommonMark converts lines starting with 4 spaces into `<pre><code>` code blocks!

## 4. Sourcing Information (进货信息 - 仅员工可见)

Staff-only sourcing data helps track supplier origins and profit margins. It is **strictly hidden from buyers**:
- `cost_minor`: Upstream procurement cost in cents (e.g., `3000` for ¥30.00, `480` for ¥4.80).
- `source_url`: Upstream supplier item URL (e.g., `https://autnine.com/products/gptgo`, `https://gmailbuy.com/home/goods.html?id=8&sid=8`).
- Admin UI automatically calculates margin (`price - cost`) and profit percentage.

## 5. Product Cover Images & Logos

- Avoid using images with watermarks, competitor branding, or dense marketing slogans.
- For brand products (ChatGPT, Google, Steam, etc.), use clean, official logos centered on a pure white square canvas (e.g. 800×800 JPEG/PNG).
- If upstream images contain watermark bars at the bottom, crop or fill the watermark area with matching background color before uploading.
- Upload image via `POST /api/v1/admin/products/{id}/images` (multipart/form-data `file=@...`).

## 6. Delivery Modes & Card Stock Management

- `delivery_mode`:
  - `auto_card`: Automated card delivery. Stock is determined by active cards in `card_stock`.
    - Import cards: `POST /api/v1/admin/products/{id}/cards/import` with `{"lines": "code1\ncode2"}`.
    - Template & separator: Set `delivery_format` (e.g. `账号----密码----辅助邮箱----账号注册地`) and `delivery_separator` (e.g. `----`).
    - Card secrets are sealed in the cardvault and never returned in plaintext.
  - `manual`: Staff manual fulfillment. Set `manual_stock` (integer, e.g. `5`; `null` = unlimited).
- `after_sales_hours`: Set warranty duration in hours (e.g. `12` for 12h first-login guarantee, `720` for 30 days).

---

## Complete Python Automation Recipe

```python
import json, os, urllib.request

BASE = os.environ.get("KESIV_BASE_URL", "https://shop.kesiv.com").rstrip("/")
TOKEN = os.environ["KESIV_API_TOKEN"]

def api(method, path, body=None):
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

# 1. Create a product with staff-only sourcing info
desc_zh = (
    "&emsp;&emsp;全新个人纯白谷歌 Gmail 邮箱 1 个，未绑定手机号，付款后系统自动发货。\n\n"
    "&emsp;&emsp;【发货格式】卡密格式：账号----密码----辅助邮箱----账号注册地。订单详情页直接提取卡密。\n\n"
    "&emsp;&emsp;【质保范围】自购买起 12 小时内仅包首次登录。人为造成的改密被风控不在售后范围。\n\n"
    "&emsp;&emsp;【使用建议】请使用干净纯净的固定住宅节点登录，切勿使用免费代理。首次登录成功后建议稳定使用 2-3 天后再改绑。"
)
desc_en = (
    "&emsp;&emsp;One fresh personal Google Gmail account. Instant automatic delivery after payment.\n\n"
    "&emsp;&emsp;[Delivery] Account----Password----Recovery Email----Region. Reveal code on order page.\n\n"
    "&emsp;&emsp;[Warranty] 12-hour warranty for first login only. Dirty IP bans excluded."
)

p = api("POST", "/admin/products", {
    "title_i18n": {
        "zh-CN": "全新个人谷歌 Gmail 邮箱（12h包首登）",
        "en": "Fresh Personal Google Gmail Account (12h First Login Guarantee)"
    },
    "description_i18n": {"zh-CN": desc_zh, "en": desc_en},
    "price": "8.00",
    "category": "accounts",
    "delivery_mode": "auto_card",
    "delivery_format": "账号----密码----辅助邮箱----账号注册地",
    "delivery_separator": "----",
    "after_sales_hours": 12,
    "cost_minor": 480,                                                # ¥4.80 staff-only cost
    "source_url": "https://gmailbuy.com/home/goods.html?id=8&sid=8",  # staff-only supplier URL
    "status": "active"
})
p_id = p["id"]
print(f"Created product: {p_id}")

# 2. Import cards for auto_card products
cards = (
    "NathansCullivan279@gmail.com----ldzvfmhrt----SusanRocha463@gmail.com--------ie\n"
    "StrieterGeng766@gmail.com----tppfusruq----kratzersimsn619@omicron.token.ro--------za"
)
imp = api("POST", f"/admin/products/{p_id}/cards/import", {
    "lines": cards,
    "note": "Initial restock"
})
print("Imported cards count:", imp["imported"])

# 3. Upload clean product image via curl
# curl -sS -H "Authorization: Bearer $KESIV_API_TOKEN" -F file=@cover.png "$KESIV_BASE_URL/api/v1/admin/products/$p_id/images"
```
