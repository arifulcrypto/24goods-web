# Review v5 — growth MVP: settings center, promo codes, resellers, product import

Owner request 2026-11-06 (after v4). Priority: **ship to dev/demo fast**; production later by
the owner. This file is the build spec; §12 is the authoritative API contract.

Legend: ✅ in v5 · ⏭ later (v5.x / M-phase) · ⚠ owner action needed.

## 0. Feasibility summary

| Request | Verdict |
|---|---|
| Cart delete; admin delete products | ✅ cart already deletes by swipe: make it visible + manage mode + "clear unavailable". Products: real **archive** + hard delete when never sold + bulk actions |
| Categories enable/disable/add | ✅ new `categories` table + admin page |
| Dev/test credentials | ✅ `scripts/dev.sh creds`, demo: `/opt/24goods-demo/deploy/secrets/demo-credentials.txt` |
| Brand 客思维 | ✅ zh-CN name is now **客思维** (en stays **Kesiv**); site name overridable in Settings → General |
| Resellers + subdomains + markup + sign-up fee | ✅ "reseller lite": sub-storefront inside the platform tenant (§7). Subdomains `{slug}.kesiv.com` work on Cloudflare **Free** (proxied wildcard DNS + Universal SSL covers one level) ⚠ prod DNS/origin-rule change done at deploy time. Reseller own domains (CF for SaaS) ⏭ |
| "Flexible API integration" | ⏭ v5.1: reseller open API (API keys, prepaid balance, place order, fetch delivery) and upstream API delivery (first target: sub2api redeem codes on flux.kesiv.com). Schema-neutral, no blocker now |
| Manual delivery 24 h, no rush | ✅ per-product ETA (default 24 h) + configurable note |
| Promo codes | ✅ percent/fixed, limits, scope, window (§6) |
| Product import (URL / pasted text / pasted HTML), images, Ctrl+V, default markup 20 % | ✅ (§8). go.ka8.ai is a client-rendered SPA → URL import falls back to "paste the page" |
| Cloudflare Turnstile toggle in admin | ✅ app-level, per action (§5.1). Reuses the sub2api widget keys; the existing CF API token is **not** needed (widget keys are separate). ⚠ the widget's hostname list must include the store host(s) |
| sub2api-style settings | ✅ tabs General / Login terms / Features / Security & auth / User defaults / Payment / Email / Resellers / Import; Operation logs page with filters. Backup tab ⏭ (prod uses `scripts/prod.sh backup`). sub2api is Go (LGPL-3.0): we adopt the structure and ideas, not the code |
| Google sign-in, SMTP from sub2api | ✅ reuse keys. ⚠ Google needs the redirect URI `https://<store-host>/api/v1/auth/oauth/google/callback` added in Google Cloud Console and an HTTPS hostname (raw-IP demo cannot use Google login) |

## 1. Cart and product deletion

- Web cart: each line shows a visible trash button (44 px) besides swipe; header "Manage" toggles
  select mode with "Select all" + "Delete (n)". Lines whose product is archived/draft/out of stock/
  in a disabled category go to an **"Unavailable"** group (greyed, cannot be selected for
  checkout) with "Clear unavailable items". `POST /cart/quote` already returns per-line state;
  it adds `unavailable_reason: archived|unpublished|out_of_stock|null`.
- Admin products: statuses `active | draft | archived`. `DELETE /admin/products/{id}` → archive
  (`archived_at=now`, hidden from storefront and from the default admin list; status filter has
  "Archived"). `?hard=true` deletes the row only if no order, card stock, conversation, quote or
  promo/reseller row references it, else 409 `product_in_use`. `POST /admin/products/{id}/restore`
  → draft. `POST /admin/products/bulk` `{ids ≤200, action: publish|unpublish|archive|restore|
  set_category, category?}` → `{updated}`. All audited.

## 2. Categories

`categories(slug, name_i18n, icon, sort, enabled)`, per tenant, seeded with the 6 old slugs.
`products.category` stays the slug (validated against the table on create/update, any status).
Storefront: `GET /catalog/categories` returns enabled ones by sort; products whose category is
disabled are hidden from listings/search/home/recommended but their page still loads (direct
links, carts). Admin CRUD; delete only if no product uses the slug (else 409 `category_in_use`,
disable instead). Slug immutable after create. `app/modules/catalog/categories.py` becomes a DB
reader with a 60 s in-process cache invalidated on admin writes; the seed creates categories for
new tenants (`ON CONFLICT DO NOTHING`).

