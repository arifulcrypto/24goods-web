# API contract — MVP-0.5 (storefront v3)

This contract is shared by `backend/` and `frontend/`. A change here must update both sides in the same PR.

## Conventions

- **Base path:** `/api/v1`. All three surfaces are served from **one origin** behind Caddy:
  - buyer SPA at `/`
  - admin SPA at `/admin/`
  - API at `/api/`
  - dev-only mock gateway at `/mockpay/`
- **Auth:** server-side session.
  - Cookie `sid`: random 256-bit token, `HttpOnly`, `SameSite=Lax`, `Secure` when `APP_COOKIE_SECURE=true`. Session data lives in Redis.
  - Buyer TTL is 7 days. Staff TTL is 12 h.
- **CSRF:** every non-GET request must send the header `X-Requested-With: fetch`; otherwise the API returns `403 csrf`. The frontend client always sends it.
- **Language:** `?lang=en|zh-CN`, otherwise `Accept-Language`, otherwise `en`.
  - Localized fields come back already resolved.
  - Admin endpoints return the full i18n dict: `{ "en": "...", "zh-CN": "..." }`.
- **Money:** JSON **strings** with 2 decimals, always paired with `currency` (MVP is `"CNY"`), e.g. `"amount": "19.90"`. The backend stores integer minor units.
- **Time:** ISO-8601 UTC strings.
- **IDs:** UUID strings, except `messages.id`, which is a monotonically increasing integer used for cursors.
- **Errors:** HTTP 4xx/5xx with body `{ "error": { "code": "snake_case", "message": "human readable (en)" } }`.
  - The frontend translates by `code`.
  - Codes: `unauthorized`, `forbidden`, `csrf`, `not_found`, `validation`, `rate_limited`, `conflict`, `out_of_stock`, `invalid_state`, `quote_expired`, `upload_rejected`, plus the v3 codes below.
  - v3 codes:

    | Code | HTTP | Meaning |
    |---|---|---|
    | `pow_required` | 428 | A proof of work is required and was not sent (see *Proof of work*). |
    | `pow_invalid` | 422 | Wrong nonce, wrong scope, tampered, expired or already used challenge. |
    | `after_sales_closed` | 409 | The order's after-sales deadline has passed (or the product has no after-sales). |
    | `after_sales_open` | 409 | The order already has an open / processing after-sales ticket. |
    | `has_open_orders` | 409 | `POST /me/delete` refused: a pending, paid or manual-review order, or an open after-sales ticket exists. |
    | `invalid_password` | 403 | Wrong `current_password` / `password` on `/me/credentials` and `/me/delete`. |
    | `conflict` | 409 | Also: email can't be used (generic message "This email can't be used" on register and `/me/credentials`; it does not reveal whether the address exists), slug taken, duplicate card, more than the allowed number of pending orders (2 for instant accounts, 5 otherwise). |
- **Pagination:** `?page=1&page_size=20` returns `{ "items": [...], "total": n, "page": 1, "page_size": 20 }`.

## Proof of work (self-hosted captcha replacement)

Required on `POST /auth/instant`, `POST /auth/register`, `POST /lookup`, and on `POST /auth/login` once the same (login, IP) pair has failed 3 times in the last hour (`428 pow_required`; the retry must carry `pow`). It is **not** required on `/lookup/reveal`.

1. `GET /pow/challenge?scope=instant|register|lookup|login` returns `{challenge, bits, expires_at}` (`expires_at` is a Unix timestamp, TTL 120 s; default `bits` = 18, `APP_POW_BITS`). For `scope=instant` the difficulty ramps with the caller's network (/24 or /48): more than 30 instant accounts created today on the net → `bits + 4`, more than 100 → `bits + 6`. The extra bits are part of the signed challenge; verification accepts any signed `bits` ≥ the base.
2. The challenge is an opaque ASCII string `v1.<scope>.<bits>.<expires_unix>.<rand_hex16>.<mac_hex32>` signed with HMAC-SHA256 under `APP_POW_KEY`.
3. The client finds a **decimal integer** `nonce` (no sign, no leading `+`, at most 16 digits) such that
   `sha256(UTF-8("<challenge>:<nonce>"))`, read as a big-endian bit string, starts with at least `bits` zero bits. `<challenge>` is the full string from step 1.
4. The request body carries `"pow": {"challenge": "<challenge>", "nonce": "<nonce>"}`.
5. A challenge is bound to its scope and single use: it is burned in Redis (`SET NX`) when a *valid* solution is accepted. A wrong nonce does not burn it.

```js
// browser reference solver
async function solve(challenge, bits) {
  const enc = new TextEncoder();
  for (let n = 0; ; n++) {
    const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(`${challenge}:${n}`)));
    let z = 0; for (const b of d) { if (b === 0) { z += 8; continue; } z += Math.clz32(b) - 24; break; }
    if (z >= bits) return String(n);
  }
}
```

## Types

```ts
type Role = "buyer" | "agent" | "admin";
type User = { id: string; email: string; display_name: string; role: Role; locale: "en" | "zh-CN";
  is_instant: boolean;          // one-tap account (email may be the placeholder `<handle>@instant.invalid`)
  handle: string | null };      // `u-xxxxxxx` (instant accounts), usable as the login id

type ProductSummary = {
  id: string; title: string; cover: string | null; price: string; currency: string;
  delivery_mode: "auto_card" | "manual"; stock: number | null; // auto_card: available non-expired cards; manual: `manual_stock` (null = unlimited / n.a.)
  category: string; sold: number;
  after_sales_hours: number;    // resolved: product override, else the site default (672); 0 = none
};
type Product = ProductSummary & {
  description: string; images: string[]; status: "active" | "draft";
  delivery_format: string | null;      // labels joined by the separator, e.g. "Account----Password----2FA----Notes"
  delivery_separator: string;          // "----" | "|" | "," | "\t" | ":"
  redeem_url: string | null;           // https URL or site-relative path, <= 500 chars
  usage_html: string;                  // sanitized HTML, resolved to the request language
};
type AdminProduct = Omit<Product, "title" | "description" | "usage_html"> & {
  title_i18n: Record<"en" | "zh-CN", string>; description_i18n: Record<"en" | "zh-CN", string>;
  usage_i18n: Record<"en" | "zh-CN", string>;   // markdown source
  after_sales_hours: number | null;             // null = site default
  group_sort: number;                           // ascending within the list (default 0)
  floor_price: string; created_at: string;
};
type DeliveryInfo = { format_labels: string[]; separator: string; redeem_url: string | null; usage_html: string };

type Attachment = { id: string; url: string; mime: string; bytes: number;
  kind: "image" | "voice" | "file"; width?: number; height?: number; duration_ms?: number;
  name?: string };   // name: sanitized display name of kind=file (always ends in .txt)

type MessageType = "text" | "image" | "voice" | "file" | "product_card" | "quote_card" | "order_card" | "system";
type Message = {
  id: number; conversation_id: string; client_msg_id: string | null;
  sender: { id: string | null; role: "buyer" | "agent" | "system"; display_name: string };
  type: MessageType; text: string | null; attachment: Attachment | null;
  payload: ProductCardPayload | QuoteCardPayload | OrderCardPayload | FilePayload | SystemPayload | null;
  created_at: string;
};
type ProductCardPayload = { product_id: string; title: string; cover: string | null; price: string; currency: string };
type QuoteCardPayload = { quote_id: string; title: string; amount: string; currency: string; qty: number;
  note: string | null; status: QuoteStatus; expires_at: string; order_id: string | null };
type OrderCardPayload = { order_id: string; title: string; amount: string; currency: string; status: OrderStatus };
type FilePayload = { name: string; bytes: number; preview: string };   // preview = first 160 chars
type SystemPayload = { event: string; [k: string]: unknown };   // e.g. price_changed, delivered, agent_joined

type QuoteStatus = "offered" | "accepted" | "withdrawn" | "expired";
type Conversation = {
  id: string; buyer: { id: string; display_name: string };
  product: ProductCardPayload | null; status: "open" | "closed";
  assigned_agent: { id: string; display_name: string } | null;
  last_message: Message | null; unread: number; updated_at: string;
};

type OrderStatus = "pending_payment" | "paid" | "delivered" | "completed" | "cancelled" | "expired"
  | "refunded" | "manual_review";
type Order = {
  id: string; order_no: string; title: string; product_id: string | null; quote_id: string | null;
  qty: number; amount: string; currency: string; status: OrderStatus; delivery_mode: "auto_card" | "manual";
  channel: string | null; created_at: string; paid_at: string | null; delivered_at: string | null;
  expires_at: string | null; version: number;
  after_sales_until: string | null;   // set at delivery; null = no after-sales / not delivered yet
  auto_confirm_at: string | null;     // set at delivery; paused while an after-sales ticket is open
  after_sales: AfterSales | null;     // the most recent ticket of the order (any status)
  delivery_info: DeliveryInfo | null; // null when the order has no product
  manual_delivery: { text: string | null; attachment: Attachment | null } | null;
  can_reveal: boolean;   // auto_card && delivered/completed && owner
  // POST /orders and POST /quotes/{id}/accept only: shown ONCE, never returned again
  access_code?: string;
};
type LookupOrder = Order;   // same view; can_reveal means POST /lookup/reveal will work
type AfterSalesReason = "not_received" | "invalid_code" | "wrong_item" | "account_issue" | "other";
type AfterSalesStatus = "open" | "processing" | "replaced" | "refunded" | "rejected" | "resolved";
type AfterSales = {
  id: string; order_id: string; reason: AfterSalesReason; description: string;
  attachments: Attachment[]; status: AfterSalesStatus; resolution_note: string | null;
  created_at: string; updated_at: string; resolved_at: string | null;
  order_no?: string;   // staff views and SSE
};
type Site = {
  brand: string;
  announcement: { version: number; title: string; body_html: string; enabled: true } | null;
  after_sales_default_hours: number; support_hours: string;
  pages: { slug: string; title: string }[];
};
type ArticleItem = { slug: string; title: string; category: string; published_at: string | null;
  updated_at: string; views: number; pinned: boolean };
type Article = { slug: string; title: string; category: string; kind: "article" | "page"; body_html: string;
  published_at: string | null; updated_at: string; views: number };
type AgentStatus = { status: "online" | "away" | "on_hold" | "offline"; message: string | null; updated_at: string };
```

