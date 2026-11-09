# Spec v10 — several EPay gateways: priority + sticky failover, admin routing view, test-payment QR

Owner request 2026-11-09: "I'll add other compatible YiPay (EPay) interfaces, maybe more than one.
Remember the previous selection and stick with the one that worked last time to increase the
success rate. Administrators should see the priority in the admin panel or set it manually. A
separate test channel where I can scan the test channel's QR code to verify it works."

Builds on: `payment_gateways` rows (label, gateway, merchant_id, sealed key, mode), tenant
`payments.epay_provider_id` (today: the single active gateway), `PaymentAttempt.provider_id`,
`modules/payments/gateways.py` (prepare/routing, `client_for`, `routed_resources` for notify),
the v9 self-test/health (`modules/admin/payment_ops.py`), and `app/payments/egress.py` (the
per-host direct/relay sticky route — keep it; it is a lower layer than this spec).

AGENTS §1.2 stays intact: every notify is verified with the attempt's own gateway key, exact
Decimal amount, re-query, row lock + state machine, UNIQUE(channel, gateway_trade_no), outbox.

## A. Routing across gateways

1. Migration `0018_gateway_routing`: `payment_gateways.priority INT NOT NULL DEFAULT 100`,
   `enabled BOOL NOT NULL DEFAULT true` (backfill: the current `epay_provider_id` row enabled with
   priority 10, others disabled), `last_ok_at`, `last_fail_at TIMESTAMPTZ NULL`, `last_fail_reason
   VARCHAR(32) NULL`. Tenant setting `payments.epay_routing = "sticky" | "priority"` (default
   sticky). Keep reading `epay_provider_id` as a legacy fallback only.
2. Candidate order at `prepare()` for the Alipay (EPay) channel: enabled gateways by priority; in
   `sticky` mode the gateway with the latest `last_ok_at` goes first.
3. Failover inside the same pay request, only when no order can exist at the failed gateway:
   - connect-level failure (`ZPayUnreachable`, v9 classification), or
   - an explicit refusal: `mapi.php` answered with a well-formed JSON `code != 1` (introduce
     `ZPayRefused(ZPayQueryError)` carrying a truncated, key-redacted gateway message).
   Each failed try: its attempt row ends `failed` (`reason` gateway_unreachable / gateway_refused),
   gateway `last_fail_*` updated, audit + operation event; then the next candidate gets a fresh
   attempt (new local_ref/out_trade_no, its own provider_id). Anything ambiguous (read timeout,
   protocol error, non-JSON, HTTP 5xx) stops failover: attempt `unknown` exactly as today.
   On success: `last_ok_at = now()` for that gateway (sticky). If every candidate failed: 503
   `channel_unreachable` (buyer can choose USDT / cancel), as in v9.
4. Re-query, notify, reconcile, cancel and admin resolve keep using the attempt's `provider_id`
   (never the "current" gateway). Disabling a gateway must not break settling its in-flight
   attempts. Cross-tenant: a gateway is only usable by its own tenant (test it).

## B. Admin: gateway list and priority

Admin -> Settings -> Payment, EPay section:
- table of gateways: label, gateway host, merchant id, enabled switch, priority (up/down buttons
  or number), health badge (v9), last OK / last failure (+reason), "in use" marker (the gateway
  the next payment will try first);
- routing mode selector: sticky (default) / fixed priority;
- existing add/edit/delete flows keep working; deleting a gateway with unsettled attempts is
  refused (or it is only disabled);
- all changes audited; en + zh-CN.
API: extend the existing payment-gateway admin endpoints (`PATCH` priority/enabled, `PUT` order),
`GET` returns the computed candidate order.

## C. Test payment with QR code

- `POST /admin/payment-gateways/{id}/test-payment` {amount: "0.01".."10.00", default "0.01"}:
  admin session only, rate limit 10/hour/admin, audit. Creates a real payment at that gateway
  (same mapi.php create + presentation as buyer payments) with an out_trade_no in a reserved test
  namespace (e.g. prefix `T`), stored in a new `payment_tests` table (tenant_id, provider_id,
  local_ref, gateway_ref, amount_minor, currency, status pending/paid/expired/failed, created_by,
  paid_at, trade_no, data). Not linked to any order/checkout; never fulfils, never touches
  stock, orders, resellers or the order ledger.
- Response: QR content (+ redirect URL if the gateway returns one). Admin UI shows a QR code
  (render locally — no CDN; reuse the QR component the USDT pay page uses) with amount, gateway,
  out_trade_no, countdown, and live status (poll `GET .../test-payments/{id}` which re-queries the
  gateway at most every 3 s).
- Notify for a test out_trade_no: same verification (signature with that gateway's key, pid,
  exact amount, re-query) then mark the test paid; idempotent; never routed to order settlement.
  Ledger: post the received amount balanced between `gateway_clearing` and a dedicated
  `test_receipts` account (so clearing still reconciles), or document why not if the ledger model
  makes that wrong — do not mix it into sales.
- Admin list of recent test payments (status, amount, time, gateway) with a note that test
  money is refunded in the gateway console. A paid test sets the gateway `last_ok_at`.

## Tests

Failover order (sticky vs priority), failover on unreachable and on refused, NO failover on read
timeout / 5xx / non-JSON, attempts and provider_id per try, notify/re-query/resolve use the
attempt's gateway after the active one changed or was disabled; admin priority/enabled/mode
endpoints (admin only, audited, cross-tenant 404); test payment create/notify/paid/idempotent,
never touches orders/ledger sales, rate limit, amount bounds, cross-tenant. Frontend vitest for the
priority list and the test-payment dialog state.