## 3. Settings center (sub2api-style)

All non-secret settings live in `tenants.settings` sections normalized by
`app/core/settings_v5.py` (`section(ts, name)`, `merge`, `DEFAULTS`) — **already written, read it**.
`PUT /admin/site` keeps its partial-update contract; `SiteIn` gains one optional model per new
section: `general, legal, features, security, defaults, email, delivery, import, resellers, audit`
(each field optional; validated with pydantic, then stored through `merge`). Existing sections
(announcement, payments, support, rate_limit, after_sales…) are unchanged.

Secrets: `PUT /admin/site/secrets` `{name: turnstile_secret|google_client_secret|smtp_password,
value: str|null}` (null deletes). Stored AES-256-GCM in `tenant_secrets` with key
`APP_SETTINGS_KEY` (32 bytes base64, api.env only; generated by bootstrap/dev.sh/prod setup).
Nonce random 12 bytes, AAD = `tenant_id|name`. Responses only expose
`secrets: {name: {configured: bool, last4}}`. Never logged, never audited in plaintext (audit
`settings.secret_set` with name only). Module `app/core/secretbox.py`.

Admin UI tabs (Element Plus, one page `SettingsPage.vue`, replaces `SitePage.vue`; route
`/settings/:tab?`, old `/site` redirects):
1. **General** — site name/subtitle (en, zh-CN), logo, contact, footer + existing announcement,
   support hours, welcome message, quick questions.
2. **Login terms** — enable, mode (checkbox/modal), pick published pages (articles kind=page).
3. **Features** — toggles (§3 table below), access limiter (existing rate_limit).
4. **Security & auth** — Turnstile on/off, site key, secret (write-only), protected actions;
   email verification on register; Google login client id + secret.
5. **User defaults** — default locale, max pending orders, order timeout.
6. **Payment** — existing channels (Alipay fee 3 %), ZPay status read-only ("configured via
   server env, PID ••1234"), after-sales/auto-confirm (existing).
7. **Email** — SMTP fields, password (write-only), notify toggles, "Send test email".
8. **Resellers** — program fields (§7).
9. **Import** — default markup kind/value, rounding.
Operation logs = existing Audit page upgraded (§9).

Feature toggles and where the API enforces them: `registration` (POST /auth/register → 403
`feature_disabled`), `guest_checkout` (POST /auth/instant), `cart` (/cart/quote, /checkout),
`promo_codes` (validate + apply), `resellers` (apply + storefront resolution), `voice_messages`
(voice upload), `lookup` (/lookup), `google_login` (oauth start). Public `GET /site` exposes
`features`, `general`, `legal` (enabled, mode, pages [{slug,title}]), `turnstile`
(`{enabled, site_key, actions}`), `google_login` (bool, only if client id + secret configured),
`delivery` (eta + note), `storefront` (§7) — never secrets.

## 4. Brand

zh-CN brand = **客思维** (`brand.ts` nameZh; `brandName('zh-CN')` = "客思维"). en = Kesiv.
`general.site_name_i18n` overrides at runtime when set (web header, document title, emails).

## 5. Security & auth

### 5.1 Turnstile (Cloudflare "shield")
- Off by default. When on, the listed actions require `turnstile_token` (JSON body field; for
  GET lookup a header `X-Turnstile-Token`). Server: POST
  `https://challenges.cloudflare.com/turnstile/v0/siteverify` (`secret`, `response`,
  `remoteip`, `idempotency_key`), 5 s timeout; failure or timeout → 400 `turnstile_failed`
  (**fail closed** only when enabled). Token single use (Redis 5 min). If enabled without a
  secret configured → treat as disabled + log a warning (avoid lock-out).
- Actions: login, register, instant_account, password_reset, lookup, promo_validate,
  reseller_apply. MVP default list: login, register, instant_account, password_reset.
- Frontend loads `https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit` **only
  when enabled and only on a protected form** (documented exception to invariant 8, ADR-022;
  Cloudflare forbids self-hosting the script). Widget mode "managed", size flexible.
- Dev/demo uses Cloudflare test keys (site `1x00000000000000000000AA`, secret
  `1x0000000000000000000000000000000AA`, always pass); production uses the sub2api widget keys.
- Admin login (staff console) is also a `login` action when enabled.

### 5.2 Email verification and password reset (SMTP)
- `email.enabled` + host… ; password secret. Sender: stdlib `smtplib` in `asyncio.to_thread`
  (no new dependency), 10 s timeout, STARTTLS/SSL per setting, never log the password.
- `POST /auth/email-code` `{email, purpose: register|reset, turnstile_token?}` → 6-digit code in
  Redis (10 min, 5 attempts, 1/min + 10/day per email, per-IP limit) → 204 always (no user
  enumeration). Register requires `email_code` when `security.email_verify_on_register`.
  `POST /auth/password-reset` `{email, code, new_password}` → resets, revokes sessions.
  Instant (guest) accounts can attach an email via the same code later (⏭).
- Order emails (outbox topic `email.order`, sent by the api jobs loop, best effort, never inside
  the payment transaction): paid (with order link + lookup hint) and delivered (link only —
  **never card secrets**). Only to real (non-instant) verified emails.
- Email templates (owner request, sub2api-style): settings `email_templates.{verify_code,
  password_reset,order_paid,order_delivered} = {subject_i18n, body_i18n}`, placeholders
  `{site_name} {code} {minutes} {order_no} {order_url} {title} {amount}`, defaults built in.
- `POST /admin/site/email/test` `{to, template?}` → sends a test, returns `{ok, error?}` (error message
  sanitized).

### 5.3 Google sign-in
- `GET /auth/oauth/google/start?next=/path` → 302 to Google (scope `openid email profile`, state
  + PKCE in Redis 10 min, `next` must be a relative path). `GET /auth/oauth/google/callback` →
  exchange code (httpx, 10 s) → validate id_token claims (iss, aud = client id, exp,
  `email_verified`) — token comes straight from Google's token endpoint over TLS (OIDC Core
  §3.1.3.7) so signature check is optional; we still check claims. Match `users.google_sub`, else
  an existing user with that verified email (link), else create a buyer (`email_verified=true`,
  random password). Staff accounts may NOT log in via Google (403). Session as normal login,
  then 302 to `next`. Redirect URI `{PUBLIC_BASE_URL}/api/v1/auth/oauth/google/callback`.

