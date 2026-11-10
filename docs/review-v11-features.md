# v11 features — variants, TG-style chat, notifications, announcements, manual services (spec)

Owner request 2026-11-10. One branch (`feat/v11-features`), one PR. AGENTS.md §1 invariants apply
throughout (server-side prices, tenant filter in every query, audit rows, en + zh-CN in the same PR,
375 px mobile first, 44 px targets, no third-party runtime assets).

Industry patterns referenced below are from general product knowledge (Shopify/Taobao SKUs, Telegram,
WhatsApp/Intercom/Zendesk notifications, Intercom "News"), not re-verified on the web this session.

## Current state (verified in code, 2026-11-10)

| Ask | Today |
|---|---|
| Variants / options | None: one `price_minor`, one stock, one set of terms per product |
| Recently viewed | Shown on Home **and** Me; Home is long |
| "Recommended for you" | Context-based SQL (`catalog/service.py` `_RECOMMEND_SQL`): same category as viewed items ×3, "bought together" by other buyers (90 d) ×2, log of 30-day sales, +0.5 if new (14 d); in-stock only; cached in Redis. No per-user model. |
| Pin / sort | Products have `group_sort` (number), categories `sort`; no pin, no admin drag sort, storefront order fixed |
| Admin browser notifications | Exist (`admin/src/notify.ts`, `useCsNotifications`) but **off by default** (`prefs.notify=false`), permission only requested from a popover toggle, page-level `new Notification` (no service worker), so CS think it is gone |
| Manual service records | Not possible: `service_entitlements.buyer_id` and `order_id` are NOT NULL |
| Buyer message alerts | None (no sound, no desktop notification); no typing indicator anywhere |
| "Draft" | Chat action "Quote in draft" pastes a quote into the composer — replaced by real replies |
| Pins | Backend `POST /chat/messages/{id}/pin` (≤ 3, `payload.pinned`) exists; UI only lists pins **inside the loaded page**, no sticky bar, no jump |
| Long-press menu | Horizontal wrapping button row |
| Announcements | One site banner in tenant settings + `users.announcement_ack` version; no list, no unread |
| Avatars | Upload only; initials fallback |

## W1 — Product variants (SKUs)

Pattern: Shopify/Taobao — a product has 1..N variants; each variant is what is actually bought and
carries its own price, stock and (optionally) its own service terms; the product page shows a
variant picker; a product without variants behaves exactly as today.

- Table `product_variants` (migration `0020_product_variants`, RLS + FORCE + `tenant_isolation` policy and
  grants like `products`): `id, tenant_id, product_id, name_i18n {en, zh-CN}, price_minor BIGINT,
  floor_price_minor BIGINT, manual_stock INT NULL (NULL = unlimited, manual products), sort INT,
  status active|archived, after_sales_months SMALLINT NULL, after_sales_hours INT NULL,
  subscription JSONB NULL (same shape as products.subscription), sold INT, created_at, updated_at`.
  NULL term fields inherit the product's. Delivery mode stays per product (one fulfilment pipeline).
- `card_stock.variant_id` NULL FK; `orders.variant_id` NULL FK + `orders.variant_name` snapshot (String 120).
  Cart/checkout items accept `variant_id`.
- Rules: a product with ≥ 1 active variant **requires** `variant_id` on buy-now / cart / checkout (422
  otherwise); unknown/archived/other-product variant → 404/422. Price = variant price, then the existing
  reseller markup, promo and channel fee pipeline (server-side, invariant 3). Manual stock reserved per
  variant under the variant row lock (same pattern as `reserve_manual_stock`); auto-card stock counted
  per variant (`available_stock` / `reserved_auto_stock` / delivery worker claim filter by variant_id);
  release paths (expiry, cancel, release-hold) use the variant. Terms snapshot (`orders.terms`) takes the
  variant's terms when set. Product card "from ¥X" = min active variant price.
- Card import / inventory pages take an optional variant; a product with variants requires one.
- Admin product dialog: "Variants" section — add / rename / reorder / archive, price, floor price,
  stock (manual), optional after-sales months and subscription toggle per variant. Price changes are
  audited (before/after). Deleting a variant that has orders = archive.
- Buyer product page: variant chips (≥ 44 px), price/stock/terms update with the selection, selected
  variant carried to cart/checkout/order detail and admin order views (`variant_name`).
- Tests: price comes from the variant (client amount ignored), stock per variant (manual + auto-card,
  last card of variant A not sold through variant B), terms inheritance, cart/checkout with two
  variants of one product, archived variant refused, product without variants unchanged, cross-tenant
  variant id refused, release on expiry returns the variant's stock.

## W2 — Telegram-style chat, typing, buyer notifications

- **Replies** replace "Quote in draft": `POST` message body gains `reply_to: int | None` (same
  conversation, not redacted); stored as `payload.reply_to = {id, sender_name, sender_role, preview ≤ 120
  chars, type}`; bubble shows a quoted block, tap scrolls to the original (load older if needed);
  composer shows a reply bar with ✕. Remove the "Quote in draft" action and its i18n keys.
- **Long-press / right-click menu** = vertical list (TG context menu), each row ≥ 44 px with icon +
  label: Reply, Copy, Pin/Unpin, Edit (own text, ≤ 48 h), Recall (own, ≤ 3 min, existing). Bottom sheet on
  mobile, popover on desktop; Esc / outside tap closes.
