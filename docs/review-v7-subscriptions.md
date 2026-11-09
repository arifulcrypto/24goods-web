# Spec v7 — subscription products, month-based after-sales, less manual service work

Owner request (2026-11-09): "When listing a product, CS should be able to mark it as a subscription.
Subscriptions are billed monthly, not every 28 days; the after-sales default should be a month,
not 28 days, so expiry reminders and CS notifications work. CS can still manage service renewals
and registrations in the backend. Staff should be able to see order details / after-sales info on
the service instead of typing it in by hand."

Existing code to build on (read first): `backend/app/models/operations.py` (ServiceEntitlement,
ServiceRenewal, ServiceReminder), `backend/app/modules/operations/{service,router,jobs}.py`
(period_end() already does calendar months; reminders already scheduled/sent),
`backend/app/modules/common/sitedefaults.py` (after-sales window; framework-free, also used by the
delivery worker), `frontend/apps/admin/src/pages/ServicesPage.vue` (today staff paste raw UUIDs).

## A. After-sales period in months

- `products.after_sales_months SMALLINT NULL` (1..12). Resolution: product `after_sales_hours`
  (not null) → hours; else product `after_sales_months` → calendar months; else tenant default.
  Setting one on a product clears the other (admin create/patch).
- Tenant setting `after_sales_default = {"unit": "month"|"hour", "value": int}`; built-in default
  `{"unit": "month", "value": 1}`. Legacy `after_sales_default_hours` still honoured when the new
  key is absent. Data migration: drop `after_sales_default_hours` where it equals 672 (the old
  seeded 28-day default) so those tenants get 1 month. Keep the admin settings API accepting the
  legacy field; add the new one; update `DEFAULT_*` in sitedefaults and the seed.
- Calendar-month add lives in sitedefaults (pure Python, UTC, clamp to month end: Jan 31 + 1 month
  = Feb 28/29). `delivery_deadlines()` uses it. Reuse it from operations `period_end()` if it fits
  (period_end is tz-aware; don't change its behaviour).
- Views keep `after_sales_hours` (for months: months×30×24, approximate, for old clients) and add
  `after_sales_months: int | null`. Shared TS types updated. Buyer web shows "1 month" / "1 个月"
  wherever it shows the after-sales period today; the order's actual deadline stays authoritative.
- Admin: product dialog after-sales control = [site default | hours | days | months];
  settings page default = value + unit (months | hours). i18n en + zh-CN.

## B. Subscription products

- `products.subscription JSONB NULL`; null = not a subscription. Shape (pydantic-validated):
  `{"period_policy": "calendar_month"|"fixed_days", "period_days": 1..366 (fixed_days only),
  "grace_days": 0..30, "email_reminders": bool, "telegram_reminders": bool}`. Default when the
  switch is turned on in the product dialog: calendar_month, grace 0, both reminders on.
- `service_entitlements.product_id UUID NULL FK products` (+ index tenant_id, buyer_id,
  product_id); backfill from orders.product_id in the migration.
- New job `operations.jobs.sync_subscriptions(res)` run from the main loop next to the reminder
  jobs (see `backend/app/main.py` `_job(...)`). Per tenant, orders with status delivered/completed,
  `delivered_at` within the last 45 days, product.subscription not null, and the order not yet
  used by any ServiceEntitlement.order_id or ServiceRenewal.order_id:
  - exactly one non-cancelled entitlement for the same tenant+buyer+product → **renewal**: extend
    from `max(ends_at, delivered_at)` by `qty` periods, add ServiceRenewal (confirmed_by = the
    entitlement owner), version+1, status active, audit `service.renewed` actor_role `system`
    reason `auto_subscription`.
  - otherwise → **new entitlement**: active, starts_at = delivered_at, ends_at = `qty` periods,
    label = product title (en, ≤120), policy/grace/reminders from product.subscription,
    owner = the staff user who delivered the order if recorded, else the tenant's earliest active
    admin (skip + log if none), timezone "UTC". Audit `service.created` reason `auto_subscription`.
  - idempotent (unique constraints already exist; use a savepoint per order, swallow
    IntegrityError), bounded batch per run, tenant-pinned sessions like the other jobs.
  - source order later refunded/cancelled → entitlement status `cancelled` (audit, system).
- Manual create still works (non-subscription products, or CS registering by hand).

## C. Admin Services page (less typing)

- `POST /admin/services` also accepts `order_no` (instead of buyer_id+order_id); buyer resolved
  from the order; `owner_id` defaults to the caller; label/policy prefilled from
  product.subscription when present.
- List rows add: order_no, product title, buyer email, source (auto | manual), days left /
  overdue tag, open after-sales on any linked order (bool). Search by order_no or buyer email.
  Dates shown in the browser's local time (not raw ISO).
- `GET /admin/services/{id}` returns linked orders (source + renewals): order_no, paid_at,
  amount+currency, status, after-sales status. Order numbers open the existing OrderDrawer.
- `GET /admin/services/{id}/renewal-candidates`: the buyer's verified-paid orders for the same
  product not yet used by any entitlement/renewal. Manage dialog: pick from that list instead of
  pasting a UUID; due date via a date-time picker.
- All tenant-scoped (AGENTS §1.4) with a cross-tenant test for each new endpoint.

## D. Buyer view

- Buyer order detail (own orders only): `service: {status, starts_at, ends_at} | null` when the
  order is the source or a renewal of an entitlement. Web order page shows "Service period:
  <start> – <end>" plus "N days left" / "Expired". i18n en + zh-CN. 375 px first.

## Tests (targeted, under the lock)

Month arithmetic (Jan 31, leap year, Dec→Jan), after-sales resolution order + legacy setting,
sync job: new entitlement, auto renewal extends from max(ends_at, delivered_at), qty>1, idempotent
second run, refunded source cancels, other tenant's orders never touched; admin create by
order_no; renewal-candidates scoping; buyer order view shows service only to its owner.

## Out of scope

Buyer self-service "renew" button / recurring auto-charge (no payment-method storage), Stripe /
PayPal adapters (tracked separately), any change to payment, notify or card-vault code.