## 6. Promo codes

- Admin CRUD (`/admin/promo-codes`), list with usage; random code generator (8 chars,
  Crockford base32). Codes stored upper-case; input is case-insensitive.
- Discount rules: eligible subtotal = lines matching `product_ids` / `category_slugs` (empty =
  all). percent: `floor(eligible * value / 1000)`; fixed: `min(value, eligible)`; then cap at
  `max_discount_minor`; must satisfy `min_subtotal_minor` on the eligible subtotal; total
  discount must be `< subtotal` (amount stays > 0). Window, `enabled`, `usage_limit`
  (`used_count`), `per_user_limit` (non-released redemptions of this user) checked under
  `SELECT … FOR UPDATE` of the promo row.
- **Fee is computed on (subtotal − discount)**: `fee = ceil((subtotal − discount) * permille /
  1000)` per order; `amount = subtotal − discount + fee` (DB CHECK).
- Checkout (cart): discount allocated across member orders proportionally to eligible line
  subtotals, largest-remainder, each order's discount < its subtotal. One redemption row with
  `checkout_id`; member orders carry `promo_code_id` + `discount_minor`.
- Single order (buy now): redemption with `order_id`. Quotes (chat price) and reseller
  storefronts: promo not applicable in v5 (`promo_not_applicable`).
- Redemption lifecycle: created `reserved` (+`used_count`) in the same transaction as the
  order/checkout; → `used` when paid; → `released` (−`used_count`) when the order/checkout is
  cancelled or expires unpaid. Refund after payment keeps it `used`.
- Ledger at payment: `gateway_clearing:zpay +amount`, `sales_revenue −subtotal`,
  `promo_discount +discount`, `surcharge_revenue −fee`, (reseller: see §7). Refund mirrors.
- Buyer API: `POST /promo/validate` `{code, items:[{product_id, qty}], turnstile_token?}` →
  `{code, discount_minor, eligible_subtotal_minor, description}` or 422 error code. Order create
  (`POST /orders`) and `POST /checkout` and `POST /cart/quote` accept optional `promo_code`;
  quote response adds `discount_minor` and per-line `discount_minor`.
- Errors: `promo_invalid` (unknown/disabled), `promo_not_started`, `promo_expired`,
  `promo_exhausted`, `promo_user_limit`, `promo_min_subtotal`, `promo_not_applicable`.
- Rate limit validate: 20/min per user/IP.

## 7. Resellers ("reseller lite")

