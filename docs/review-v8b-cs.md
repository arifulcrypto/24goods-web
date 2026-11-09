# Spec v8b — order lifecycle in CS, desk alerts, avatars & profile (issue #58 §4, §5, §3 drawer label)

Read `gh issue view 58` §3–§5 for evidence (verified: buyers who never opened chat get no
conversation, so no order card or system messages; the desk only chimes for paid manual orders).

## §4 Every order is visible to customer service

1. Order creation (buy-now, cart checkout, quote acceptance — wherever `chat.open_conversation_for`
   is used today to pick the conversation) attaches the order to the buyer's open conversation,
   creating it if missing (reuse the existing "ensure open conversation" helper used by settle if
   it exists, else add one in chat/service.py; tenant-scoped, idempotent under concurrency, staff
   accounts never get buyer conversations). Then the existing order_card / paid / cancelled system
   messages appear for every order.
2. Staff realtime: make sure the staff SSE stream receives an order event on creation, payment
   (all delivery modes) and cancellation/expiry (check `realtime/publish.py` / `broadcast_order`).
3. Admin `useCsNotifications.ts`: alerts (existing chime + desktop notification + toast pipeline)
   for: new order ("New order #{no} · {title}"), payment received, all modes ("Paid #{no} ·
   {amount}"), cancelled ("Cancelled #{no}"). Expired: available but off by default.
   Per-agent toggles stored in localStorage (wrapped in try/catch), surfaced in the existing
   notification popover (`NotifyPopover.vue`). Defaults: created on, paid on, cancelled on,
   expired off. Do not double-alert a paid manual order (today's alert) — one alert per event.
   i18n en + zh-CN. The conversation with a new/updated order moves to the top of the inbox with
   its unread badge (follow how a buyer message does it today).

## §3 (remainder) Payment method in the order drawer

`OrderDrawer.vue`: show the payment method as a localized tag using the admin i18n keys
`payChannel.*` added in #60 (`zpay_alipay`, `usdt_xlayer`, `usdt_arbitrum`, `usdt_trc20`, `none`).

## §5 Avatars and profile

- `users.avatar_key` (nullable; migration `0016_user_avatars`) referencing an image stored through
  the existing hostile-upload pipeline (`uploads/service.py`: magic bytes, re-encode, EXIF strip,
  size limit, random key) — re-encode to a small square (≤ 256 px), ≤ 2 MB input, images only.
- `PUT /me/avatar` (multipart) and `DELETE /me/avatar`; rate-limited; audit row. Staff can set
  their own avatar the same way from the admin console (small profile control is enough).
- Serving: follow how chat image attachments are served today (same origin/headers rules, §1.6).
  Visibility: a buyer's avatar to that buyer and staff of the same tenant; staff avatars to buyers
  they chat with (tenant-scoped). No cross-tenant access; test it.
- `/me` returns `avatar_url`; chat message sender payloads include `avatar_url` (null → initial).
- `ChatMessage.vue` (shared): avatar on the right for own messages (`self`) as well as on the left
  for others; image with initial fallback; keep layout at 375 px.
- Web: nickname + avatar editing (Me page edit sheet or Settings page — wherever display name is
  edited today). i18n en + zh-CN; 44 px touch targets.

## Tests

Order without prior chat gets a conversation + order card; concurrency (two orders at once → one
conversation); staff never get one; staff SSE receives created/paid/cancelled; avatar upload
rejects non-images / oversize, re-encodes, other-tenant fetch 404, buyer cannot fetch another
buyer's avatar; `/me` and chat payload carry `avatar_url`. Frontend: vitest for the notification
toggle defaults and event mapping.

## Out of scope

EPay amount jitter (declined), vaulting manual delivery text (#8).