## Buyer / public endpoints

| Method & path | Body / query | Returns | Notes |
|---|---|---|---|
| POST `/auth/register` | `{email, password, display_name, locale, pow}` | `User` + sets cookie | **PoW (`register`).** Password ≥10 chars; Argon2id; rate-limited 5/min/IP; emails ending in `.invalid` are rejected |
| POST `/auth/instant` | `{pow}` | `{user: User, credentials: {login, password}}` + cookie | **PoW (`instant`).** One-tap buyer: handle `u-` + 7 base32 chars (lowercase `a-z2-7`), random 14-char password, `is_instant=true`. The password is returned **once** (`Cache-Control: no-store`). There is **no `email` field** (an unverified address on a throw-away account would allow email squatting); the stored email is the internal placeholder `<handle>@instant.invalid`, and the account can set a real email later via `/me/credentials`. Limits: 3/h and 10/day per IP; hard cap 200/day per /24 (IPv4) or /48 (IPv6), plus the PoW ramp above |
| POST `/auth/login` | `{email, password, pow?}` | `User` + cookie | `email` accepts an email **or a handle** (`u-xxxxxxx`, case-insensitive). Rate-limited 5/min/IP and 10/h per (login, IP); after 3 failures per (login, IP) the next attempt needs `pow` (`login` scope, `428 pow_required`); generic error `unauthorized` |
| PATCH `/me` | `{display_name?, locale?}` | `User` | Audited |
| POST `/me/credentials` | `{current_password?, email?, new_password?}` | `User` | At least one of `email` / `new_password`. `current_password` is required unless the account `is_instant`; wrong → `403 invalid_password`. Setting a real email turns an instant account into a regular one. Changing the password revokes every other session (the current one survives). 10/h/user |
| POST `/me/delete` | `{password?, confirm: "DELETE"}` | `204` | Buyers only. `password` required unless `is_instant`. `409 has_open_orders` if a `pending_payment` / `paid` / `manual_review` order or an open after-sales ticket exists. Anonymises the row (email `deleted+<id>@invalid`, name "Deleted user", unusable password hash, handle cleared, status `deleted`, closed tickets' descriptions redacted), **redacts the user's chat messages** (`text` → null, `payload = {"redacted": true}`, the attachment is dropped from the view; clients show "Message deleted"), **deletes their uploaded files** from storage (attachment rows are kept with `deleted_at`; `GET /files/{id}` → 404), revokes all sessions, audited. Audit-log rows (which include IP addresses) are retained and the privacy page says so |
| GET `/site` | `lang` | `Site` | Public. `announcement` is null unless enabled; `pages` lists published fixed pages |
| GET `/pow/challenge` | `scope=instant\|register\|lookup\|login` | `{challenge, bits, expires_at}` | See *Proof of work*. 60/min/IP |
| GET `/articles` | `category, page, page_size` | Paginated `ArticleItem` | Published `kind=article` only; pinned first, then newest |
| GET `/articles/{slug}` | — | `Article` | Published articles **and pages**; counts one view per viewer per 10 min |
| POST `/lookup` | `{order_no, access_code, pow}` | `LookupOrder` | **PoW (`lookup`).** `Cache-Control: no-store`; never creates a session. 10/h/IP (every attempt counts); the check order is per-IP limit → verify code → on failure count it per `order_no:ip` (5/h) and per order across all IPs (50/h, sized against the 50-bit code); a request with the **correct** code is never blocked by those per-order counters, only wrong codes get `429` once a counter is full; unknown order and wrong code give the same `404 not_found`. `access_code` is case-insensitive and tolerates spaces/hyphens and I/L/O confusion |
| POST `/lookup/reveal` | `{order_no, access_code}` | `{cards: [{id, code}]}` | Same authentication and per-order failure rules (30/h/IP). Goes through the delivery service with the same per-order, per-buyer (the order's buyer) and global budgets as `/orders/{id}/reveal`. `no-store`; audited |
| POST `/orders/{ref}/access-code` | — | `{access_code}` | Owner only; replaces the code (the old one stops working), returned once, audited. 10/h/user |
| POST `/orders/{ref}/after-sales` | `{reason, description, attachment_ids[]}` | `AfterSales` | Owner only; order must be `delivered`/`completed` (else `invalid_state`) and `now < after_sales_until` (else `after_sales_closed`); one open ticket per order (`after_sales_open`); ≤4 own image attachments; description 1–2000 chars. Posts an `after_sales_opened` system card into the order's conversation and an SSE `after_sales` event to staff. 10/h/user |
| POST `/auth/logout` | — | `204` | |
| GET `/auth/me` | — | `User` or `401` | |
| GET `/categories` | — | `[{key, name}]` | localized |
| GET `/products` | `q, category, page, page_size` | Paginated `ProductSummary` | `q` uses PG full-text + trigram on title/description. Ordered by `group_sort`, then sold |
| GET `/products?ids=a,b,c` | ≤20 UUIDs | `{items: ProductSummary[]}` | Keeps the requested order; unknown, draft and cross-tenant ids are skipped; invalid UUID / more than 20 → `422` |
| GET `/products/recommended` | `for?, recent?, limit=8 (1–20)` | `{items: ProductSummary[]}` | score = 3·[same category as `for`/`recent`] + 2·co-purchase(90 d) + ln(1+sold_30d) + 0.5·[created < 14 d]. Excludes drafts, out-of-stock and the `for` item. Cached 5 min in Redis per input |
| GET `/products/{id}` | — | `Product` | |
| GET `/support/status` | — | `AgentStatus` | Best status across agents |
| POST `/conversations` | `{product_id?: string}` | `Conversation` | Find-or-create the buyer's open conversation. If `product_id` is given and differs from the last product card, append a `product_card` message |
| GET `/conversations` | — | `Conversation[]` | Only the buyer's own |
| GET `/conversations/{id}/messages` | `after_id?, before_id?, limit≤100` | `Message[]` ascending | Participants only |
| POST `/conversations/{id}/messages` | `{client_msg_id, type: "text"\|"image"\|"voice"\|"file", text?, attachment_id?}` | `Message` | Idempotent on `(sender, client_msg_id)`; text ≤2000 chars; 30/min/user. `type=file` needs a `kind=file` attachment of the sender; the message `payload` is `{name, bytes, preview}` and `text` is null |
| POST `/conversations/{id}/read` | `{message_id}` | `204` | |
| POST `/uploads` | multipart `file`, `kind=image\|voice\|file` | `Attachment` | Image ≤8 MB (jpeg/png/webp/gif first frame), re-encoded, EXIF stripped. Voice ≤1 MB (webm/ogg/mp4/mpeg magic bytes) plus `duration_ms` form field ≤60000. **`kind=file`**: plain text only — valid UTF-8, no NUL, ≤256 KB; stored as `text/plain`; `name` = sanitized basename forced to `.txt` (≤100 chars) |
| GET `/files/{id}` | — | bytes | Auth: owner, conversation participant or staff. `nosniff`, `CSP: sandbox`. Images are `inline`; voice and text files are `attachment` (text: `text/plain; charset=utf-8`, `filename*=UTF-8''<name>`). Product images are public |
| GET `/stream` | — | `text/event-stream` | Events: `message` (Message), `conversation` (Conversation), `order` (Order), `agent_status` (AgentStatus), `after_sales` (AfterSales, own tickets). Heartbeat comment every 15 s. Clients fall back to polling `messages?after_id` every 4 s. `order` events are also emitted when an order auto-completes |
| POST `/quotes/{id}/accept` | — | `Order` + `access_code` | Buyer of that conversation only; status `offered` and not expired; creates a `pending_payment` order with `amount = quote.amount`; posts an `order_card`; quote becomes `accepted` |
| POST `/orders` | `{product_id, qty, src?}` | `Order` + `access_code` | Buy-now at list price; `qty` 1–10; for `auto_card`, checks available (non-expired) stock. For `manual` products with a finite `manual_stock` the quantity is reserved (decremented under a product row lock) and `409 out_of_stock` is returned when insufficient; cancel/expiry give it back once, paid orders keep it (also applies to `/checkout` and quote accept). `src` is a traffic tag stored as `orders.source`; it is lowercased and **silently dropped** unless it matches `[a-z0-9_-]{1,32}`. `access_code` (10 Crockford base32 chars) is returned **only here** (and on quote accept / rotation) |
| GET `/orders` | `page, page_size` | Paginated `Order` | Own orders only |
| GET `/orders/{id}` | — | `Order` | Owner only |
| POST `/orders/{id}/pay` | `{channel: "zpay_alipay"\|"usdt_arbitrum"\|"usdt_xlayer"\|"usdt_trc20"}` | `{pay_url}` | Only `pending_payment`; `zpay_alipay` builds a signed `submit.php` URL; a `usdt_*` channel opens a BEpusdt transaction and returns our own page `/pay/usdt/{order_no}` (same-origin). Disabled channel (USDT is off by default) or BEpusdt not configured -> 503 `unavailable` |
| POST `/orders/{id}/reveal` | — | `{secrets: string[], cards: [{id, code}]}` | Proxied to the delivery service; owner + `can_reveal`; 10/h/order; audited. Voided (replaced/defective) cards are never returned |
| POST `/orders/{id}/confirm` | — | `Order` | `delivered` → `completed` |
| GET `/pay/notify/zpay` | ZPay query params | `text/plain` `success` \| `fail` | Full pipeline (architecture §7). POST also accepted. A notify for an already `refunded` order with the same `trade_no` as its payment is a benign duplicate (`success`, no state change, no critical audit); a different `trade_no` stays a critical `payment.unexpected_state` audit |
| POST `/pay/notify/bepusdt` | BEpusdt JSON body (`trade_id, order_id, amount, actual_amount, token, block_transaction_id, status, signature, ...`) | `text/plain` `ok` \| `fail` | USDT notify (internal network). Pipeline: pinned-MD5 signature (constant time) -> `order_id` is this tenant's order/checkout -> `status` (1/3: `ok`, no change) -> `amount` (CNY) exact Decimal == order amount (else manual review) -> server-to-server `POST {BEPUSDT_URL}/api/v1/pay/info` must confirm status 2, same `order_id`, `money`, `fiat=CNY`, network == order channel -> row lock + state machine + `UNIQUE(channel, trade_id)` + outbox. `ok` only for processed or idempotent duplicates; `fail` makes BEpusdt retry. Exempt from CSRF and the global rate limit |
| GET `/pay/usdt/{no}` | — | `UsdtPayView` | Owner of that order (`G...`) or checkout (`C...`) only, else 404. `{channel, label, network, address, usdt_amount, fiat_amount_minor, currency, expires_at, seconds_left, expired, status, qr_svg, sim_url?}`; `usdt_amount` is the exact amount to send, `status` is the order/checkout status (the page polls it), `qr_svg` is a server-generated QR of the address (render through an `<img>` data URI), `sim_url` only in dev/demo. 404 when no live attempt exists (press pay again) |
| GET `/pay/return/zpay` | ZPay query params | `302` → `/orders/{out_trade_no}` | **Display only**, never fulfils |

## Markdown fields

All `*_html` fields (articles, announcement, product usage) are rendered server-side from markdown with raw HTML disabled and an `nh3` allowlist. `<img src>` is limited to same-origin `/api/v1/files/<uuid>`; external image URLs are dropped (no IP leak or tracking beacon). Links are `http(s)`/`mailto` with `rel="noopener noreferrer nofollow"`.

**Redacted messages.** After the author deletes their account, `Message.payload` is `{"redacted": true}`, `text` is null and `attachment` is null; `type` is unchanged.

## Admin endpoints (`/api/v1/admin/*`, role `agent` or `admin`)

| Method & path | Body / query | Returns | Role |
|---|---|---|---|
| GET `/admin/stats` | — | `{orders_today, revenue_today, pending_payment, awaiting_manual, open_conversations, low_stock: ProductSummary[]}` | agent |
| GET `/admin/products` | `q, status, page` | Paginated `AdminProduct` | agent |
| POST `/admin/products` | `AdminProduct` fields (no id); optional `delivery_format`, `delivery_separator`, `redeem_url`, `usage_i18n`, `after_sales_hours`, `group_sort` | `AdminProduct` | admin. `redeem_url` must be `https://` (no userinfo) or a `/relative` path, ≤500 chars. `usage_i18n` markdown is sanitized to `usage_html` |
| PATCH `/admin/products/{id}` | partial | `AdminProduct` | admin |
| DELETE `/admin/products/{id}` | — | `204` (soft delete → draft) | admin |
| POST `/admin/products/{id}/images` | multipart `file` | `{url}` | admin |
| POST `/admin/products/{id}/cards/import` | `{lines: string, note?, expires_at?}` (one code per line) | `{imported, duplicates, rejected, batch_id}` | **admin only**; ≤5000 lines/request; sealed immediately; audited. Creates a batch (`batch_id` is null when nothing new was imported). `expires_at` = ISO datetime or a bare `YYYY-MM-DD` ("valid through that day", UTC), must be in the future. A line may end in `\|exp=YYYY-MM-DD` (overrides the batch expiry); the suffix is stripped **before** sealing and fingerprinting, an invalid date rejects the line |
| GET `/admin/products/{id}/cards` | `status, batch_id, expiring_days, order_no, page` | Paginated `CardRow` | admin; plaintext never returned. `expiring_days=N` = available cards expiring within N days |
| GET `/admin/products/{id}/batches` | — | `{items: BatchRow[]}` | admin. `BatchRow = {id, note, count, expires_at, created_by, created_at, available, reserved, sold, void, expiring_7d}` |
| POST `/admin/cards/find` | `{lines: string[] (1–100), by: "content"\|"first_field"}` | `{items: FoundCard[], missing}` | admin. Matches by keyed fingerprint (content, `\|exp=` suffix ignored) or by `lookup_fp` = HMAC(fp_key, tenant ‖ 0x1e ‖ casefold(first field)) — the line **is** the first field. `FoundCard = CardRow & {line, product_id, product_title_i18n}`; `missing` = lines without a match. Never decrypts. 120/h. Rate limit 30/h per staff member. Audited as `cards.find` with counts only (`lines`, `found`, `missing`, `mode`), never the pasted values |
| POST `/admin/cards/void` | `{ids[] \| batch_id, reason}` | `{voided}` | admin. Exactly one of `ids` (≤1000) / `batch_id`; only `available`/`reserved` cards are voided (`void_reason`=`reason` ≤64 chars). Audited |
| POST `/admin/cards/{id}/replace` | `{code}` | `CardRow` (the new card) | admin. Seals a new card (same product, batch and expiry unless `\|exp=` given) and voids the old with `void_reason=replaced`, `replaced_by`. Only `available`/`reserved` (`409 conflict` otherwise; duplicate → `409`) |
| GET/PUT `/admin/site` | PUT: `{announcement?: {enabled?, title_i18n?, body_i18n?}, after_sales_default_hours?, auto_confirm_hours?: {manual, auto_card}, support_hours?: {en, zh-CN}}` | `{announcement: {version, enabled, title_i18n, body_i18n, body_html_i18n}, after_sales_default_hours, auto_confirm_hours, support_hours, defaults}` | **admin only**; partial update. `announcement.version` increases when the title or body changes or when it is re-enabled; body markdown is sanitized to `body_html_i18n`. Audited |
| GET `/admin/articles` | `status, kind, category, q, page` | Paginated `AdminArticle` | **admin only** |
| POST `/admin/articles` | `{slug, kind?, category?, title_i18n, body_i18n?, status?, pinned?}` | `AdminArticle` | slug `[a-z0-9][a-z0-9-]{0,62}`, unique per tenant (`409`); body is markdown, rendered + sanitized server-side. `AdminArticle = {id, slug, kind, category, title_i18n, body_i18n, body_html_i18n, status, pinned, views, published_at, created_at, updated_at}` |
| GET / PATCH / DELETE `/admin/articles/{id}` | PATCH: any create field | `AdminArticle` / `204` | Fixed pages (`kind=page`) cannot be deleted (`422`). Audited |
| GET `/admin/after-sales` | `status, page` | Paginated `StaffAfterSales` | staff. `StaffAfterSales = AfterSales & {order: {id, order_no, title, status, delivery_mode, qty, amount, currency, delivered_at, after_sales_until}, buyer: {id, display_name, email}, resolved_by}` |
| GET `/admin/after-sales/{id}` | — | `StaffAfterSales` | staff |
| POST `/admin/after-sales/{id}/action` | `{action: "processing"\|"replace"\|"refund"\|"reject"\|"resolve", note?, delivery_text?, card_ids?}` | `StaffAfterSales` | staff; **`refund` is admin only** (`403` for agents; also enforced inside the service). From the 3rd `replaced` ticket of an order on, `replace` is admin only too (`403 forbidden`). Open or processing tickets only (`invalid_state`). `reject` needs `note`. `replace` on a manual order needs `delivery_text` (replaces the order's delivery text; ticket → `replaced`). `replace` on an auto_card order enqueues outbox `delivery.replace` and the ticket becomes `processing`; the delivery worker voids the reported cards (`card_ids`, default all of the order's current, non-void cards; ids that are not one of those → `422 validation`, as is an order with no replaceable cards) with `void_reason=defective`, claims new ones for the same order, writes new delivery rows and sets the ticket `replaced` (no stock → ticket stays `processing`, audit `delivery.replace_out_of_stock`; nothing replaceable when the worker runs → audit `delivery.replace_no_cards` plus an `after_sales_replace_failed` system message). The outbox payload carries the staff `actor_id`/`actor_role`: the worker writes them to the `aftersales.replaced` audit row and sets `resolved_by`/`resolved_at`. `refund` marks the order `refunded` and posts a balanced reversal ledger transaction (money goes back through the Alipay console in MVP). Closing a ticket sets `order.auto_confirm_at = max(old, now + 24 h)`. Every action is audited and posts a system message into the order conversation |
| GET `/admin/orders` | `status, q, page` | Paginated `Order & {buyer: {id, email, display_name}, source}` | agent |
| GET `/admin/orders/{id}` | — | same + `events: [{from, to, actor, reason, at}]` | agent |
| PATCH `/admin/orders/{id}/price` | `{amount, reason}` | `Order` | agent; only `pending_payment`; `amount ≥ floor_price × qty` if a product is linked; `version++`; audited; posts a `system` message `price_changed` |
| POST `/admin/orders/{id}/deliver` | `{text, attachment_id?}` | `Order` | agent; manual orders in `paid` → `delivered` |
| POST `/admin/orders/{id}/cancel` | `{reason}` | `Order` | agent; only `pending_payment` |
| GET `/admin/conversations` | `filter=all\|mine\|unassigned, status=open\|closed, page` | Paginated `Conversation` | agent |
| GET `/admin/conversations/{id}/messages` | same as buyer | `Message[]` | agent |
| POST `/admin/conversations/{id}/messages` | same as buyer | `Message` (sender role `agent`) | agent |
| POST `/admin/conversations/{id}/assign` | `{agent_id: string\|null}` | `Conversation` | agent |
| POST `/admin/conversations/{id}/close` | — | `Conversation` | agent |
| POST `/admin/conversations/{id}/quotes` | `{title, amount, qty, note?, product_id?, expires_in_minutes (5–1440)}` | `QuoteCardPayload` + posts a `quote_card` | agent |
| POST `/admin/quotes/{id}/withdraw` | — | `QuoteCardPayload` | agent |
| GET `/admin/agent/status` / PUT `/admin/agent/status` | `{status, message?}` | `AgentStatus` | agent (own status) |
| GET `/admin/agents` | — | `[{id, display_name, status}]` | agent |
| GET `/admin/stream` | — | SSE: all conversation, message, order and `after_sales` (StaffAfterSales) events for the tenant | agent |
| GET `/admin/ledger` | `page` | Paginated `{txn_id, at, account, amount, currency, memo, order_id}` | admin |
| GET `/admin/audit` | `page` | Paginated `{at, actor, action, target, ip, detail}` | admin |

## Commerce v4 additions (cart, channel fee, cancel, order information, rate limit)

Spec: [review-v4.md](review-v4.md). These rows extend or override the tables above.

**Error envelope.** Machine-readable extras are echoed flat inside `error` and mirrored in `error.data`: `{"error": {"code": "pay_via_checkout", "message": "...", "checkout_no": "C...", "data": {"checkout_no": "C..."}}}`. New codes: `fee_channel_mismatch` 409, `pay_via_checkout` 409 `{checkout_no}`, `already_paid` 409, `buyer_input_required` 422, `out_of_stock` 409 `{product_id}`.

**Money.** `amount = subtotal + fee`, `fee = ceil(subtotal * permille / 1000)` (integer maths), computed by the server at order creation with the channel rate in `tenants.settings.payments.channels` (default `zpay_alipay` enabled at 30 permille; 0-200). `orders.fee_permille` records the applied rate.

**OrderView additions:** `subtotal, fee, fee_rate ("3%"), checkout_no|null, buyer_input|null (owner and staff only), cancel_reason|null`.
**Product additions:** `description_html` (markdown rendered at write time), `buyer_input` (public config resolved to the request locale, or `null` when the popup is off): `{enabled, required, multiline, max_len, title, prompt, placeholder}`; an empty title falls back to "Order information" / 「下单信息」.
**AdminProduct addition:** `buyer_input` in the i18n form `{enabled, required, multiline, max_len (1-4000), title_i18n, prompt_i18n, placeholder_i18n}` (each text <= 1000 chars) or `null`; PATCH with `null` clears it.

| Method & path | Body / query | Returns | Notes |
|---|---|---|---|
| GET `/site/payment-channels` | `lang` | `[{id, label, fee_rate, permille}]` | Enabled channels only. Shown, never trusted |
| GET `/site` | | + `support: {welcome, quick_questions[]}` | Resolved to the locale (quick questions fall back to `en` when empty) |
| POST `/cart/quote` | `{items: [{product_id, qty (1-10), buyer_input?}]}` (1-20) | `{lines: [{product_id, title, cover, unit_price, currency, stock, available, max_qty, needs_input, input_missing, qty, line_subtotal}], subtotal, fee, fee_rate, total, currency}` | No login. Unavailable lines (unknown/draft product, `qty > max_qty`) are flagged `available:false` and left out of the totals. `needs_input` = popup enabled and required; `input_missing` (extra) = required but the request line carried no value. `fee` is the sum of per-line ceilings, i.e. exactly what checkout charges |
| POST `/checkout` | `{items: [{product_id, qty, buyer_input?}], source?}` | `{checkout: CheckoutView, orders: OrderView[], access_codes: {order_no: code}}` | One order per line + one `checkouts` row, one transaction. 409 `out_of_stock` `{product_id}` (stock summed per product), 422 mixed currency / `buyer_input_required`, 404 unknown/draft product, 409 when the open-payables cap (pending single orders + pending checkouts) is reached. Access codes are returned once |
| GET `/checkouts/{no}` | | `CheckoutView` | Owner only (404 otherwise) |
| POST `/checkouts/{no}/pay` | `{channel}` | `{pay_url}` | `out_trade_no = checkout_no`, `money = checkout.amount`; checkout and every order must be `pending_payment` and unexpired (else 409 `invalid_state`) |
| POST `/checkouts/{no}/cancel` | `{reason?}` | `CheckoutView` | See *Cancel* |
| POST `/orders/{ref}/pay` | | | An order with a `checkout_id` gets 409 `pay_via_checkout` `{checkout_no}`; a channel whose rate differs from `order.fee_permille` re-applies that channel's fee server-side while the order is `pending_payment` and unpaid (audited `order.fee_recomputed`, `version`++, buyer system message `price_changed` if the amount changed; a previous USDT attempt is dropped, an old pay URL's notify lands in manual review via the amount-mismatch path); 409 `invalid_state` once a payment was recorded or the order is not pending (`fee_channel_mismatch` is no longer raised); a disabled channel 503. Same for `POST /checkouts/{no}/pay` |
| POST `/orders` | + `buyer_input?` | | Validated against the product config; ignored when the product has no popup |
| POST `/orders/{ref}/cancel` | `{reason?}` (<= 200) | `OrderView` | See *Cancel* |
| PATCH `/orders/{ref}/input` | `{buyer_input}` | `OrderView` | Owner; `pending_payment` or `paid`; if `paid` a new buyer chat message "(updated)" is posted |

`CheckoutView = {checkout_no, status (pending_payment|paid|cancelled|expired|manual_review), amount, currency, expires_at, order_nos[], subtotal, fee, fee_rate, channel, paid_at, created_at}`.

**Cancel.** Only `pending_payment`. An order inside a checkout cancels the WHOLE checkout. If a pay URL was ever issued (`channel` set) the gateway is re-queried FIRST (no row locks held): paid -> settled through the normal settlement path and the call answers 409 `already_paid`; query failure -> 503 (retry). Effects: orders `cancelled` + `cancel_reason`, order events, audit, system message `order_cancelled` (`payload.by = "buyer"|"staff"`, text "Order cancelled by buyer" / "Order closed by support: <reason>"). A later notify still records the money and moves the order to `manual_review`.

**Buyer input.** Trimmed, control characters other than `\n\t` stripped, `max_len` enforced (422), required empty -> 422 `buyer_input_required`. Stored in `orders.buyer_input` (plaintext behind RLS, MVP; a follow-up issue tracks encryption at rest). Never logged, never in audit rows (they record `{"buyer_input": true}`). After a successful (non-late) payment a `text` message from the BUYER (`📝 Order G... · <title>\n<value>`, truncated to 2000 chars) is posted into the buyer's open conversation (created if needed), idempotent per order.

**Notify.** `out_trade_no` starting with `C` takes the checkout path with the same pipeline (signature, pid, exact amount vs `checkout.amount_minor`, status, re-query, replay guard), then one `Payment(checkout_id)` and the same per-order transition for each member. If the checkout itself is expired/cancelled every member is late (`manual_review`, money recorded, nothing fulfilled).

**Ledger per payment:** `gateway_clearing:zpay +amount` (`gateway_clearing:bepusdt` for `usdt_*` channels, memo `bepusdt payment ...`, no gateway-cost entry), `sales_revenue -subtotal`, `surcharge_revenue -fee` (fee > 0) plus the unchanged gateway-cost entry. Refund reversal: `sales_revenue +subtotal`, `surcharge_revenue +fee`, `gateway_clearing:zpay -amount`.

| Admin method & path | Body | Returns | Notes |
|---|---|---|---|
| GET/PUT `/admin/site` | + `payments: {channels: {zpay_alipay: {enabled, surcharge_permille (0-200)}}}`, `support: {welcome_i18n: {en, zh-CN}, quick_questions_i18n: {en: string[], zh-CN: string[]}}` (<= 6 each, <= 60 chars), `rate_limit: {enabled, limit (10-10000), window_s (1-3600)}` | same keys | Partial update. `rate_limit` is also mirrored to Redis `cfg:ratelimit:<tenant_id>` |
| GET `/admin/orders` | `q` also matches `checkout_no` | rows + `subtotal, fee, fee_rate, checkout_no, buyer_input, cancel_reason` | |
| GET `/admin/orders/{id}` | | + `checkout_orders: [{id, order_no, title, amount, status}]` (when in a checkout), `payment: {channel, trade_no, amount, currency, scope: "order"|"checkout", at}|null` | |
| PATCH `/admin/orders/{id}/price` | `{amount, reason, remove_markup?: false}` | `Order` | `amount` is the new SUBTOTAL; the fee is recomputed at the order's rate. v5.1: allowed on promo / reseller-markup orders: the promo is released (discount 0) and the markup becomes `min(old, subtotal - floor*qty)` (>=0), or 0 with `remove_markup`. Errors: 409 `reprice_checkout_member`, 409 `reprice_not_goods`, 409 `invalid_state` (not pending), 422 `reprice_below_floor`; audit detail carries old/new discount and markup |
| POST `/admin/orders/{id}/cancel` | `{reason}` (required, 1-500) | `Order` | Same rules as the buyer cancel (whole checkout, gateway re-query first) |
| POST `/admin/conversations/{id}/messages` | `{client_msg_id, type: "product_card", product_id}` | `Message` | Staff only (buyers get 422). Payload built by the server from the active product of the same tenant (404 otherwise) |
| POST `/admin/content/images` | multipart `file` | `{id, url: "/api/v1/files/<id>"}` | Staff. Same pipeline as every image (magic bytes, re-encode, EXIF strip, 8 MB); stored as a public attachment |

**Global access-frequency limit** (default OFF): per-IP fixed window on `/api/*`, except the notify path, `/api/v1/realtime/*`, `/api/v1/admin/*` and non-`/api` paths (`/healthz`). Over the limit: HTTP 429, `Retry-After`, body exactly `{"code":429,"msg":"Access is too frequent. Please try again later."}` (not the error envelope). Fails open on Redis errors; config cached in-process 5 s.

## Product import (v5, admin only, 30 req/min per admin)

- `POST /admin/products/import/fetch` `{url}` -> `ImportDraft`. Server-side SSRF-safe fetch
  (`app/core/safefetch.py`: http/https, ports 80/443/8080/8443, every resolved IP must be public,
  connection pinned to the vetted IP, redirects <= 3 re-vetted, 10 s, 3 MB). Audited as
  `product.import_fetch` with `target = host:<hostname>` only. Refusals: 422 `unsafe_url` /
  `fetch_failed`; 429 when rate limited.
- `POST /admin/products/import/parse` `{text?, html?, base_url?}` -> `ImportDraft` (422 when both empty).
- `POST /admin/content/images/fetch` `{url}` -> `{id, url}` (same as `POST /admin/content/images`;
  8 MB cap, then the normal magic-byte / re-encode / EXIF-strip pipeline).
- `ImportDraft = {title, description_md, images:[{url, alt}], price_minor|null, currency, source_url|null,
  site|null, suggested_price_minor|null, warnings:[code]}`. `suggested_price_minor` =
  round-up(cost + markup, `import.round_to_minor`) from settings section `import`.
  Warning codes: `no_title`, `no_price`, `no_description`, `no_images`, `paste_page` (client-rendered
  site: ask the admin to paste the page instead). Image URLs in the draft are remote; the UI
  uploads the chosen ones via `images/fetch` and rewrites the description.
- Adapters: gmailbuy.com, maitg.com (server-rendered), go.ka8.ai (public JSON API
  `/api/v1/public/products/<slug>`; falls back to `paste_page` if the site blocks the server).

## Internal (never routed by Caddy)

| Service | Endpoint | Purpose |
|---|---|---|
| api `:8081` (loopback/internal listener) | GET `/internal/tls/ask?domain=` | Caddy on-demand TLS permission: `200` only for verified active `custom_domains` |
| delivery `:8001` | POST `/internal/reveal` `{order_id, buyer_id}` + header `X-Internal-Token` | Decrypt delivered codes for the owner |
| mockpay `:8002` | `/submit.php`, `/api.php?act=order` | ZPay simulator (dev/demo only) |

## SSE event format

```
event: message
data: {"conversation_id":"…","message":{…Message}}

event: order
data: {…Order}
```

## System message events (v3 additions)

`payload.event` values that the client translates (English fallback text is always present): `after_sales_opened`, `after_sales_replaced`, `after_sales_refunded`, `after_sales_rejected`, `after_sales_resolved`, `after_sales_replace_failed` (each with `order_id`, `after_sales_id`; `note` where given). `delivered` always carries `order_id`; for manual delivery it also carries `delivered_at` (ISO). `completed` carries `auto: true` when the order was auto-confirmed.

## Background jobs (API expiry loop, every `APP_EXPIRY_INTERVAL_S` = 60 s)

- Every minute: expire unpaid orders; auto-complete `delivered` orders past `auto_confirm_at` unless a ticket is `open`/`processing`.
- Hourly: void expired `available` cards (`void_reason=expired`); anonymise instant accounts older than 7 days without a paid order.
- Delivery worker: `order.paid` (claims only non-expired stock; sets `after_sales_until` and `auto_confirm_at`) and `delivery.replace`.

## Configuration added in v3

| Variable | Default | Purpose |
|---|---|---|
| `APP_POW_KEY` | dev: derived | base64 ≥32 bytes, signs PoW challenges. **Required in prod** (generated by `deploy/bootstrap.sh`) |
| `APP_LOOKUP_KEY` | dev: derived from the PoW key | base64 ≥32 bytes, HMAC key of order access codes. **Required in prod** (the API fails fast at startup) |
| `APP_POW_BITS` / `APP_POW_TTL_S` | 18 / 120 | difficulty / challenge lifetime |
| `APP_BRAND_NAME` | `Kesiv` | `Site.brand`, seed tenant name |
| `APP_INSTANT_PER_IP_HOUR` / `_DAY` / `APP_INSTANT_PER_NET_DAY` | 3 / 10 / 200 | instant-account limits (net = hard cap per /24 or /48) |
| `APP_INSTANT_RAMP_1` / `_2` | 30 / 100 | instant accounts today on the net above which PoW gets +4 / +6 bits |
| `APP_LOOKUP_FAILURES_PER_ORDER_HOUR` / `APP_LOOKUP_FAILURES_GLOBAL_ORDER_HOUR` | 5 / 50 | wrong codes per (order, IP) / per order per hour |
| `APP_MAX_PENDING_ORDERS_INSTANT` | 2 | unpaid orders per instant account (registered: `APP_MAX_PENDING_ORDERS`=5) |
| `APP_INSTANT_PURGE_DAYS` | 7 | purge window for unpaid instant accounts |
| `APP_LOOKUP_PER_IP_HOUR` | 10 | `/lookup` attempts per IP |

## v5 core additions (settings center, categories, archive, security)

Errors as always `{error:{code,message}}`. New codes: `feature_disabled` (403), `turnstile_failed` (400), `invalid_code` (400), `terms_required` (422), `category_in_use` (409), `product_in_use` (409), `oauth_failed` (400).

**Public**
- `GET /site` adds `features{registration,guest_checkout,cart,promo_codes,resellers,voice_messages,lookup,google_login}`, `general{site_name,subtitle,site_name_i18n,logo_url,contact,footer}`, `legal{enabled,mode,updated_at,pages[{slug,title}]}`, `turnstile{enabled,site_key,actions}` (enabled only when a secret is configured), `google_login:bool`, `delivery{manual_eta_hours,manual_note}` (`{hours}` substituted client-side). `storefront` is added by the reseller work.
- `GET /catalog/categories` (alias `/categories`) -> `[{slug,key,name,icon}]`, enabled only. Products of a disabled category are hidden from list/search/recommended; their page still loads.
- Product payloads: `delivery_eta_hours` (manual only; product value else site default). Never `cost_minor`/`source_url`.
- Order payloads: `delivery_eta_hours`, `delivery_deadline` (manual; `paid_at + eta`, null until paid).
- `POST /cart/quote` lines add `unavailable_reason: archived|unpublished|out_of_stock|null`.
- `POST /auth/login|register|instant` accept `turnstile_token`; `POST /lookup` takes header `X-Turnstile-Token` (each token is single use; request a fresh one per call).
- `POST /auth/register` also `email_code` (required when `security.email_verify_on_register`) and `agree_terms` (required when `legal.enabled`); 403 `feature_disabled` when registration is off.
- `POST /auth/email-code` `{email, purpose: register|reset, turnstile_token?}` -> 204 always (403 `feature_disabled` when email is not enabled). Turnstile action: `register` / `password_reset`.
- `POST /auth/password-reset` `{email, code, new_password}` -> 204; revokes all sessions. Buyers only. No Turnstile on this step (the code is the proof).
- `GET /auth/oauth/google/start?next=/path` -> 302 Google; `GET /auth/oauth/google/callback` -> 302 `next` (relative only) with session cookie. Staff accounts: 403.

**Admin** (admin role unless noted)
- `GET/POST /admin/categories`, `PATCH/DELETE /admin/categories/{id}`; body `{slug, name_i18n{en,zh-CN}, icon?, sort?, enabled?}`; slug immutable; list is staff-readable and includes `products` count.
- `DELETE /admin/products/{id}` archives (204); `?hard=true` deletes if unreferenced else 409 `product_in_use`; `POST /admin/products/{id}/restore` -> draft view; `POST /admin/products/bulk` `{ids<=200, action: publish|unpublish|archive|restore|set_category, category?}` -> `{updated}`. List `status=archived`; default list excludes archived. Product create/patch/views add `delivery_eta_hours` (1-720, null = site default), `cost_minor`, `source_url` (http/https), view also `archived_at`.
- `GET /admin/site` returns every section: `general, legal, features, security, defaults, email, delivery, import, resellers, audit, email_templates`, plus `secrets{turnstile_secret|google_client_secret|smtp_password: {configured, last4}}` and `zpay{configured, pid_last4}`. `defaults` = v5 user defaults (`locale, max_pending_orders, order_timeout_minutes`) plus the legacy factory-default keys. `PUT /admin/site` takes any subset of the sections (each field optional; `import` is the JSON key of the import section).
- `security.staff_ip_allowlist: string[]` (v5.2): IPs or CIDRs, max 50, invalid entry -> 422. A request whose trusted-proxy client IP matches skips Turnstile and the global rate limit (mirrored to Redis `cfg:ratelimit:<tenant>` as `staff_ips` on save). Over the global limit, a request with a valid staff (agent/admin) session cookie is also let through. The admin login sends `turnstile_token` when `/site` reports `login` protected.
- `PUT /admin/site/secrets` `{name, value|null}` -> `{secrets}`; write-only.
- `POST /admin/site/email/test` `{to, template?: verify_code|password_reset|order_paid|order_delivered, locale?}` -> `{ok, error?}` (`error` in connect_failed|auth_failed|rejected|send_failed).
- Secret name `telegram_bot_token` is accepted by `PUT /admin/site/secrets` (write-only, `last4` only).
- Settings section `notifications` (GET/PUT `/admin/site`, admin only; not in the public site): `telegram_enabled` (bool), `telegram_chat_ids` (<=10 integer strings, 422 otherwise), `event_order_paid|event_chat_message|event_after_sales|event_payment_review|event_reseller_application|event_low_stock` (bool, default true).
- `POST /admin/site/telegram/test` (admin only, 5 per 10 min, audited) -> `{ok, error?}` (`error` in no_token|no_chat|send_failed). Ignores the enable/event toggles.
- Telegram messages are sent off the request path, never raise, and carry order no, title, amount, admin link only (buyer email masked, no card secrets). Throttles: chat 5 min per conversation, low stock (<3) 1 h per product, order/payment/after-sales/reseller events once per entity.
- Email templates: `email_templates.<kind> = {subject_i18n:{en,zh-CN}, body_i18n:{en,zh-CN}}`; `PUT` partial; an empty string resets that locale to the built-in default. Placeholders `{site_name} {code} {minutes} {order_no} {order_url} {title} {amount}`; unknown ones stay literal. Bodies are plain text/markdown (HTML part sanitized).
- `GET /admin/audit` + `action` (prefix), `actor` (email substring), `target`, `severity`, `since`, `until`; items add `id, severity, before, after`. `GET /admin/audit.csv` (same filters, <= 10 000 rows).
- `GET /admin/orders?overdue_manual=true`.

**Jobs:** order emails (`email.order` outbox rows, paid + delivered, verified non-instant buyers, never card secrets) and the daily audit retention purge run in the API jobs loop.


## Commerce v5 additions (promo codes, resellers) — backend `feat/v5-be-com`

Authoritative spec: [review-v5.md](review-v5.md) §6, §7, §12. Errors use the usual envelope.

**Promo codes.** `POST /promo/validate` `{code, items:[{product_id, qty}], turnstile_token?}` ->
`{code, discount_minor, eligible_subtotal_minor, description}`; 20/min per user/IP. 422 codes:
`promo_invalid | promo_not_started | promo_expired | promo_exhausted | promo_user_limit |
promo_min_subtotal | promo_not_applicable`. `promo_code?` is accepted by `POST /orders`,
`POST /cart/quote` (never errors: result in `promo: null | {code, discount_minor} | {error}`) and
`POST /checkout`. Quote adds `discount`, `discount_minor`, per-line `discount_minor`, `promo`;
`total = subtotal - discount + fee` (fee per line on the discounted line). Order and checkout
views add `kind`, `discount`, `discount_minor`, `promo_code`, `reseller` (`null | {store_name}`;
staff also `slug`); checkout view adds `discount`, `discount_minor`. Promo is `promo_not_applicable`
on reseller storefronts and quote orders. Toggles: `features.promo_codes`, `features.cart`
(403 `feature_disabled`).

Admin (admin role): `GET/POST /admin/promo-codes`, `GET /admin/promo-codes/generate` -> `{code}`,
`PATCH /admin/promo-codes/{id}` (code immutable), `GET /admin/promo-codes/{id}/redemptions`.
Create body: `{code?, kind: percent|fixed, value (percent = permille), min_subtotal_minor,
max_discount_minor?, starts_at?, ends_at?, usage_limit?, per_user_limit, product_ids,
category_slugs, enabled, note?}`.

**Storefront.** Resolved from Host `{slug}.{resellers.base_domain}` else `X-Storefront: {slug}`
(only when `features.resellers`; non-active slug = main store). Catalog list/detail/ids/
recommended, cart quote, `POST /orders`, `POST /checkout` use storefront prices (base + markup;
hidden products 404). `GET /site` adds `storefront: null | {slug, store_name, logo_url, welcome,
white_label: true}`, overrides `brand` with the store name, and `resellers: {base_domain, link_mode}` when the
feature is on. New conversations record `reseller_id`.

**Reseller center** (`features.resellers`, else 403): `GET /reseller/me` (`null` if none),
`POST /reseller/apply` `{slug, store_name, contact?, turnstile_token?}` -> me view +
`signup_order` (order view; pay with `POST /orders/{no}/pay`) + `order_no`; errors `slug_taken`,
`slug_reserved`, `reseller_exists`. `PATCH /reseller/me`; `GET /reseller/products?q&page` (items
`{id, product_id, title, cover, base_price(_minor), markup_kind, markup_value, markup_minor,
price(_minor), hidden, custom}`); `PUT /reseller/products/{id}` `{markup_kind?, markup_value?,
hidden?}` and `DELETE` (204; 422 `markup_too_high`); `GET /reseller/orders?page`
(`{order_no, title, qty, amount, amount_minor, markup_minor, status, created_at, buyer(masked)}`);
`GET /reseller/stats` (`today/last_7_days/total {orders, earnings_minor}`, `balances`, plus flat
`today_orders, today_earnings_minor, week_orders, week_earnings_minor`); `GET/POST
/reseller/payouts` (`{amount_minor}`; errors `payout_account_required`, `payout_below_minimum`,
`payout_open`, `insufficient_balance`).

Admin: `GET /admin/resellers?status&q&page`, `GET/PATCH /admin/resellers/{id}` `{status?:
active|suspended, markup_kind?, markup_value?}`, `GET /admin/payouts?status`, `POST
/admin/payouts/{id}/decide` `{action: paid|rejected, note}`; `GET /admin/orders?reseller_id=` and
admin order views gain `reseller_markup_minor`, `kind`, `discount_minor`, `promo_code`, `reseller`.

**Custom domains (v5.2, Cloudflare for SaaS).** `GET /reseller/domains` ->
`{enabled, fallback_origin, max_domains, items[{id, hostname, status: pending|active|failed,
ssl_status, verification{ownership, ssl_records[]}, created_at, verified_at}]}`; `POST
/reseller/domains {hostname}` (201; active reseller only; 422 `bad_hostname|hostname_reserved`, 409
`custom_domains_unavailable|domain_limit|domain_taken`, 502 `domain_provider_error`); `POST
/reseller/domains/{id}/refresh` (re-queries Cloudflare; 30/h); `DELETE /reseller/domains/{id}`
(204; deletes at Cloudflare). Other resellers' ids answer 404. Admin: `GET /admin/reseller-domains`,
`DELETE /admin/reseller-domains/{id}` (force). Secret `cloudflare_saas_token` via `PUT
/admin/site/secrets`; settings `resellers.{custom_domains_enabled, cf_zone_id, fallback_origin,
max_domains}`. Only `active` domains resolve the storefront by Host. Reserved store slugs: fixed
set + `resellers.reserved_slugs` (422 `slug_reserved`).

**Ledger accounts.** payment: `gateway_clearing:zpay +amount`, `sales_revenue -(subtotal - markup)`,
`reseller_payable:{id} -markup`, `promo_discount +discount`, `surcharge_revenue -fee`; sign-up:
`reseller_signup_revenue -subtotal`; refund = exact mirror; payout paid: `reseller_payable:{id}
+amount`, `cash:payouts -amount`.

**Store link (v5.1).** `settings.resellers.link_mode` = `query` (default) | `subdomain`.
`store_url` (reseller `/me` and admin reseller views) is `{PUBLIC_BASE_URL}/?store=<slug>` in query
mode, `https://<slug>.<base_domain>/` in subdomain mode. The web app remembers the storefront in
localStorage for 30 days (`?store=off` clears it).

## Blocking v5.2 (user suspension, blocked IPs) — review-v5.1 §5

New error codes (both `403`):

| code | When |
| --- | --- |
| `account_suspended` | Login (password, Google) and every authenticated request of a suspended user. Envelope carries extra string fields `until` (ISO-8601 `Z`, when the suspension is timed) and `reason`. A timed suspension auto-lifts on the first check after `until`. Wrong passwords stay a generic `401` (no probing of suspended accounts). |
| `ip_blocked` | Any `/api/*` request from a blocked client IP: `{"error":{"code":"ip_blocked","message":"Access denied"}}`. Exempt: `/api/v1/pay/notify/*`, `/healthz`, non-API paths, and IPs in `security.staff_ip_allowlist` (allowlist wins). The client IP is the trusted-proxy resolved one (an untrusted peer's `X-Forwarded-For` is ignored). Fails open on Redis/DB errors. |

Admin endpoints:

| Endpoint | Body / query | Returns | Notes |
| --- | --- | --- | --- |
| GET `/admin/users` | `?q=&role=&status=active\|suspended&page&page_size` | `Paginated<AdminUser>` | staff; `q` matches email/handle/name. Agents only see buyers (`role` ignored). `AdminUser = {id, email, handle, display_name, role, status: "active"\|"suspended", suspended_until, suspend_reason, created_at, last_login_at}` |
| POST `/admin/users/{id}/suspend` | `{reason (1..200), until?: ISO-8601 with offset, future}` | `AdminUser` | staff. Admins can suspend buyers and agents; agents buyers only (`403`). Never self, never an admin (`403`). Other tenant → `404`. Revokes all the user's sessions; audit `user.suspend` (before/after/reason/ip) |
| POST `/admin/users/{id}/unsuspend` | none | `AdminUser` | same permissions; audit `user.unsuspend` |
| GET `/admin/blocked-ips` | `?page&page_size` | `Paginated<BlockedIp>` | admin only. `BlockedIp = {id, cidr, note, expires_at, created_at, created_by}` |
| POST `/admin/blocked-ips` | `{cidr, note?, expires_at?}` | `BlockedIp` (201) | admin only. `cidr` is an IP or CIDR (normalised); IPv4 down to `/8`, IPv6 down to `/32`; `0.0.0.0/0` and `::/0` refused (`422 validation`), as is a range containing the caller's own IP. Duplicate → `409`. Audit `blocked_ip.add` |
| DELETE `/admin/blocked-ips/{id}` | none | `204` | admin only; audit `blocked_ip.remove`. Takes effect within a few seconds (in-process cache) |

Config is mirrored to Redis `cfg:blockedips:<tenant_id>` (TTL 600 s, rebuilt from the DB on a miss or after a
Security settings save).

**v5.2 manual stock.** `products.manual_stock` int, NULL = unlimited (existing rows migrate to NULL). Admin `POST /admin/products` for `delivery_mode=manual`: omitted `manual_stock` = 1, explicit `null` = unlimited; `PATCH /admin/products/{id}` accepts `manual_stock` (>= 0 or null), audited in `product.updated` before/after. Admin product views expose `manual_stock`.
Manual-stock caveats: lock order for order creation is buyer row -> product rows (sorted by id) -> promo row; releases are aggregated per product and applied in product-id order. Setting a number while earlier orders reserved against NULL (unlimited) is outstanding lets later releases inflate the count (releases just add back). Switching a product to `manual` via PATCH without `manual_stock` sets it to 1 (it is NULL otherwise = unlimited). An order that expired (stock released) and is paid late is refund-only.

## Staff API tokens v5.2 (agent / admin API) — review-v5.2 §5

Guide with curl recipes: [`docs/agent-api.md`](agent-api.md).

`Authorization: Bearer kst_<43 urlsafe chars>` is accepted **only** on `/api/v1/admin/*` and
`POST /api/v1/uploads`, and only when no session cookie is sent (a cookie request keeps the CSRF
header rule; a cookie-less token request needs no `X-Requested-With`). Anywhere else, or if the token
is unknown/expired/revoked/owner no longer an active, non-suspended staff user of the request's
tenant: `401 unauthorized`. The request acts as the owner (role never exceeds the owner's). A route
without a declared scope, or a scope the token lacks, returns `403` code **`token_scope`**.
Rate limit 600/min per token (`429`).

Scopes: `products:read`, `products:write`, `cards:import`, `orders:read`, `chat`, `uploads`, `stats` (route mapping in
agent-api.md). Tokens cannot reach settings, secrets, tokens, users, blocked IPs, payouts, refunds,
ledger, audit, resellers, order mutations or card listing/reveal.

| Endpoint | Body | Returns | Notes |
| --- | --- | --- | --- |
| GET `/admin/api-tokens` | none | `{items: ApiToken[], scopes: string[]}` | admin session only (never a token). `ApiToken = {id, name, prefix, scopes, user_id, expires_at, last_used_at, last_used_ip, revoked_at, created_at, status: "active"\|"revoked"\|"expired"}`; never the secret or hash |
| POST `/admin/api-tokens` | `{name (1..60), scopes (>=1 of the list), expires_in_days (1..365, default 90)}` | `ApiToken` + `token` (201) | the **only** response containing the plaintext token. 20 creates/hour/admin. Audit `api_token.create` (name, prefix, scopes; no secret) |
| POST `/admin/api-tokens/{id}/revoke` | none | `ApiToken` | admin session only; other tenant → `404`; idempotent. Audit `api_token.revoke` |

Storage: table `api_tokens` (migration 0009): SHA-256 hex unique, RLS forced, `app_api` may only
update `last_used_at`, `last_used_ip`, `revoked_at` (last-used written at most once a minute).

## SEO routes v5.2 (root level, not under `/api`) — review-v5.2 §6, ADR-024

Public, no auth, tenant from the Host header, GET/HEAD. Caddy proxies exactly these paths to the API;
the global per-IP limiter covers them like `/api`. `?lang=en|zh-CN` picks the language (default: the
tenant's `defaults.locale`; `Accept-Language` is ignored). A page the SPA does not know a route for is
not served here.

| Path | Response |
| --- | --- |
| `/`, `/help`, `/search` | the SPA `index.html` with `<title>`, description, canonical, `hreflang` en / zh-CN / x-default, Open Graph, Twitter, JSON-LD (`WebSite`+`SearchAction`, `Organization`, `BreadcrumbList`) and a pre-rendered content block. `/search` is always `noindex,follow` |
| `/p/{id}` | same, with `Product`+`Offer` (price, currency, `InStock`/`OutOfStock`: manual stock NULL = unlimited, auto = available cards) and `BreadcrumbList`. Not a UUID / unknown / archived / other tenant / disabled category / hidden by the reseller: **404** + `noindex,nofollow` + SPA shell |
| `/a/{slug}` | `Article` + `BreadcrumbList`; unknown or unpublished: 404 as above |
| `/sitemap.xml` | `urlset` with `xhtml:link` alternates: home, help, active products, published articles, `lastmod`; empty when indexing is off; cached 10 min |
| `/robots.txt` | `Allow: /`, `Disallow:` `/admin /api /cart /orders /pay /me /messages /reseller /chat /lookup`, `Sitemap: <base>/sitemap.xml`; indexing off: `Disallow: /` |

Headers: HTML `Cache-Control: public, max-age=0, s-maxage=300, must-revalidate` (404: `max-age=60`),
sitemap/robots `public, max-age=300`; `X-Robots-Tag: noindex, nofollow` on 404s and when indexing is off.
`503` (no cache) when the SPA shell cannot be loaded; Caddy then serves the static shell.

Settings: `PUT /admin/site` accepts `seo: {indexing_enabled, index_storefronts,
default_description_i18n, og_image}` (`og_image`: empty, `/path` or `https://` URL); returned by
`GET /admin/site` as `seo`. Audited with the other v5 sections. Env: `APP_WEB_INDEX_URL`
(default `http://web/index.html`), `APP_WEB_INDEX_PATH` (file, overrides the URL).



## Issue 23 additions (channel totals, switching, specific codes)

- `POST /cart/quote` and `POST /checkout` accept optional `channel` (one of the channel ids): the fee uses that channel when enabled, else the first enabled one. `/cart/quote` also returns `channel` and `channel_totals: {channel_id: {fee, fee_rate, amount}}` for every enabled channel (fee ceiled per line, same as checkout).
- Pending-payment order views (`GET /orders/{ref}`, lists, SSE) and checkout views carry `channel_totals` (same shape; `null` otherwise). The client renders these for the selected channel and never computes money itself.
- New 409 `too_many_unpaid` (unpaid-order cap; was generic `conflict`) and 422 `buyer_input_too_long` (was generic `validation`; also for the pydantic 4000-char cap). Pasted order info drops zero-width/BOM characters.