**Model.** A reseller is a buyer account with a `resellers` row (same tenant). Their store is a
**storefront view** of the platform catalog: same products, same stock, same CS team, same
fulfilment — only branding (store name/logo/welcome) and **prices** differ. Buyers' accounts are
shared across storefronts (one login per host because of `__Host-` cookies).

**Storefront resolution** (`app/core/storefront.py`, request middleware/dependency): Host
`{slug}.{resellers.base_domain}` → active reseller; else header `X-Storefront: {slug}` (the SPA
sets it from `?store={slug}`, remembered in sessionStorage — needed on the raw-IP demo); else
none. Unknown/suspended slug → none (main store). Only when `features.resellers` is on.
Security: a storefront only ever **adds** markup and changes branding; buyers can always buy at
base price on the main store, so the header fallback cannot lower a price.

**Price.** `unit = price_minor + markup`, markup per product override (`reseller_prices`) else
reseller default; percent: `ceil(price * permille / 1000)`, fixed: `+value`. Clamp to settings
max. `hidden` products disappear on that storefront. Every catalog/product/cart/order endpoint
returns storefront prices when a storefront is resolved. Order: `reseller_id`,
`reseller_markup_minor = markup * qty` (part of subtotal). Checkout: `checkouts.reseller_id`.
Quote (chat price) orders: markup 0, reseller_id still set for attribution.
Conversations created on a storefront get `reseller_id` (CS sees "via store X").

**Sign-up.** `POST /reseller/apply` `{slug, store_name, contact?, turnstile_token?}` (feature on,
logged-in non-instant buyer, slug regex + not reserved + unique) → creates `resellers`
(pending_payment) and, when `signup_fee_minor > 0`, an order `kind='reseller_signup'`,
`delivery_mode='system'`, `product_id=NULL`, title "Reseller sign-up: {store_name}", amount via
the normal fee rule, `signup_order_id` set; buyer pays with the existing `POST /orders/{no}/pay`.
On settlement (`settle_member`): `kind == 'reseller_signup'` → reseller `active`,
`activated_at`, order → `completed` immediately, system chat message; ledger
`gateway_clearing +amount`, `reseller_signup_revenue −subtotal`, `surcharge_revenue −fee`. Fee 0
→ active immediately. Re-applying while pending re-uses the row and makes a new order if the old
one is cancelled/expired. Admin can activate (waive), suspend, re-activate.

**Earnings ledger.** At payment of a goods order with markup: `sales_revenue −(subtotal −
markup)`, `reseller_payable:{reseller_id} −markup` (plus promo/fee lines as §6). Refund mirrors
(+markup back). Balances shown in the reseller center: `pending` = markup of paid/delivered
orders and completed orders younger than `settle_after_hours`; `available` = completed older
than that − payouts requested/paid. Payout: `POST /reseller/payouts` `{amount_minor}` (≥ min,
≤ available, one open request at a time) → admin marks paid (ledger
`reseller_payable:{id} +amount`, `cash:payouts −amount`) or rejected. Payout account
(`{method: alipay, name, account}`) set in reseller settings; snapshotted on request.

**Reseller center** (buyer web app, Me → "Reseller center"): apply/pay; dashboard (store URL with
copy + QR, today/7-day orders and earnings, balances); products (catalog with base price, my
markup, my price, hide toggle, bulk "apply default"); orders (order_no, title, qty, amount,
markup, status, date, buyer handle masked — no buyer input, no delivery content); payouts; store
settings (name, logo, welcome, default markup, payout account).

**Admin**: Resellers page (list/filter, detail drawer with stats, activate/suspend, edit markup
limits per reseller), Payouts queue (mark paid with note / reject). Orders list gets a
"Store" column/filter.

**White-label.** On a storefront the platform brand never shows (store name/logo replace it;
`/site.storefront.white_label = true`). Owner concern: buyers can strip the subdomain and land on
the main store → v5.1 adds **reseller custom domains** via Cloudflare for SaaS (custom hostnames,
free up to 100 on the Free plan; CNAME to a fallback origin) and optionally a neutral
white-label base domain whose apex is not the main store (`resellers.base_domain`).

**Subdomains in production** ⚠ (done at deploy, not in dev): proxied wildcard `A *.kesiv.com →
c4`; config/origin rules extended from `shop.kesiv.com` to `(http.host wildcard "*.kesiv.com"
and not http.host in {existing names})`; Origin CA cert re-issued for `*.kesiv.com` +
`kesiv.com`; Caddy site block `*.kesiv.com`; AOP: zone-level with our client cert (other
origins ignore it). The current CF token (DNS, SSL, Zone Settings, Zone Rulesets: Edit) covers
all of this.

