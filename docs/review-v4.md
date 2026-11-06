# Commerce v4: cart, channel fee, order info popup, cancel/close, CS everywhere

Owner feedback 2026-11-06, after the first production test of https://shop.kesiv.com.

**Status:** approved by the owner ("Please begin implementation").

**Schema:** already on the base branch `feat/v4-base`, in migration `0003_commerce_v4` and the models (§9). Agents build on that schema and do not write another migration unless one is unavoidable.

**Lookup:** the order lookup by access code stays as it is. The owner explicitly said to keep it.

**References:**
- Mature shops (Taobao/JD cart and sticky action bar, Shopify checkout fee line).
- Faka sites: ka8.ai's "order information" popup, gmailbuy's list layout.
- Alipay "close trade" practice: query the gateway before closing.

## 1. Payment-channel surcharge (手续费由买家承担)

ZPay charges the merchant 1.6%. The owner wants the buyer to pay a channel fee, **default 3% for Alipay**, so that every sale is profitable. A private-domain direct transfer (later) would have 0%.

**Config.** Store it in `tenants.settings.payments.channels`, edited from the admin Site page:
```json
{"zpay_alipay": {"enabled": true, "surcharge_permille": 30}}
```
- Missing config means `zpay_alipay` is enabled with **30‰**.
- Valid range is 0–200‰.
- `Settings.zpay_fee_permille` (16) stays: it is the gateway's cost to us, used for the ledger fee line.

**Calculation.** Done at order creation, **server-side only**:
- `fee_minor = ceil(subtotal_minor × permille / 1000)`, using integer maths `(s*p + 999)//1000`. Round up: never lose a fen.
- `amount_minor = subtotal_minor + fee_minor`, which a DB CHECK enforces.
- The order also stores `fee_permille`, the rate that was applied.
- Single-item orders, quote orders and checkout orders all get the fee.
- The fee belongs to the order's channel, the default channel `zpay_alipay`.
- `POST /orders/{ref}/pay` with a channel whose permille differs from `order.fee_permille` returns 409 `fee_channel_mismatch`. With only one channel today, this cannot happen. A later multi-channel picker will recompute the fee before the first pay URL is issued.

**Admin price change** (`PATCH /admin/orders/{id}/price`): this sets the new **subtotal**, then the fee is recomputed with the same permille.

**Ledger** for a payment of `amount = subtotal + fee`:
- `gateway_clearing:zpay +amount`
- `sales_revenue −subtotal`
- `surcharge_revenue −fee`, only when fee > 0

The existing gateway-cost entry (`fees:zpay` / `gateway_clearing`, at 16‰ of the amount) stays unchanged.

**Refund reversal** (after-sales) must mirror the lines: `sales_revenue +subtotal`, `surcharge_revenue +fee`, `gateway_clearing −amount`. Old orders have fee = 0, so they keep working.

**Views.** Order views gain `subtotal`, `fee`, `fee_rate` (a string such as `"3%"`) and `checkout_no`. A new public `GET /api/v1/site/payment-channels` returns:
```json
[{"id": "zpay_alipay", "label": "Alipay", "fee_rate": "3%", "permille": 30}]
```
The storefront uses it to show the fee before an order exists. It is shown, never trusted.

**Buyer UI.** The buy sheet, cart and order detail show:

    Subtotal ¥10.00
    Payment fee (Alipay 3%) ¥0.30
    Total ¥10.30

The product price itself stays as set. The fee is shown as a transparent line, which is the common faka-site 手续费 pattern.

## 2. Cart and combined checkout (购物车)

**Cart.** Client-side in `localStorage`, at most 20 lines. Each line is `{product_id, qty, buyer_input?}`.
- A Cart tab sits in the tab bar with a badge: Home, Messages, Cart, Orders, Me.
- `POST /api/v1/cart/quote {items}` returns the server truth per line: title, cover, unit price, stock, `available`, `max_qty`, `needs_input`. It also returns the totals: subtotal, fee and total for the default channel.
- No login is needed. Unavailable lines are flagged, not silently dropped.
- Lines are grouped by product, with select checkboxes, a qty stepper and swipe-to-delete. The sticky footer reads "Total ¥x · Checkout (n)".

