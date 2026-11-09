# Spec v8 — order/stock/refund/subscription integrity (issue #59 A, B, C, D, F, G)

Read issue #59 (`gh issue view 59`) for the evidence; this spec records the decisions. Keep AGENTS §1
intact (payment verification pipeline, vault, tenant filters/RLS, balanced ledger, audit rows).
One migration `0015_order_integrity` (additive). Tests: one new file `tests/test_integrity_v8.py`
plus updates to existing assertions that encode the old behaviour (e.g. `test_flow.py` paid-short-stock).

## A. No oversell of auto-card stock; recovery for held orders

1. **Count-based reservation** (no new table): when creating any goods order for an `auto_card`
   product (buy-now, cart checkout, product-backed quote acceptance), lock the product row(s) in the
   existing lock order (buyer row → products sorted → promo; see `lock_manual_products`) and require
   `available_stock(product) - reserved(product) >= qty`, where `reserved` = sum(qty) of goods orders
   for that product, tenant, `delivery_mode='auto_card'`, status in (`pending_payment`, `paid`).
   Expiry/cancellation releases automatically because it is derived from order status.
2. **Requeue** `POST /admin/orders/{id}/requeue-delivery` (admin only): goods + auto_card + status
   `manual_review` + a verified succeeded payment covering the order (reuse the checks of
   `operations.service.paid_order` or the settle path; never infer payment from status alone) +
   enough available stock now → status back to `paid`, emit the same outbox event the settle path
   emits (idempotent key per requeue), order event + audit `order.delivery_requeued`.
3. **Refund before delivery**: admins can refund a paid-but-undelivered goods order
   (`paid` / `manual_review`) through the same refund routine as after-sales (B semantics).
   Endpoint `POST /admin/orders/{id}/refund` {reference, note?}.
4. **Buyer non-delivery escalation**: buyers may open an after-sales request with reason
   `not_received` on their own `paid`/`manual_review` goods orders once `paid_at` is older than the
   tenant's manual ETA (`delivery.manual_eta_hours`) for manual or 1 hour for auto_card. Existing
   delivered/completed rules unchanged.
5. Admin OrderDrawer: buttons "Requeue delivery" (auto_card manual_review) and "Refund" (paid /
   manual_review), confirm dialogs, i18n en + zh-CN.

## B. "Refunded" means the money was returned

- Every refund action (after-sales refund and A.3) requires `reference` (3..200 chars: provider
  refund id, transaction hash or transfer note) and is the staff's **confirmation that the money was
  sent back**. Store it on the ticket/order event and in the audit `detail`; show it to staff and to
  the buyer on the order ("Refunded to the original payment method · ref …").
- Staff copy (en + zh-CN) replaces the Alipay-only hint: "Return the money first (payment console,
  wallet transfer for USDT, …), then confirm here with the reference." Button: "Confirm refund sent".
- No new order states now; a two-phase refund with provider APIs comes with #55. Ledger reversal
  stays exactly as today (once).

## C. Subscription refunds per paid period

- Migration: `service_renewals.reversed_at timestamptz NULL`, `service_entitlements.source_reversed_at
  timestamptz NULL`.
- `sync_subscriptions` reversal pass (tenant-pinned, row locks, audited as `system`):
  - renewal order refunded/cancelled and its ServiceRenewal not reversed → subtract that renewal's
    periods (qty × policy, calendar months backwards with the same clamping helpers) from `ends_at`,
    set `reversed_at`, version+1, audit `service.renewal_reversed`.
  - source order refunded/cancelled: if any non-reversed renewal remains → subtract the source's
    periods instead of cancelling and set `source_reversed_at` (audit `service.source_reversed`);
    otherwise cancel as today.
  - if a subtraction would put `ends_at` at/before `starts_at`, do not guess: leave the row, log
    a warning and record an operation event for staff.
  - reminders follow the version bump exactly as a staff `patch` does today.
- Manual services (`source='manual'`) are never auto-adjusted (as today).

## D. Reseller dispute reserve

- `reseller.service.balances` excludes markup of orders that have an after-sales ticket in an open
  state (`open`, `processing`); the same function serves request and approval (already rechecked
  at approval). Expose `disputed_minor` in the balance view and show it in the reseller centre and
  admin payout dialog (en + zh-CN). Keep the existing lock order; document it in a comment.

## F. Purchase-term snapshot

- `orders.terms JSONB NULL`, written at order creation for product-backed orders:
  `{"after_sales_hours": int|null, "after_sales_months": int|null, "subscription": {...}|null}`
  (the product's values at that moment; null product values mean "tenant default at delivery").
- `delivery_deadlines` callers (delivery worker, manual delivery) use the snapshot when present,
  product values otherwise (legacy orders). `sync_subscriptions` uses `orders.terms->'subscription'`
  when `terms` is not null, product.subscription only for legacy rows (terms null).
- The delivery worker only needs SELECT on orders (already table-level); confirm in the migration
  docstring.

## G. Manual stock reservations with provenance

- `orders.stock_reserved INTEGER NOT NULL DEFAULT 0`. `reserve_manual_stock` returns the quantity
  it actually reserved (0 when unlimited); `create_order` stores it; `release_manual_stock` gives
  back only `stock_reserved` and sets it to 0 in the same statement/transaction (idempotent).
- Migration backfill: pending_payment manual goods orders whose product currently has a finite
  `manual_stock` get `stock_reserved = qty` (equivalent to today's behaviour for them).

## Tests (targeted, under the lock)

Last-card contention (two buy-nows for one card: second gets OutOfStock), cart + quote paths,
expiry frees the reservation, requeue (needs verified payment; idempotent; refuses without stock;
agent role forbidden; other tenant 404), refund before delivery (reference required, ledger reversed
once), buyer not_received escalation timing, subscription renewal refund / source refund with and
without renewals / ambiguous case, reseller balance with an open ticket, terms snapshot used after
a product edit, unlimited→finite stock release. Cross-tenant attempt for every new endpoint.

## Out of scope

Vaulting manual delivery text (#59 E → tracked with #8), provider refund APIs (#55), payment-amount
jitter (declined in #58 triage).
