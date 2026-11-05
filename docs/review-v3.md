# Review v3: storefront UX, content, guest checkout, after-sales, card management

Status: **approved for build (MVP-0.5)**, 2026-11-05. Input:
- the owner's click-through feedback on the MVP-0 demo;
- a study of gmailbuy.com (home, product, article, card lookup pages) and jiegan.xyz / web3chirou.com.
  - daitu.cc renders client-side only.
  - haoyue.ai returned 403 to automated fetches.

Everything here still runs on **mock data only**. The AGENTS.md §1 invariants are unchanged.

## 0. What the reference shops do well (and what we copy)

| Observed | Where | We adopt |
|---|---|---|
| A plain **table/list** of products grouped by category: title · price · stock · Buy. Titles carry tags such as 【优质】 or 【2fa】. | gmailbuy, web3chirou | **List view by default** with small 56 px thumbnails, grouped by category. The grid becomes an optional toggle. |
| Notice block on the home page plus a "read before buying" link | all three | Dismissible **announcement modal** (once per version), plus a collapsible notice bar on Home |
| Header items: 卡密查询 / 订单查询, 教程, 在线客服, 会员中心 | all | Header and Me entries: **Order lookup**, **Help & tutorials**, **Support** |
| Delivery format written per product (`账号-密码-2Fa-国家`), plus usage tips and the after-sales window | gmailbuy | Structured `delivery_format`, `redeem_url`, `usage` and after-sales window **per product**, rendered next to the delivered codes |
| Articles: title · date · category · read count; "published / updated / read N times" | gmailbuy | **Articles CMS** (admin-authored, markdown, sanitized server-side), plus fixed pages (rules, terms, privacy, after-sales, help) |
| Two-tier lookup: the full order number shows delivery content; the contact shows only a summary | web3chirou | Lookup needs **order no + access code** (a secret). An order number alone is guessable, and gmailbuy's order-number-only lookup is an IDOR we won't copy. |
| Warranty scope written per category ("first login within 24 h only") | gmailbuy, web3chirou | `after_sales_hours` per product (site default 28 days = 672 h), shown as a badge and on the order |
| Explicit idempotency guidance for purchase APIs | web3chirou docs | Already in place (idempotent client ids, `UNIQUE(channel, trade_no)`) |

We do **not** copy: Telegram/WeChat QR as the only support channel, stock-0 items still buyable, or "records may be purged at any time".

## 1. Brand and domains

- **Brand**: the Latin wordmark **Kesiv**. kesiv.com is already owned, it is 5 letters, and it is quick to type.
  - Chinese name proposal: **可信** (*kěxìn*, "trustworthy"), a near-homophone of *ke-siv* that also says what a virtual-goods shop has to earn.
  - Alternative: **客速** (*kèsù*, "fast for customers").
- **All brand strings live in one place**, so renaming is a one-line change:
  - frontend `packages/shared/src/brand.ts` (name, Chinese name, tagline, logo SVG);
  - backend `APP_BRAND_NAME` (used by the OG/meta defaults).
- The internal package names (`@24goods/*`) and the compose project name stay. They are never shown to users.
- **Logo**: inline SVG monogram (a rounded square with a "K" and a check notch), also used as the favicon. There are no image files to host.
- **Domains**: `kesiv.com` is the storefront (the shortest to type). `shop.kesiv.com` is a 301 redirect to it, kept for links and printed material. Later it can become the merchant/reseller entry.
  - DNS today: kesiv.com is on Cloudflare (proxied); shop.kesiv.com has no record.
  - The demo stays on `IP:18888` until the owner decides how to route it. The host Caddy owns 80/443.

## 2. Storefront UX

1. **Product list**:
   - rows with a 56 px thumbnail, a 2-line title, badges (`Instant` / `Manual`, `After-sales 28d`), price, stock and sold count, and a **Buy** button;
   - grouped by category with sticky headers;
   - a grid toggle that is remembered locally.
   - Search results use the same row.
   - Product detail: the image is capped (aspect 4:3, max 280 px tall) with the title and price above the fold.