**Checkout.** `POST /api/v1/checkout {items:[{product_id, qty, buyer_input?}], source?}`, authenticated. The instant account flow is used if the visitor has none.
- It creates **one order per line**, each with its own `order_no`, access code and fee.
- It also creates one `checkouts` row: `checkout_no = "C" + 12 base32`, with `amount = Σ order.amount_minor`.
- Everything happens in one transaction. Stock is checked per auto_card line (409 `out_of_stock` with the product id). Mixed currencies return 422. 1–20 lines.
- All orders share `checkout_id` and `expires_at`.
- The open-orders cap counts payables, not orders: pending single orders plus pending checkouts.
- Response:
  ```json
  {"checkout": CheckoutView, "orders": [OrderView], "access_codes": {"<order_no>": "<code>"}}
  ```
  Access codes are returned once, as for buy-now.
- Buy-now (`POST /orders`) stays as it is, for a single item without a checkout.

**Pay.** `POST /api/v1/checkouts/{checkout_no}/pay {channel}` returns a pay URL with `out_trade_no = checkout_no` and `money = checkout.amount`.
- The checkout must be `pending_payment`, not expired, and all its orders must be `pending_payment`.
- `POST /orders/{ref}/pay` on an order that has a `checkout_id` returns 409 `pay_via_checkout` with `{checkout_no}`. A member order is never paid twice: its money only arrives via the checkout.

**Notify / fulfilment** (payment-auditor review required). When `out_trade_no` starts with `C`, take the checkout path. The pipeline is identical up to settlement: signature, then pid, then exact amount against `checkout.amount_minor`, then status, then server re-query, then the replay guard. At settlement:
1. Lock the checkout `FOR UPDATE`, then its orders `ORDER BY id FOR UPDATE`.
2. Insert **one** `Payment(checkout_id=…, order_id=NULL)`. `UNIQUE(checkout_id)` and `UNIQUE(channel, trade_no)` provide idempotency.
3. If `Σ member amount_minor ≠ paid`, every member goes to `manual_review` (`amount_changed`).
4. Otherwise apply the **same per-order transition as a single payment** to each member, factored out into one helper:
   - `paid` (or `manual_review` if late, meaning the checkout or the order was expired or cancelled)
   - order events and audit
   - the per-order ledger lines (§1)
   - outbox `order.paid`
   - `sold += qty`
   - the system message
5. The checkout becomes `paid` (or `manual_review`), with `paid_at` and `channel` set.

`expire_pending` also expires pending checkouts, together with their orders.

**Payment lookups.** The refund path, the "already paid" check and the admin order view must find an order's payment by `order_id = X OR checkout_id = order.checkout_id`. A refund of one member order reverses only that order's lines. The recorded refund flow is unchanged.

**My Orders.** No separate checkout page. Member orders appear as normal rows in My Orders.
- A pending row with a `checkout_no` shows "Pay together · n items · ¥total", which pays the checkout, and "Cancel".
- The order detail shows the same, plus a list of sibling orders.

## 3. Cancel and close (取消订单 / 关闭订单)

**Buyer.** `POST /orders/{ref}/cancel {reason?}` and `POST /checkouts/{no}/cancel {reason?}`.
- Only for `pending_payment`.
- An order inside a checkout cancels **the whole checkout**, with all its orders. A partial cancel would change the amount owed.
- If a pay URL was ever issued (`channel` set), **re-query ZPay first**. If it was paid, settle it through the normal path and return 409 `already_paid`. If the query fails, return 503 and let the buyer retry. This is Alipay's "query before close" rule.
- Effects: status `cancelled`, `cancel_reason`, an order event, audit, and the system message "Order cancelled by buyer".
- A later notify for a cancelled order still records the money and goes to `manual_review`, which is the existing behaviour.

**Staff.** The existing `POST /admin/orders/{id}/cancel` gets the same rules: a required reason, the checkout-wide effect and the re-query. The buyer sees the reason in chat ("Order closed by support: …").
- The admin OrderDrawer and Orders list get a **Close order** button with a reason prompt on pending orders.
- Paid orders keep using the after-sales refund.

**Buyer UI.** "Cancel order" (with a confirm dialog) on pending orders, in the list and the detail. A countdown "Pay within mm:ss" appears next to "Pay now".

