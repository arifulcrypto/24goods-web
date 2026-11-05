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
    | `conflict` | 409 | Also: email already registered, slug taken, duplicate card, more than the allowed number of pending orders (2 for instant accounts, 5 otherwise). |
- **Pagination:** `?page=1&page_size=20` returns `{ "items": [...], "total": n, "page": 1, "page_size": 20 }`.

## Proof of work (self-hosted captcha replacement)

Required on `POST /auth/instant`, `POST /auth/register`, `POST /lookup`, and on `POST /auth/login` once the same (login, IP) pair has failed 3 times in the last hour (`428 pow_required`; the retry must carry `pow`). It is **not** required on `/lookup/reveal`.

1. `GET /pow/challenge?scope=instant|register|lookup|login` returns `{challenge, bits, expires_at}` (`expires_at` is a Unix timestamp, TTL 120 s; default `bits` = 18, `APP_POW_BITS`).
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
  delivery_mode: "auto_card" | "manual"; stock: number | null; // stock only for auto_card; expired cards excluded
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
| POST `/auth/instant` | `{pow, email?}` | `{user: User, credentials: {login, password}}` + cookie | **PoW (`instant`).** One-tap buyer: handle `u-` + 7 base32 chars (lowercase `a-z2-7`), random 14-char password, `is_instant=true`. The password is returned **once** (`Cache-Control: no-store`). Without `email` the stored email is `<handle>@instant.invalid`; a valid `email` is stored as given (`409 conflict` if taken) and `is_instant` is then false. Limits: 3/h and 10/day per IP, 30/day per /24 (IPv4) or /48 (IPv6) |
| POST `/auth/login` | `{email, password, pow?}` | `User` + cookie | `email` accepts an email **or a handle** (`u-xxxxxxx`, case-insensitive). Rate-limited 5/min/IP and 10/h per (login, IP); after 3 failures per (login, IP) the next attempt needs `pow` (`login` scope, `428 pow_required`); generic error `unauthorized` |
| PATCH `/me` | `{display_name?, locale?}` | `User` | Audited |
| POST `/me/credentials` | `{current_password?, email?, new_password?}` | `User` | At least one of `email` / `new_password`. `current_password` is required unless the account `is_instant`; wrong → `403 invalid_password`. Setting a real email turns an instant account into a regular one. Changing the password revokes every other session (the current one survives). 10/h/user |
| POST `/me/delete` | `{password?, confirm: "DELETE"}` | `204` | Buyers only. `password` required unless `is_instant`. `409 has_open_orders` if a `pending_payment` / `paid` / `manual_review` order or an open after-sales ticket exists. Anonymises the row (email `deleted+<id>@invalid`, name "Deleted user", unusable password hash, handle cleared, status `deleted`, closed tickets' text redacted), revokes all sessions, audited |
| GET `/site` | `lang` | `Site` | Public. `announcement` is null unless enabled; `pages` lists published fixed pages |
| GET `/pow/challenge` | `scope=instant\|register\|lookup\|login` | `{challenge, bits, expires_at}` | See *Proof of work*. 60/min/IP |
| GET `/articles` | `category, page, page_size` | Paginated `ArticleItem` | Published `kind=article` only; pinned first, then newest |
| GET `/articles/{slug}` | — | `Article` | Published articles **and pages**; counts one view per viewer per 10 min |
| POST `/lookup` | `{order_no, access_code, pow}` | `LookupOrder` | **PoW (`lookup`).** `Cache-Control: no-store`; never creates a session. 10/h/IP (every attempt counts); 5 wrong codes per order per hour → `429`; unknown order and wrong code give the same `404 not_found`. `access_code` is case-insensitive and tolerates spaces/hyphens and I/L/O confusion |
| POST `/lookup/reveal` | `{order_no, access_code}` | `{cards: [{id, code}]}` | Same authentication/limits (30/h/IP). Goes through the delivery service with the same per-order, per-buyer (the order's buyer) and global budgets as `/orders/{id}/reveal`. `no-store`; audited |
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
| POST `/orders` | `{product_id, qty, src?}` | `Order` + `access_code` | Buy-now at list price; `qty` 1–10; for `auto_card`, checks available (non-expired) stock. `src` is a traffic tag stored as `orders.source`; it is lowercased and **silently dropped** unless it matches `[a-z0-9_-]{1,32}`. `access_code` (10 Crockford base32 chars) is returned **only here** (and on quote accept / rotation) |
| GET `/orders` | `page, page_size` | Paginated `Order` | Own orders only |
| GET `/orders/{id}` | — | `Order` | Owner only |
| POST `/orders/{id}/pay` | `{channel: "zpay_alipay"}` | `{pay_url}` | Only `pending_payment`; builds a signed `submit.php` URL; the client navigates to it |
| POST `/orders/{id}/reveal` | — | `{secrets: string[], cards: [{id, code}]}` | Proxied to the delivery service; owner + `can_reveal`; 10/h/order; audited. Voided (replaced/defective) cards are never returned |
| POST `/orders/{id}/confirm` | — | `Order` | `delivered` → `completed` |
| GET `/pay/notify/zpay` | ZPay query params | `text/plain` `success` \| `fail` | Full pipeline (architecture §7). POST also accepted |
| GET `/pay/return/zpay` | ZPay query params | `302` → `/orders/{out_trade_no}` | **Display only**, never fulfils |

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
| POST `/admin/cards/find` | `{lines: string[] (1–100), by: "content"\|"first_field"}` | `{items: FoundCard[], missing}` | admin. Matches by keyed fingerprint (content, `\|exp=` suffix ignored) or by `lookup_fp` = HMAC(fp_key, tenant ‖ 0x1e ‖ casefold(first field)) — the line **is** the first field. `FoundCard = CardRow & {line, product_id, product_title_i18n}`; `missing` = lines without a match. Never decrypts. 120/h |
| POST `/admin/cards/void` | `{ids[] \| batch_id, reason}` | `{voided}` | admin. Exactly one of `ids` (≤1000) / `batch_id`; only `available`/`reserved` cards are voided (`void_reason`=`reason` ≤64 chars). Audited |
| POST `/admin/cards/{id}/replace` | `{code}` | `CardRow` (the new card) | admin. Seals a new card (same product, batch and expiry unless `\|exp=` given) and voids the old with `void_reason=replaced`, `replaced_by`. Only `available`/`reserved` (`409 conflict` otherwise; duplicate → `409`) |
| GET/PUT `/admin/site` | PUT: `{announcement?: {enabled?, title_i18n?, body_i18n?}, after_sales_default_hours?, auto_confirm_hours?: {manual, auto_card}, support_hours?: {en, zh-CN}}` | `{announcement: {version, enabled, title_i18n, body_i18n, body_html_i18n}, after_sales_default_hours, auto_confirm_hours, support_hours, defaults}` | **admin only**; partial update. `announcement.version` increases when the title or body changes or when it is re-enabled; body markdown is sanitized to `body_html_i18n`. Audited |
| GET `/admin/articles` | `status, kind, category, q, page` | Paginated `AdminArticle` | **admin only** |
| POST `/admin/articles` | `{slug, kind?, category?, title_i18n, body_i18n?, status?, pinned?}` | `AdminArticle` | slug `[a-z0-9][a-z0-9-]{0,62}`, unique per tenant (`409`); body is markdown, rendered + sanitized server-side. `AdminArticle = {id, slug, kind, category, title_i18n, body_i18n, body_html_i18n, status, pinned, views, published_at, created_at, updated_at}` |
| GET / PATCH / DELETE `/admin/articles/{id}` | PATCH: any create field | `AdminArticle` / `204` | Fixed pages (`kind=page`) cannot be deleted (`422`). Audited |
| GET `/admin/after-sales` | `status, page` | Paginated `StaffAfterSales` | staff. `StaffAfterSales = AfterSales & {order: {id, order_no, title, status, delivery_mode, qty, amount, currency, delivered_at, after_sales_until}, buyer: {id, display_name, email}, resolved_by}` |
| GET `/admin/after-sales/{id}` | — | `StaffAfterSales` | staff |
| POST `/admin/after-sales/{id}/action` | `{action: "processing"\|"replace"\|"refund"\|"reject"\|"resolve", note?, delivery_text?, card_ids?}` | `StaffAfterSales` | staff; **`refund` is admin only** (`403` for agents). Open or processing tickets only (`invalid_state`). `reject` needs `note`. `replace` on a manual order needs `delivery_text` (replaces the order's delivery text; ticket → `replaced`). `replace` on an auto_card order enqueues outbox `delivery.replace` and the ticket becomes `processing`; the delivery worker voids the reported cards (`card_ids`, default all cards of the order) with `void_reason=defective`, claims new ones for the same order, writes new delivery rows and sets the ticket `replaced` (no stock → ticket stays `processing`, audit `delivery.replace_out_of_stock`). `refund` marks the order `refunded` and posts a balanced reversal ledger transaction (money goes back through the Alipay console in MVP). Closing a ticket sets `order.auto_confirm_at = max(old, now + 24 h)`. Every action is audited and posts a system message into the order conversation |
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
| `APP_LOOKUP_KEY` | derived from the PoW key | base64 ≥32 bytes, HMAC key of order access codes |
| `APP_POW_BITS` / `APP_POW_TTL_S` | 18 / 120 | difficulty / challenge lifetime |
| `APP_BRAND_NAME` | `Kesiv` | `Site.brand`, seed tenant name |
| `APP_INSTANT_PER_IP_HOUR` / `_DAY` / `APP_INSTANT_PER_NET_DAY` | 3 / 10 / 30 | instant-account limits |
| `APP_MAX_PENDING_ORDERS_INSTANT` | 2 | unpaid orders per instant account (registered: `APP_MAX_PENDING_ORDERS`=5) |
| `APP_INSTANT_PURGE_DAYS` | 7 | purge window for unpaid instant accounts |
| `APP_LOOKUP_PER_IP_HOUR` | 10 | `/lookup` attempts per IP |