- **Edit own text messages**: `PATCH /chat/messages/{id}` {text}; sender only, text type, ≤ 48 h, not
  redacted; keeps `payload.edited_at`, broadcasts the updated message; "edited" label; audit row.
- **Pinned bar** (TG): `GET /conversations/{id}/pins` (server list, newest first, ≤ 3, independent of the
  loaded page); sticky bar under the header shows the newest pin + count; tap jumps to the message and
  cycles to the next pin; both buyer and staff can pin/unpin (existing endpoint), an SSE update refreshes
  both sides. Pin/unpin posts a small system line ("X pinned a message").
- **Typing indicator**: `POST /conversations/{id}/typing` (participants only, rate limit 1 per 3 s per
  user, nothing stored); SSE event `typing {conversation_id, role, name, until}` to the other side (buyer
  channel ↔ agent channel); client shows "typing…" for 6 s or until a message from that side arrives;
  sent while the composer has input, at most every 3 s.
- **Buyer alerts**: on a new staff message when the chat is not open or the tab is hidden: chime
  (reuse the WebAudio chime, move to shared) and a desktop notification (Notification API through the
  service worker when available). Buyer Settings → Notifications: sound on/off, desktop on/off (asks
  permission on enable). Defaults come from admin Settings → Notifications (`notify_defaults.buyer
  {sound: true, desktop: false}` in tenant settings, exposed in `/site`); the buyer's own choice
  (localStorage) wins. Unread count in the tab title.
- Tests: reply validation (other conversation / redacted refused), edit rules (other sender, too old,
  non-text), typing authz + rate limit, pins list tenant/participant scoped, cross-tenant attempts.

## W3 — Announcements, manual services, pin & sort, avatars, admin notifications

- **Announcements** (Intercom "News" pattern): reuse `articles` with `kind='notice'` (title/body i18n,
  `category` news|operations|inventory|notice, `pinned`, `status`, `published_at`). Table
  `announcement_reads (tenant_id, user_id, article_id, read_at)` PK (user_id, article_id), RLS.
  Buyer: bell in header with unread badge, `/announcements` list + detail, "Mark all as read"; on a new
  published announcement the buyer gets a one-time prompt (modal, newest unread, "View" / "Later"),
  per tenant setting `announcements.prompt_new` (default on). Guests keep read state in localStorage.
  Admin: Announcements page (create/edit/publish/pin/delete, category), per item read count, setting
  toggles (`prompt_new`, `show_badge`), staff also see unread + "Mark all as read" for their own view.
  The old single banner stays as the site banner (unchanged).
- **Manual service records** (calendar-reminder pattern): `service_entitlements.buyer_id` and
  `order_id` become NULLable; new `customer_label` (String 120, e.g. name / handle), `contact`
  (String 200), `notes` (Text), `ref` (String 12, unique per tenant, human code like `SV-7K3Q9P`, always
  generated — order/customer ids are optional). `service_renewals.order_id` NULLable + `note`.
  Admin Services: "New record" form with customer label, contact, optional buyer email (links to an
  account when it exists) and order no., label, start date/time, end date or period, timezone,
  reminder lead days, notes; reminders use the existing scheduler (staff Telegram/email). Search by
  ref / customer / contact / label. A manual record is edited and renewed manually.
- **Pin & sort**: `products.pinned_at` and `categories.pinned_at` (NULL = not pinned; pinned first,
  newest pin first), keep `group_sort`/`sort` as the manual order; admin Products and Categories get a
  pin toggle and drag-to-reorder (writes the sort numbers in one audited bulk call). Tenant setting
  `catalog.default_sort` = manual | sales | price_asc | price_desc | newest (default manual); buyer
  listing gets a sort selector (same options, default from setting). Pinned items stay on top in every
  sort.
- **Generic avatars**: DONE (orchestrator) — `frontend/packages/shared/src/avatars/index.ts`: 16
  original inline-SVG presets `p01..p16`, `resolveAvatar({url, preset, seed})`, `defaultAvatarPreset`,
  `isAvatarPreset`. Remaining for W3: backend + pickers + rendering. `users.avatar_preset` String 16 NULL; `PUT /me/avatar/preset {preset}` (validated against
  the list), clears an uploaded avatar; staff can pick in their profile; a user with neither upload nor
  preset gets a deterministic preset from a hash of the user id (frontend), so every bubble has a face.
- **Admin browser notifications**: DONE (orchestrator, `admin/public/sw.js`, `admin/src/notify.ts`) — on by default; first-login banner "Enable desktop notifications"
  (permission request on click); register a tiny service worker (`/admin/sw.js`, same origin, no
  caching, only `notificationclick` → focus/open the URL) and show notifications through
  `registration.showNotification` (works minimised and on Android Chrome), fallback `new Notification`;
  urgent alerts use `requireInteraction`; a "Send test notification" button; chime plays also when
  hidden. True push with the browser closed would need Web Push (FCM, unreachable in mainland China)
  — out of scope, documented.
- **Recently viewed**: DONE (orchestrator) — removed from Home, kept on Me (refreshes on return).

## Out of scope (recorded)
Web Push with the browser closed; per-variant images; variant option matrices (two dimensions) —
a flat variant list covers the catalogue today (e.g. "1 month / 3 months / 12 months").