## 4. Order information popup (下单信息弹窗)

Example: a ChatGPT Plus top-up needs the buyer's session token. When enabled on a product, the popup appears on **Buy now** and **Add to cart**, before the order or cart line is created.

**Product config** `products.buyer_input`, edited in the admin ProductDialog under "Order information popup":
```json
{
  "enabled": true, "required": true, "multiline": true, "max_len": 2000,
  "title_i18n": {"en": "Order information", "zh-CN": "下单信息"},
  "prompt_i18n": {"en": "Open `https://chatgpt.com/api/auth/session` after logging in, copy everything and paste it below.", "zh-CN": "…"},
  "placeholder_i18n": {"en": "Paste here", "zh-CN": "粘贴到这里"}
}
```
- Validation: `max_len` is 1–4000, each text field at most 1000 characters, and `enabled: false` disables the popup.
- The title falls back to the i18n default "Order information / 下单信息" when empty. The owner calls it a "fixed popup title".

**Popup** (web, Vant popup):
- the title;
- the prompt, rendered by the shared tokenizer (§7): `` `backtick` `` segments and URLs become **tap-to-copy chips**;
- the fixed hint **"You can also tap the text to copy it."** / 「也可以点击文字复制」;
- a textarea (or input) with a counter and a **Paste** button (`navigator.clipboard.readText`, hidden when unsupported);
- **Confirm**, disabled while a required field is empty.

The value goes into `buyer_input` for POST /orders and the checkout lines, and into the cart line.

**Server.**
- 422 `buyer_input_required` when the field is required and the value is empty. Values are trimmed, and control characters other than `\n\t` are stripped.
- Stored in `orders.buyer_input`. It is **never logged and never put in audit snapshots**: the audit detail records only `{"buyer_input": true}`.
- `PATCH /orders/{ref}/input {buyer_input}` lets the buyer fix it while the order is `pending_payment` or `paid` (not yet delivered).

**After a successful payment**, the settle helper inserts a chat message **from the buyer** into the order's conversation:
```
📝 Order G… · <product title>
<buyer_input>
```
CS then sees it in the inbox with the usual sound and browser notification. No chat with CS is needed before the purchase.

**Display.**
- Buyer order detail: an "Order information" block with a copy button and Edit while allowed.
- Staff OrderDrawer and inbox side panel: the same block, with copy.

**Security note (MVP).** The value is stored in plaintext behind RLS. File a GitHub issue: "encrypt orders.buyer_input at rest (it can hold session tokens)". The owner accepted MVP-level handling.

## 5. Customer service from anywhere (主动咨询 / 获客)

**Floating CS bubble** on every buyer page except chat, login and register.
- Bottom-right, above the tab bar, 48 px, with an unread badge.
- Tapping it opens `/chat`. On a product page it opens `/chat?product=<id>`; the existing conversation flow posts the product card ("I'm asking about this").

**No account?** Tapping any CS entry (bubble, product page, Me → Contact CS, order lookup) creates an **instant account** silently, using the existing PoW flow with a "Connecting you to support…" status. It then opens the chat directly; it never redirects to /login.
- A small banner in chat says "You're chatting as guest u-xxxx · Save login" and opens the existing CredentialsSheet.
- Route `/chat` loses `meta.auth`. The page itself ensures there is an account.

**Welcome message and quick questions.** Site settings `support.welcome_i18n` and `support.quick_questions_i18n`: a list of at most 6 strings per locale, each at most 60 characters.
- The welcome is shown at the top of an empty or new conversation as a local, non-persisted agent-style bubble.
- The quick questions are chips above the composer that send the text when tapped. They are edited on the admin Site page.

**Offline.** The chat stays open. Show "Support is offline. Leave a message and we'll reply at <support_hours>."

## 6. Staff sends product links (客服推送商品)

- The staff composer gets a **Product** button. It opens a picker dialog with product search, title, price and stock, then sends `{type: "product_card", product_id}`.
- `POST /admin/conversations/{id}/messages` accepts `type=product_card` with `product_id`. The server builds the payload from the product (`product_card_payload`), never from the client. The product must be active and in the same tenant.
- The buyer chat renders product cards with **View** and **Buy now** buttons. Buy now opens the buy sheet, including the §4 popup.
- The admin Products list gets a **Copy link** button: `https://<host>/buy/<id>` (the direct buy link from v3).

