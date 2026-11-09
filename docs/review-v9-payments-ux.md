# Spec v9 — payment-channel resilience & diagnostics, storefront gate/announcements, expedite (#63)

Incident (prod, 2026-11-09 06:28 UTC): the Xiaodou EPay host stopped accepting TCP connections
from our production IP (connect timeout on 80/443; other mainland hosts fine; worked at 23:17 the
day before). `prepare()` marked the attempt `unknown`, every later `/pay` and `/cancel` for that
checkout returned 503 (`requery_all` -> UnresolvedEvidence) until the checkout expired, and the
reconciler kept re-querying every 2 minutes. Read issue #63 (`gh issue view 63`) for the rest.

Payment code rules (AGENTS §1.2) stay intact: signature pinning, exact Decimal amount, re-query,
row lock + state machine, unique gateway trade no, outbox. Never treat "unknown" as "not paid"
without positive evidence or an audited admin decision.

## §1 Pre-dispatch failures are not "unknown"; admin can resolve stuck attempts

1. In the EPay/ZPay prepare path (`modules/payments/gateways.py`, `app/payments/zpay.py` client):
   classify transport errors. Errors where the request provably never reached the gateway —
   DNS failure, `httpx.ConnectError`, `httpx.ConnectTimeout` (and the equivalent from the adapter's
   own wrapper; keep the original exception type available) — mark the attempt `failed` with
   `data.reason = "gateway_unreachable"`, audit `payment.attempt_failed`, and return 503 with a
   distinct code `channel_unreachable` ("This payment method is unavailable right now. Please choose
   another method."). The payable stays `pending_payment` and the buyer can immediately pay with
   another channel or cancel. Read timeouts / ambiguous errors keep today's `unknown` behaviour.
2. `requery_all` / cancel: attempts in `failed` are skipped (they were never dispatched).
3. Admin resolve: `POST /admin/payment-attempts/{id}/resolve` {outcome: "not_paid", note (10..500)},
   admin only, tenant-scoped, only for `unknown`/`preparing` attempts. Staff confirm in the gateway
   console that no charge exists; sets `failed` with `data.resolved_by/resolved_note`, audit
   severity warning, operation event. If the attempt later turns out paid, the normal late-payment
   path (manual_review) still applies. Admin order drawer: when the order/checkout has an unknown
   attempt, show a warning with this action (en + zh-CN).
4. Reconciler: back off unknown attempts (e.g. every 2 min for the first hour, then hourly) and,
   after 24 h unresolved, emit one high-severity operation event + staff notification instead of
   re-querying forever.
5. Buyer web: on `channel_unreachable` show the message and let them pick another method (no dead
   end); keep the order page reachable.

## §2 Channel self-test and health

- `POST /admin/payment-gateways/{id}/test` (admin): from the server, (a) connect to the gateway
  host with a 5 s timeout -> reachable + latency; (b) a signed order query for a random
  non-existent out_trade_no via the existing query client -> a well-formed "not found" proves
  reachability + merchant/signature format; report the gateway's message verbatim (never echo
  keys). Same idea for BEpusdt (its existing health/query endpoint). Audit `payment.gateway_tested`.
- Health per channel in the admin payment settings: `healthy` / `degraded` / `unreachable` from the
  last self-test plus recent attempt outcomes (last 30 min: any `gateway_unreachable` -> unreachable;
  unknown > 0 -> degraded). "Test now" button. Optional dashboard badge.
- No simulated real-money charge in this PR.

## §3 Checkout member orders carry the chosen channel

When a checkout's channel is set (`orders/checkout.py`), set `order.channel` on every member order
in the same transaction (tenant-scoped). Backfill in a migration for pending checkouts. Test that
buyer and admin order views show the channel during payment.

## §5 USDT confirmation guidance

`UsdtPayPage.vue`: tip "Blockchain confirmation usually takes 1–3 minutes. You can stay here or
check My orders — the status updates automatically." plus a button to the order page while polling
continues. en + zh-CN.

## §6 Manual delivery: urgent staff notice + buyer "expedite"

- On payment of a manual order, staff get an urgent desk alert + Telegram (reuse the existing
  notification pipeline; one notice, no duplicates with the #62 paid alert — mark it urgent).
- Buyer button "Remind seller to ship" on own paid, undelivered manual orders: posts a system
  message into the order's conversation + staff alert + Telegram. Limits: once per 30 min per
  order, max 3 per order (persist the count, e.g. order events), 429 with retry-after otherwise.
  en + zh-CN. Note: the tenant's manual delivery note currently says rush orders are not accepted;
  do not change tenant data — just mention it in the PR.

## §7 Site-wide storefront gate

`StorefrontGate.vue`: when the visit challenge is enabled, every storefront route requires
clearance (no route allow-list), including /me, /cart, /orders, /chat, /lookup, payment return
pages. Keep the existing clearance flow, the docked banner fallback for 428s raised mid-session, and
`siteAccessCleared` refetching. 375 px, a11y as before.

## §8 Announcement banner + mandatory acknowledgement

- Extend the existing announcement settings: `display: "modal" | "banner" | "both"` and
  `require_ack: bool`. Banner: dismissible strip on the home page (per version, localStorage with
  try/catch). `require_ack`: modal without a close button until the buyer taps "I have read and
  understood" (stored per version; signed-in users also server-side so it follows them). Admin
  settings UI + i18n en + zh-CN. Defaults keep today's behaviour.

## Declined (do not implement)

§4 random Alipay tail — declined in #58 §7; EPay orders carry our own out_trade_no and signed
notifies, and adding offsets to evade a provider's risk controls endangers the merchant account.

## Tests

Connect-timeout on create -> attempt failed, payable payable/cancellable, other channel works;
read-timeout -> unknown (unchanged); admin resolve (admin only, tenant isolation, only unknown,
audited) then cancel succeeds; reconciler backoff/24 h alert; self-test endpoint with a fake gateway
(reachable / not found / unreachable) and no key in the response; checkout member channel sync;
expedite limits (cooldown, max 3, other buyer 404); gate covers all routes (vitest); announcement
require_ack flow (vitest). Cross-tenant attempt for every new endpoint.
