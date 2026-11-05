# API contract — MVP-0 (demo vertical slice)

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
  - Codes: `unauthorized`, `forbidden`, `csrf`, `not_found`, `validation`, `rate_limited`, `conflict`, `out_of_stock`, `invalid_state`, `quote_expired`, `upload_rejected`.
- **Pagination:** `?page=1&page_size=20` returns `{ "items": [...], "total": n, "page": 1, "page_size": 20 }`.

## Types

```ts
type Role = "buyer" | "agent" | "admin";
type User = { id: string; email: string; display_name: string; role: Role; locale: "en" | "zh-CN" };

type ProductSummary = {
  id: string; title: string; cover: string | null; price: string; currency: string;
  delivery_mode: "auto_card" | "manual"; stock: number | null; // stock only for auto_card
  category: string; sold: number;
};
type Product = ProductSummary & { description: string; images: string[]; status: "active" | "draft" };
type AdminProduct = Omit<Product, "title" | "description"> & {
  title_i18n: Record<"en" | "zh-CN", string>; description_i18n: Record<"en" | "zh-CN", string>;
  floor_price: string; created_at: string;
};

type Attachment = { id: string; url: string; mime: string; bytes: number;
  kind: "image" | "voice"; width?: number; height?: number; duration_ms?: number };

type MessageType = "text" | "image" | "voice" | "product_card" | "quote_card" | "order_card" | "system";
type Message = {
  id: number; conversation_id: string; client_msg_id: string | null;
  sender: { id: string | null; role: "buyer" | "agent" | "system"; display_name: string };
  type: MessageType; text: string | null; attachment: Attachment | null;
  payload: ProductCardPayload | QuoteCardPayload | OrderCardPayload | SystemPayload | null;
  created_at: string;
};
type ProductCardPayload = { product_id: string; title: string; cover: string | null; price: string; currency: string };
type QuoteCardPayload = { quote_id: string; title: string; amount: string; currency: string; qty: number;
  note: string | null; status: QuoteStatus; expires_at: string; order_id: string | null };
type OrderCardPayload = { order_id: string; title: string; amount: string; currency: string; status: OrderStatus };
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
  manual_delivery: { text: string | null; attachment: Attachment | null } | null;
  can_reveal: boolean;   // auto_card && delivered/completed && owner
};
type AgentStatus = { status: "online" | "away" | "on_hold" | "offline"; message: string | null; updated_at: string };
```

## Buyer / public endpoints

| Method & path | Body / query | Returns | Notes |
|---|---|---|---|
| POST `/auth/register` | `{email, password, display_name, locale}` | `User` + sets cookie | Password ≥10 chars; Argon2id; rate-limited 5/min/IP |
| POST `/auth/login` | `{email, password}` | `User` + cookie | Rate-limited 5/min/IP and 10/h/email; generic error `unauthorized` |
| POST `/auth/logout` | — | `204` | |
| GET `/auth/me` | — | `User` or `401` | |
| GET `/categories` | — | `[{key, name}]` | localized |
| GET `/products` | `q, category, page, page_size` | Paginated `ProductSummary` | `q` uses PG full-text + trigram on title/description |
| GET `/products/{id}` | — | `Product` | |
| GET `/support/status` | — | `AgentStatus` | Best status across agents |
| POST `/conversations` | `{product_id?: string}` | `Conversation` | Find-or-create the buyer's open conversation. If `product_id` is given and differs from the last product card, append a `product_card` message |
| GET `/conversations` | — | `Conversation[]` | Only the buyer's own |
| GET `/conversations/{id}/messages` | `after_id?, before_id?, limit≤100` | `Message[]` ascending | Participants only |
| POST `/conversations/{id}/messages` | `{client_msg_id, type: "text"\|"image"\|"voice", text?, attachment_id?}` | `Message` | Idempotent on `(sender, client_msg_id)`; text ≤2000 chars; 30/min/user |
| POST `/conversations/{id}/read` | `{message_id}` | `204` | |
| POST `/uploads` | multipart `file`, `kind=image\|voice` | `Attachment` | Image ≤8 MB (jpeg/png/webp/gif first frame), re-encoded, EXIF stripped. Voice ≤1 MB (webm/ogg/mp4/mpeg magic bytes) plus `duration_ms` form field ≤60000 |
| GET `/files/{id}` | — | bytes | Auth: owner, conversation participant or staff. `nosniff`, `Content-Disposition: inline`, `CSP: sandbox`. Product images are public |
| GET `/stream` | — | `text/event-stream` | Events: `message` (Message), `conversation` (Conversation), `order` (Order), `agent_status` (AgentStatus). Heartbeat comment every 15 s. Clients fall back to polling `messages?after_id` every 4 s |
| POST `/quotes/{id}/accept` | — | `Order` | Buyer of that conversation only; status `offered` and not expired; creates a `pending_payment` order with `amount = quote.amount`; posts an `order_card`; quote becomes `accepted` |
| POST `/orders` | `{product_id, qty}` | `Order` | Buy-now at list price; `qty` 1–10; for `auto_card`, checks available stock (not reserved) |
| GET `/orders` | `page, page_size` | Paginated `Order` | Own orders only |
| GET `/orders/{id}` | — | `Order` | Owner only |
| POST `/orders/{id}/pay` | `{channel: "zpay_alipay"}` | `{pay_url}` | Only `pending_payment`; builds a signed `submit.php` URL; the client navigates to it |
| POST `/orders/{id}/reveal` | — | `{secrets: string[]}` | Proxied to the delivery service; owner + `can_reveal`; 10/h/order; audited |
| POST `/orders/{id}/confirm` | — | `Order` | `delivered` → `completed` |
| GET `/pay/notify/zpay` | ZPay query params | `text/plain` `success` \| `fail` | Full pipeline (architecture §7). POST also accepted |
| GET `/pay/return/zpay` | ZPay query params | `302` → `/orders/{out_trade_no}` | **Display only**, never fulfils |

## Admin endpoints (`/api/v1/admin/*`, role `agent` or `admin`)

| Method & path | Body / query | Returns | Role |
|---|---|---|---|
| GET `/admin/stats` | — | `{orders_today, revenue_today, pending_payment, awaiting_manual, open_conversations, low_stock: ProductSummary[]}` | agent |
| GET `/admin/products` | `q, status, page` | Paginated `AdminProduct` | agent |
| POST `/admin/products` | `AdminProduct` fields (no id) | `AdminProduct` | admin |
| PATCH `/admin/products/{id}` | partial | `AdminProduct` | admin |
| DELETE `/admin/products/{id}` | — | `204` (soft delete → draft) | admin |
| POST `/admin/products/{id}/images` | multipart `file` | `{url}` | admin |
| POST `/admin/products/{id}/cards/import` | `{lines: string}` (one code per line) | `{imported, duplicates, rejected}` | **admin only**; ≤5000 lines/request; sealed immediately; audited |
| GET `/admin/products/{id}/cards` | `status, page` | Paginated `{id, masked, status, order_id, created_at}` | admin; plaintext never returned |
| GET `/admin/orders` | `status, q, page` | Paginated `Order & {buyer: {id, email, display_name}}` | agent |
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
| GET `/admin/stream` | — | SSE: all conversation, message and order events for the tenant | agent |
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