2. **Generated cover** when a product has no image: a deterministic SVG (palette colour from a hash of the title, a category glyph, and the first 1–2 characters of the title, CJK-aware). It is rendered client-side by `ProductThumb`, so it needs no storage and no generation endpoint, and it looks the same in admin and buyer apps.
3. **Announcement**:
   - `GET /site` returns `announcement {version, title, body_html, enabled}`.
   - Shown as a modal on entry until it is dismissed. The dismissal is stored in `localStorage` keyed by `version`, and an edit bumps the version, so the modal shows again.
   - Also shown as a collapsible bar on Home.
4. **Back navigation (bug)**:
   - The cause: `router.back()` used `window.history.length`, which counts the external payment page.
   - The fix: a shared `goBack(fallback)` that uses vue-router's `history.state.back`. That value is null when the entry was reached from outside the SPA (the payment return, a direct link or a refresh), and in that case we `replace` to a fallback.
   - Fallbacks: chat → `/messages`, order → `/orders`, product → `/`.
   - The payment-return page itself is entered with `replace`.
5. **Chat CTA wording**: drop "Want a better price?". The product page shows **"Questions before buying? / Report a problem"**. The chat empty state offers chips for *Pre-sales question*, *Problem with an order* (opens after-sales) and *Delivery & usage*.
6. **Recently viewed**:
   - `localStorage` keeps up to 20 product ids with timestamps.
   - Shown as a strip on Home and as a list in Me, with a "clear" control.
   - `GET /products?ids=` returns summaries in that order.
7. **Recommendations** (`GET /products/recommended?for=&recent=&limit=8`) use one SQL query, cached in Redis for 5 min per input:
   - score = 3·[same category as the context or recent items] + 2·co-purchase(90 d, same buyers) + ln(1+sold_30d) + 0.5·[created < 14 d];
   - excludes out-of-stock items, drafts and the context item.
   - Home shows "Recommended for you"; the product page shows "You may also like".
8. **Me page**:
   - Edit profile: display name, language, email. Instant accounts can set an email and password.
   - Change password (needs the current one; revokes other sessions).
   - Then a list: Order lookup · Help center · Platform rules · After-sales policy · Terms · Privacy · Language · **Contact online customer service** (bottom, opens the support conversation without a product) · Delete account.

## 3. Guest purchase → **instant account** (decision)

**Pure guest checkout is rejected.**
- Chat, after-sales, reveal limits and order history all need an identity.
- Anonymous orders can't be rate-limited per buyer.

**Instead: one-tap instant account**, which is what the owner suggested.

- **Direct buy link** `/buy/{product_id}?qty=N&src=tag`: opens the product with the buy sheet open. `src` is stored on the order (`[a-z0-9_-]{0,32}`) for traffic attribution.
- If the visitor isn't signed in, the sheet offers **"Continue without registering"** (email optional) or "Sign in".
- `POST /auth/instant {pow, email?}`:
  - creates a buyer with a random handle `u-xxxxxxx`, a random 14-char password, and `is_instant=true`;
  - starts a session;
  - returns the credentials **once**.
- The UI shows a credentials sheet (copy · save as .txt) and continues to checkout.
- Each order also gets an **access code** (see §4), so the buyer can recover with order no + code even if the credentials are lost.

Abuse controls (the reasons pure guest checkout would be risky, and how each is handled):

| Threat | Control |
|---|---|
| Mass account creation | Self-hosted **proof-of-work**: an HMAC-signed challenge with a 120 s TTL and single use (Redis); the client brute-forces `sha256(challenge‖nonce)` to N leading zero bits. The default of ~18 bits takes about 0.3–1 s on a phone. No third-party captcha (China reachability + privacy). Plus per-IP limits: 3/h and 10/day per IP; 30/day per /24 (IPv4) or /48 (IPv6). |
| Order flooding / stock hoarding | Instant accounts: **max 2 pending orders** (registered: 5). Unpaid orders expire in 30 min. Stock is not reserved by unpaid orders (M1 adds reservation only at pay-redirect, with a short TTL). |
| Junk accounts accumulating | Instant accounts with no paid order are purged after 7 days. |
| Credential loss | Order lookup by order no + access code; the buyer can set an email and password later in Me. |

The same PoW is used on `/auth/register`, `/lookup` and `/auth/login` (only after 3 failures).