## 7. Smart text in chat (链接/邮箱/单号识别)

There is one pure tokenizer, `linkify(text): Segment[]`, in `packages/shared/src/chat/linkify.ts`, with unit tests. `ChatMessage.vue` renders the segments as Vue nodes and **never uses v-html**. Both apps therefore get it. The popup prompt (§4) and delivery notes reuse the tokenizer.

| Kind | Rule | Chip | Tap |
|---|---|---|---|
| url | `https?://` up to whitespace; trailing `.,;:!?)】」` trimmed. Only http(s). | 🔗 + hostname | A confirm dialog shows the **full URL**, "Only open links you trust", **Open** → `window.open(url, '_blank', 'noopener,noreferrer')`. |
| email | the usual local@domain.tld | ✉ "Email" | Copy, toast "Copied — looks like an email address". |
| order_no | `\bG[A-Z2-7]{12}\b` | 🧾 "Order no." | Copy, toast "Copied — looks like an order number". Buyer app: also "View order". |
| number | 6 or more consecutive digits, optional single spaces or dashes, at most 32 characters | # "Number" | Copy, toast "Copied — looks like a number (account / order / phone?)". |
| code | `` `...` `` (prompt texts only) | monospace | Copy, toast "Copied". |
| text | everything else | — | Long-press or the context menu copies the whole message (keep the existing behaviour). |

## 8. Rich product descriptions and help docs with images

- `products.description_i18n` is now **markdown**. On create and update the server renders `description_html_i18n` with the existing `core/markdown.py` (nh3, images only from `/api/v1/files/<uuid>`). It backfills on first write. A one-off pass in the seed (idempotent) renders existing products.
- Product views gain `description_html`. The buyer app renders it with `SafeHtml`, falling back to plain text.
- **Admin markdown editor** `MarkdownEditor.vue` (admin app) with:
  - a toolbar: bold, heading, list, link, **image upload**, preview;
  - **paste or drop an image to upload**.

  It is used for the product description (EN/zh), articles, fixed pages (help/terms/…) and the announcement body.
- Image upload: `POST /api/v1/admin/content/images` (staff, multipart). It reuses the uploads processing: magic bytes, re-encode, EXIF strip, size limit, a **public** attachment. It returns `{id, url: "/api/v1/files/<id>"}`, and the editor inserts `![](url)`.
- Rendered images: `max-width: 100%`, tap to preview (Vant `showImagePreview`) in the buyer app.

## 9. Data model (`0003_commerce_v4`, already on `feat/v4-base`)

- **`checkouts`:** id, tenant_id, checkout_no (unique), buyer_id, amount_minor, currency, status (`pending_payment|paid|cancelled|expired|manual_review`), channel, expires_at, paid_at, timestamps. FORCE RLS. app_api: select/insert/update. app_delivery: select.
- **`orders`:** + checkout_id, subtotal_minor (NOT NULL; a trigger derives it when omitted), fee_minor, fee_permille, buyer_input (≤ 4000), cancel_reason. CHECK `amount = subtotal + fee`.
- **`payments`:** order_id is now nullable, + checkout_id (unique), CHECK exactly one of the two.
- **`products`:** + buyer_input jsonb, description_html_i18n jsonb.

## 10. Global access-frequency limit (default **OFF**)

The owner wants it ready but disabled for the MVP, with an admin switch.

**Config** in Redis key `cfg:ratelimit:<tenant_id>`, written by the admin Site PUT and also stored in `tenants.settings.rate_limit`:
```json
{"enabled": false, "limit": 120, "window_s": 60}
```
- `limit` is 10–10000 and `window_s` is 1–3600.
- Fast path: when the setting is disabled, at most one cached Redis read every 5 s per process.

**Middleware.** Pure ASGI, after `CoreMiddleware`, so the client IP is already resolved through the trusted proxies.
- Per-IP fixed window on `/api/*`.
- **Exempt:** the payment notify path, `/healthz`, `/api/v1/realtime/*` (SSE) and `/api/v1/admin/*`, since staff are IP-allowlisted at the edge.
- Over the limit, return HTTP 429 with `Retry-After` and exactly this body:
  ```json
  {"code":429,"msg":"Access is too frequent. Please try again later."}
  ```