## 8. Product import (middleman listing)

Admin → Products → "Import": tabs **From URL** and **Paste page**.
- `POST /admin/products/import/fetch` `{url}` → server-side fetch (§8.1) → parse → `ImportDraft`.
- `POST /admin/products/import/parse` `{text?, html?, base_url?}` (the paste box sends the
  clipboard's `text/html` when present, else plain text) → `ImportDraft`.
- `ImportDraft = {title, description_md, images:[{url, alt}], price_minor|null, currency,
  source_url|null, site|null, suggested_price_minor|null, warnings:[str]}`.
  `suggested_price_minor = roundup(cost + markup, round_to_minor)` using settings `import`.
- Parsers (`app/modules/catalog/importer/`): JSON-LD `Product`/`Offer`, OpenGraph/meta,
  `__NEXT_DATA__`/`__NUXT__` JSON when present, then heuristics (h1, main content block,
  price regex `[¥￥$]\s?\d+(\.\d{1,2})?` / `价格|售价`, choose the most prominent). Site
  adapters for gmailbuy.com, maitg.com (server-rendered, verified 2026-11-06), go.ka8.ai (SPA:
  try its JSON API if discoverable, else warning "paste the page"). HTML → markdown with a small
  in-house converter over `html.parser` (headings, p, br, lists, b/strong, a, img; drop
  scripts/styles/nav/footer) — no new dependency; output goes through the existing markdown
  sanitizer on save.
- Images are NOT hot-linked: the admin dialog lists found images with checkboxes; selected ones
  go through `POST /admin/content/images/fetch` `{url}` → SSRF-safe download → existing upload
  pipeline (magic bytes, re-encode, EXIF strip) → `{id, url}`; description image URLs are
  rewritten to ours.
- Pre-filled ProductDialog: title, description, price = suggested, `cost_minor`, `source_url`,
  delivery_mode **manual**, `delivery_eta_hours` 24, status **draft**. Staff-only fields
  (`cost_minor`, `source_url`) appear in admin views only; admin list shows margin.
- Ctrl+V: MarkdownEditor and the product image uploader accept pasted image files (and
  drag-drop) → upload → insert `![alt](url)`; alt text editable ("image description").

### 8.1 SSRF-safe fetcher (`app/core/safefetch.py`)
http/https only, ports 80/443 (+8080/8443), resolve host and reject private, loopback,
link-local, CGNAT, multicast, reserved, IPv4-mapped, unspecified; connect to the vetted IP
(pin; Host header + SNI preserved), redirects manual ≤3 each re-vetted, 10 s total, body cap
3 MB (pages) / 8 MB (images), content-type check, UA `KesivImporter/1.0`. Admin-only, rate
limited 30/min per admin. Never forwards cookies/auth.

## 9. Operation logs

Audit page: filters action (prefix), actor (email search), target, severity, date range;
detail drawer with before/after JSON diff; CSV export of the filtered set (≤10 000 rows);
`audit.retention_days` (default 365) purged daily by the jobs loop. Money-state audit rows are
also in ledger/order_events, so purge is safe. New audit actions: `category.*`, `product.archived|
restored|hard_deleted|bulk`, `promo.*`, `reseller.*`, `payout.*`, `settings.secret_set`,
`settings.updated` (section names), `auth.google_login`, `auth.password_reset`.

## 10. Schema (migration `0004_v5_growth`, already written)

See the migration file — it is authoritative: `categories`, products (`archived` status,
`delivery_eta_hours`, `source_url`, `cost_minor`, `archived_at`), `tenant_secrets`,
`users.google_sub`, `promo_codes`, `promo_redemptions`, `resellers`, `reseller_prices`,
`reseller_payouts`, orders (`kind`, `discount_minor`, `promo_code_id`, `reseller_id`,
`reseller_markup_minor`, delivery_mode `system`, CHECK `amount = subtotal − discount + fee`,
discount < subtotal, markup ≤ subtotal), checkouts (`reseller_id`, `promo_code_id`),
`conversations.reseller_id`, audit indexes. Models in `app/models/{catalog,orders,growth}.py`.

## 11. Manual delivery promise

`delivery_eta_hours` per product (null → `delivery.manual_eta_hours`, 24). Public product
payloads add `delivery_eta_hours` (manual only) and `GET /site` gives `delivery.manual_note`
(locale-picked, `{hours}` substituted client-side). Web: product page + buy sheet + cart line
show "Manual · ships within 24 h" and the note "Manual delivery does not accept rush orders";
order detail for manual orders shows the promise with the deadline (`paid_at + eta`). Admin
order list highlights manual orders past their deadline.

## 12. API contract (authoritative)

Base `/api/v1`. Errors `{code, message}` like v4. Money in minor units.

Public / buyer
- `GET /site` + `features, general, legal, turnstile, google_login, delivery, storefront`
  (`storefront: null | {slug, store_name, logo_url, welcome}`).
- `GET /catalog/categories` → `[{slug, name, icon}]` enabled only.
- product payloads + `delivery_eta_hours: int|null`; prices are storefront prices.
- `POST /cart/quote` `{items, promo_code?}` → v4 shape + `discount_minor`, line
  `discount_minor`, line `unavailable_reason`, `promo: null|{code, discount_minor}|{error}`.
- `POST /checkout` `{items, channel, promo_code?, buyer_inputs?}`; `POST /orders` + `promo_code?`.
- order payloads + `discount_minor, kind, reseller: null|{store_name}` (buyer view);
  `delivery_eta_hours`, `delivery_deadline` for manual.
- `POST /promo/validate` (§6).
- `POST /auth/email-code`, `POST /auth/password-reset`; register + `email_code?`,
  `agree_terms?` (required when legal.enabled); login/register/instant + `turnstile_token?`;
  `GET /lookup` + header `X-Turnstile-Token`.
- `GET /auth/oauth/google/start`, `GET /auth/oauth/google/callback`.
- Reseller: `GET /reseller/me` → `null | {slug, store_name, status, store_url, markup_kind,
  markup_value, logo_url, welcome_i18n, contact, payout_account, signup_order_no,
  balances:{pending_minor, available_minor, paid_minor}}`; `POST /reseller/apply`;
  `PATCH /reseller/me` (store_name, logo_url, welcome_i18n, contact, markup_kind, markup_value,
  payout_account); `GET /reseller/products?q&page` → base price, markup, price, hidden;
  `PUT /reseller/products/{id}` `{markup_kind?, markup_value?, hidden?}` /
  `DELETE` (reset to default); `GET /reseller/orders?page`; `GET /reseller/stats`;
  `GET /reseller/payouts`, `POST /reseller/payouts`.
- `POST /me/uploads/logo`? — no: reseller logo uses the existing chat image upload then
  `logo_url` (must be our own `/media/` URL).

Admin
- `GET/POST /admin/categories`, `PATCH/DELETE /admin/categories/{id}`.
- `DELETE /admin/products/{id}?hard=`, `POST /admin/products/{id}/restore`,
  `POST /admin/products/bulk`; product create/patch + `delivery_eta_hours, cost_minor,
  source_url`; list `status=archived` filter.
- `POST /admin/products/import/fetch`, `POST /admin/products/import/parse`,
  `POST /admin/content/images/fetch`.
- `GET/PUT /admin/site` (+ sections), `PUT /admin/site/secrets`, `POST /admin/site/email/test`.
- `GET/POST /admin/promo-codes`, `PATCH /admin/promo-codes/{id}`,
  `GET /admin/promo-codes/{id}/redemptions`.
- `GET /admin/resellers?status&q`, `GET /admin/resellers/{id}`, `PATCH /admin/resellers/{id}`
  `{status?, markup_kind?, markup_value?}`, `GET /admin/payouts?status`,
  `POST /admin/payouts/{id}/decide` `{action: paid|rejected, note}`.
- `GET /admin/audit` + `action, actor, target, severity, since, until`; `GET /admin/audit.csv`.
- `GET /admin/orders` + `reseller_id`, `overdue_manual=true` filters; order admin view + `kind,
  discount_minor, promo_code, reseller{slug, store_name}, reseller_markup_minor, cost_minor`.

## 12b. Environments
Demo is also served at **https://demo.kesiv.com** (Cloudflare proxied, Flexible SSL, origin rule →
107.173.10.79:18888; host Caddy untouched). `APP_PUBLIC_BASE_URL=https://demo.kesiv.com`.
Caddy trusts CF-Connecting-IP only from Cloudflare ranges; CSP allows
`https://challenges.cloudflare.com` for script/frame (Turnstile, ADR-022).

## 13. Out of scope / later
Reseller open API + balance wallet, upstream API delivery (sub2api), reseller custom domains,
backup tab, guest-email attach, per-reseller CS seats, promo on reseller storefronts,
multi-currency. Encryption of `orders.buyer_input` stays issue #8.