## 4. Order / card lookup (卡密查询)

- Every order gets an `access_code`: 10 chars of Crockford base32 (50 bits). It is generated server-side and stored only as `HMAC-SHA256(lookup_key, order_no‖code)`.
  - It is shown on the order page, the payment-return page and the instant-credentials sheet, with copy buttons and "save as .txt".
- `POST /lookup {order_no, access_code, pow}` returns the order view (status, items, delivery info, after-sales deadline). Limits: 10 per hour per IP and 5 failures per order per hour.
- `POST /lookup/reveal {order_no, access_code}` reveals codes through the same delivery service, with the same per-order, per-buyer (the order's buyer) and global budgets as `/orders/{id}/reveal`.
- A lookup never creates a session. Signed-in buyers simply use My Orders.
- `localStorage` remembers the last 5 looked-up order numbers (never the codes).

## 5. Delivery presentation (codes, accounts, redemption)

New product fields:
- `delivery_format`: labels joined by the separator, e.g. `Account----Password----2FA----Notes`;
- `delivery_separator`: default `----`; also `|`, `,`, tab or `:`;
- `redeem_url`: https only, validated, rendered with `rel=noopener noreferrer`;
- `usage_i18n`: markdown, sanitized server-side to `usage_html`;
- `after_sales_hours`: null means the site default, 672 h (28 d); 0 means no after-sales.

Revealed code view (order page, lookup page, and **inline in chat** under the delivery message):
- each card is split by the separator into labelled rows (from `delivery_format`), each with a copy button;
- **Copy all** / **Download .txt**;
- a "How to use" box with the **Redeem at …** button and usage notes;
- the after-sales deadline ("After-sales until 3 Dec, 14:05 · 27 days left").
- If a card has more fields than labels, the extras are labelled "Field N". If there is no format, the code is shown as one block.

## 6. Chat improvements

- **Long text → .txt**:
  - Text over 500 characters is offered as a `.txt` file (the default choice); over 2000 characters it is always sent as a file.
  - Upload `kind=file`: `text/plain` only, valid UTF-8, no NUL bytes, ≤ 256 KB, served `Content-Disposition: attachment` with `nosniff` and the sandbox CSP.
  - Message `type=file` with `payload {name, bytes, preview}`, where preview is the first 160 chars.
  - Bubble: file card with preview · Open (in-app text viewer, rendered as text, not HTML) · Download.
- **CS notifications (admin)**: a sound chime (WebAudio, no asset) plus a browser Notification. Each toggles independently and is stored per browser.
  - Fires on a new buyer message, a new paid manual order, and a new after-sales ticket.
  - Only when the tab is hidden or another conversation is open; throttled to 1 per 4 s.
  - The tab title shows an unread count.
- **Manual shipping**:
  - Delivering posts a system message: "Delivered · 14:05 (2 min ago)", with live relative time and the staff note.
  - The delivery content gets **Copy as text** for both staff and buyer.
- **Auto-confirm receipt**:
  - A delivered order auto-completes after `auto_confirm_hours`: site default 72 h for manual, 24 h for auto_card.
  - The timer pauses while an after-sales ticket is open.
  - The buyer sees a countdown and can confirm early.
  - The job runs in the existing expiry loop.

## 7. After-sales (售后)

- Opened from **My Orders / order detail** ("Request after-sales"); the chat chip links there.
- Allowed when the status is `delivered` or `completed`, and `now < delivered_at + after_sales_hours` (snapshotted on the order as `after_sales_until` at delivery).
- One open ticket per order.
- Ticket fields: reason (`not_received`, `invalid_code`, `wrong_item`, `account_issue`, `other`), description (≤ 2000), up to 4 image attachments.
- Opening a ticket posts a system card into the order's conversation, so it is discussed in chat, and notifies staff.
- Staff actions (admin **After-sales** page; every action is audited):

| Action | Effect |
|---|---|
| reply | Via the order conversation |
| **replace** | Manual: new delivery text. Auto_card: an outbox `delivery.replace` → the delivery worker voids the reported card (`void_reason=defective`) and claims a new one for the same order |
| **refund (recorded)** | Order → `refunded`; a balanced reversal ledger transaction. The money goes back to the buyer through the Alipay merchant console in MVP; API refunds come at M1. |
| reject | With a reason shown to the buyer |
| resolve | Closes the ticket |

## 8. Card management (security-preserving)

The constraint: staff **never** see plaintext. Only the delivery service holds the private key (invariant 1), so nothing below decrypts in the API.

| Need | Solution |
|---|---|
| "Find the card I imported" | **Find by original content**: staff paste one or more codes; the API computes the keyed fingerprint (the API already holds `fp_key`) and matches `UNIQUE(tenant, fingerprint)` exactly. The result shows the masked id, status, product, batch, expiry and order no if sold. |
| "Find by account name" | New `lookup_fp` = keyed HMAC of the **first field** (split by the product's separator, case-folded). "Find by account / first field" for multi-field cards. |
| Expiring codes | Optional `expires_at` per import batch, or per line via `|exp=YYYY-MM-DD`. Delivery never claims expired cards. An hourly job voids them (`void_reason=expired`). The admin shows "expiring in 7 d". |
| Invalidate | **Void** (single, bulk by selection, or all remaining in a batch), with a reason. Allowed for `available`/`reserved` only. A DB trigger enforces that `app_api` can only move `available→void`. |
| Modify | **Replace content** = seal a new card + void the old one (`replaced_by` link). Records stay immutable for audit. |
| Delete | Not offered: void is the delete, which keeps audit and duplicate protection. Void cards are excluded everywhere. |
| Sold card is bad | Not editable. Handled by after-sales → replace (§7). |
| Batches | Every import creates an `import_batch` (note, count, expiry, by, at). The batch list shows available/sold/void counts. |
| Filters | Status, batch, expiring-soon and order no. |

Plaintext export or staff reveal of unsold cards is **not offered**. That is deliberate: a stolen staff session can't dump stock. If the owner needs it later, the design is a delivery-side, TOTP step-up, audited, per-card reveal (M1+).

## 9. Data model additions (migration `0002_storefront_v3`)

- `users`:
  - `is_instant bool`, `email_verified bool`;
  - status adds `'deleted'`;
  - email of deleted users is `deleted+<id>@invalid`.
- `products`: `delivery_format`, `delivery_separator`, `redeem_url`, `usage_i18n jsonb`, `usage_html_i18n jsonb`, `after_sales_hours int null`, `group_sort int`.
- `orders`: `access_code_hash`, `source`, `after_sales_until`, `auto_confirm_at`.
- `articles`:
  - `tenant_id, slug, kind (article|page), category, title_i18n, body_i18n, body_html_i18n, status (draft|published), pinned, views, published_at, updated_at, created_by`;
  - UNIQUE (tenant, slug).
- `after_sales`:
  - `tenant_id, order_id, buyer_id, reason, description, attachment_ids uuid[], status (open|processing|replaced|refunded|rejected|resolved), resolution_note, resolved_by, resolved_at, created_at, updated_at`;
  - a partial UNIQUE on `(order_id)` WHERE status IN (open, processing).
- `card_batches`: `tenant_id, product_id, note, count, expires_at, created_by, created_at`.
- `card_stock`: `batch_id`, `lookup_fp`, `expires_at`, `void_reason`, `voided_at`, `voided_by`, `replaced_by`.
- Site settings live in `tenants.settings`:
  - `announcement {version, enabled, title_i18n, body_i18n, body_html_i18n}`;
  - `after_sales_default_hours`;
  - `auto_confirm_hours {manual, auto_card}`;
  - `support_hours`.
- RLS ENABLE + FORCE on every new tenant table; grants follow the v2 matrix.
- Card-status transition trigger for `app_api`.
- Markdown → HTML: `markdown-it-py` with HTML disabled, then an **`nh3` allowlist sanitize**, done server-side at save time. The SPA renders `*_html` with `v-html` only from these fields. The CSP stays strict (no inline script).

## 10. Not in this batch (deferred, with reason)

- **Email verification, password reset by email**: needs an SMTP provider decision (M1).
- **Real refund API**: ZPay `act=refund` needs the real merchant account (M1).
- **Server-rendered article pages for SEO**: M1, together with OG `/s/{id}` and the sitemap.
- **Avatar upload**: initials avatars are enough for now.

## 11. Build plan (agents)

| Agent | Scope | Owns |
|---|---|---|
| backend | migration 0002, all endpoints in §12, PoW, jobs (auto-confirm, card expiry, instant-account purge), delivery worker changes (skip expired, `delivery.replace`), seed (articles, pages, formats, batches), tests incl. cross-tenant | `backend/`, `docs/api-contract.md` |
| web | §2, §3, §4, §5, §6 (buyer side), shared brand/logo/ProductThumb/goBack/file bubble/PoW solver | `frontend/apps/web`, `frontend/packages/shared` |
| admin | articles editor, site/announcement settings, card management (§8), after-sales desk, notifications, product dialog fields, manual-ship copy, brand | `frontend/apps/admin`, plus a new `packages/shared/src/api/admin-v3.ts` |

The orchestrator merges, runs AGENTS.md §3 + E2E, runs the security-reviewer, payment-auditor and i18n-mobile-reviewer, fixes, deploys the demo and updates docs.

## 12. API additions (authoritative; folded into `docs/api-contract.md`)

Public / buyer:
```
GET  /site                      -> {brand, announcement|null, after_sales_default_hours, support_hours, pages:[{slug,title}]}
GET  /pow/challenge?scope=instant|register|lookup|login -> {challenge, bits, expires_at}
POST /auth/instant  {pow:{challenge,nonce}, email?}     -> {user, credentials:{login, password}}
PATCH /me           {display_name?, locale?}            -> User
POST /me/credentials {current_password?, email?, new_password?}  (instant: current not needed) -> User
POST /me/delete     {password? , confirm:"DELETE"}      -> 204   (409 if pending/paid/manual_review order or open after-sales)
GET  /products?ids=a,b,c        (≤20, keeps order)       -> {items: ProductSummary[]}
GET  /products/recommended?for=&recent=&limit=          -> {items: ProductSummary[]}
GET  /articles?category=&page=  -> Paginated<{slug,title,category,published_at,updated_at,views,pinned}>
GET  /articles/{slug}           -> {slug,title,category,kind,body_html,published_at,updated_at,views}
POST /lookup        {order_no, access_code, pow}        -> LookupOrder
POST /lookup/reveal {order_no, access_code}             -> {cards:[{id,code}]} (Cache-Control: no-store)
POST /orders        {product_id, qty, src?}             -> Order (+ access_code once, in field `access_code`)
GET  /orders/{ref}  -> Order now also has: after_sales_until, auto_confirm_at, after_sales (ticket|null), delivery_info
POST /orders/{ref}/after-sales {reason, description, attachment_ids[]} -> AfterSales
POST /uploads       kind=file (text/plain)              -> Attachment
```
`ProductSummary` adds `after_sales_hours` (resolved) and `cover` (may be null, so the client generates one). `Product` adds `delivery_format`, `delivery_separator`, `redeem_url`, `usage_html`. `delivery_info = {format_labels[], separator, redeem_url, usage_html}`.

Admin:
```
GET/PUT  /admin/site                         announcement, defaults (admin only)
GET/POST /admin/articles, GET/PATCH/DELETE /admin/articles/{id}
GET  /admin/after-sales?status=&page=        GET /admin/after-sales/{id}
POST /admin/after-sales/{id}/action {action: processing|replace|refund|reject|resolve, note?, delivery_text?}
GET  /admin/products/{id}/batches
POST /admin/products/{id}/cards/import {lines, note?, expires_at?}  -> {imported, duplicates, rejected, batch_id}
POST /admin/cards/find {lines[], by: content|first_field}           -> {items: CardRow[]}  (≤100 lines)
POST /admin/cards/void {ids[]|batch_id, reason}                     -> {voided}
POST /admin/cards/{id}/replace {code}                               -> CardRow (new)
GET  /admin/products/{id}/cards?status=&batch_id=&expiring_days=&order_no=
```
`CardRow = {id, masked, status, batch_id, expires_at, void_reason, order_no|null, created_at}`.
SSE adds the events `after_sales` (staff) and `order` updates for auto-confirm.