- **Fail open** on Redis errors.

**Frontend.** The shared API client maps that 429 to a toast with the i18n text. In zh-CN that is 「访问过于频繁，请稍后再试」.

**Admin.** Site page → "Access limit": a switch plus limit and window.

## 11. Other polish (mature-shop patterns, kept small)

- **Product page:** a sticky bottom bar with CS, Cart (badge), **Add to cart** and **Buy now**. Also a stock and sold line and a qty stepper in the buy sheet.
- **My Orders:** status tabs All / Unpaid / To deliver / Delivered / After-sales (client-side filter on the existing list, or `?status=`). Completed orders get **Buy again**.
- **Announcement popup:** confirm the v3 dismissible announcement shows when `announcement.enabled`. Production was seeded **disabled**, which is why the owner never saw it. The admin Site page must make the toggle obvious. Ops will enable it in production with a welcome text.
- **Admin dashboard:** counts of unpaid, to-deliver and after-sales orders, each linking to the filtered lists.

## 12. API additions (authoritative; fold into `docs/api-contract.md`)

```
GET  /api/v1/site/payment-channels                 -> [{id,label,fee_rate,permille}]
POST /api/v1/cart/quote        {items}              -> {lines:[{product_id,title,cover,unit_price,currency,
                                                        stock,available,max_qty,needs_input,qty,line_subtotal}],
                                                        subtotal, fee, fee_rate, total, currency}
POST /api/v1/checkout          {items,source?}      -> {checkout, orders, access_codes}
GET  /api/v1/checkouts/{no}                         -> CheckoutView {checkout_no,status,amount,currency,
                                                        expires_at,order_nos:[...],subtotal,fee}
POST /api/v1/checkouts/{no}/pay {channel}           -> {pay_url}
POST /api/v1/checkouts/{no}/cancel {reason?}        -> CheckoutView
POST /api/v1/orders            + buyer_input?       (existing)
POST /api/v1/orders/{ref}/cancel {reason?}          -> OrderView
PATCH /api/v1/orders/{ref}/input {buyer_input}      -> OrderView
OrderView  + subtotal, fee, fee_rate, checkout_no, buyer_input (owner/staff only), cancel_reason
Product    + description_html, buyer_input (public config: enabled, required, multiline, max_len,
             title, prompt, placeholder; resolved to the request locale)
AdminProduct + buyer_input (i18n form)
POST /api/v1/admin/conversations/{id}/messages  type=product_card, product_id
POST /api/v1/admin/orders/{id}/cancel {reason}      (reason now required)
POST /api/v1/admin/content/images (multipart file)  -> {id,url}
GET/PUT /api/v1/admin/site  + payments.channels, support.welcome_i18n, support.quick_questions_i18n,
                              rate_limit {enabled,limit,window_s}
GET  /api/v1/site (public)  + support.welcome, support.quick_questions (resolved locale)
Errors: fee_channel_mismatch 409, pay_via_checkout 409 {checkout_no}, already_paid 409,
        buyer_input_required 422, out_of_stock 409 {product_id}
```

Error bodies use the existing error envelope. Only the global limiter uses the fixed `{"code":429,"msg":…}` body.

## 13. Build plan

There are three `implementer` agents (Sonnet), each in its own worktree from `feat/v4-base`.

| Agent | Scope |
|---|---|
| **backend** | §1–§6, §8 server, §10, §12, tests (cross-tenant included), `docs/api-contract.md` |
| **web** | buyer app: cart tab and page, buy sheet fee lines, §4 popup, cancel and countdown, floating CS bubble and guest chat, welcome and quick questions, product card buttons, `linkify` + `ChatMessage` (shared), 429 toast (shared client), description HTML, product sticky bar, order tabs, Buy again |
| **admin** | ProductDialog buyer-input config and MarkdownEditor, articles/pages/announcement editor, Site page (channel fee, rate limit, welcome and quick questions), Close order, product picker in StaffComposer, Copy link, dashboard counters |

After the build: payment-auditor and security-reviewer review the backend, then i18n-mobile-reviewer the frontend. Then the merge, the PR and CI. Deploy to the demo, then to production with `scripts/prod.sh deploy`.
