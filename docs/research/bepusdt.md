# BEpusdt — USDT gateway research (2026-11-07)

Legend: **[V]** verified in the upstream repo/docs on 2026-11-07, **[I]** inferred.

- Repo `v03413/BEpusdt` (2.3k★, last push 2026-11-01), latest release **v1.24.3** (2026-10-21).
  Image `v03413/bepusdt:<tag>` (pin a version tag + digest, never `latest`). **[V]**
- Networks: `usdt.arbitrum` (Arbitrum One), `usdt.xlayer` (X Layer), `usdt.trc20` (TRON) among
  others (`docs/trade-type.md`). **[V]**
- Storage: SQLite in `/var/lib/bepusdt` (volume) by default; PostgreSQL ≥18 via `POSTGRESQL_DSN`
  (recommended upstream). For us: SQLite volume (small, isolated; no access to our DB). **[V]/[I]**
- Admin UI on :8080 (wallet addresses, rate, API token). Must NOT be exposed publicly: internal
  network only, admin reached via SSH tunnel. The buyer checkout page `/pay/checkout/:trade_id`
  is public → expose only `/pay/*` + static assets through Caddy on a path or subdomain. **[V]/[I]**

## API (`docs/api/api.md`, `app/router/epusdt.go`) **[V]**
- `POST /api/v1/order/create-transaction` JSON: `order_id`, `amount` (fiat, >0 = fixed-amount
  mode; never use 0 = "exclusive address" mode), `trade_type`, `notify_url`, `redirect_url`,
  optional `fiat`/`name`/`timeout`, `signature`. Response `data`: `trade_id`, `order_id`,
  `amount`, `actual_amount` (crypto), `token` (address), `expiration_time`, `payment_url`, `status=1`.
  Re-creating with the same `order_id` rebuilds the order (amount/type reset).
- `POST /api/v1/order/cancel-transaction` `{trade_id, signature}`.
- `POST /api/v1/pay/info` `{trade_id}` → `order_id`, `status`, `money` (fiat), `actual_amount`,
  `trade_type`, `token`, `trade_url` (explorer link). **Unauthenticated** → our server-to-server
  re-query over the internal network.
- EPay-compatible `GET|POST /submit.php` also exists (not needed; use the native API).
- Signature: all non-empty params except `signature`, sorted by key (ASCII), `k=v` joined by `&`,
  append API token (no `&`), MD5, lowercase. (MD5 is upstream's choice; compare constant-time.)

## Notify (`docs/notify/readme.md`) **[V]**
- POST to `notify_url`, same body for 3 events, `status`: 1 waiting (every minute, no retry),
  2 paid (retries with back-off up to the upstream retry limit), 3 expired.
  **Correction 2026-11-08:** the configured `0716485` native POST notifier accepts any
  HTTP 200, ignoring the body; this differs from its EPay GET notifier. Current API
  `200 fail` replies incorrectly suppress retries (#26); retryable failures must return
  non-200. See [pinned-source verification](security-payment-audit-2026-11-08.md#f0--bepusdt-http-200-failure-replies-permanently-acknowledge-failed-callbacks).
- Fields include `trade_id`, `order_id`, `amount`, `actual_amount`, `token`, `block_transaction_id`,
  `status`, `signature` [I: confirm exact names in the doc's field table].

## Mapping to our invariants (AGENTS §1.2)
signature (pinned MD5, constant-time) → `order_id` matches our order + instance → `amount` exact
Decimal equal to the order amount (fiat) → `status == 2` → re-query `/api/v1/pay/info` (status 2,
same order_id, same money) → row lock + state machine → `UNIQUE(channel, gateway_trade_no=trade_id)`
→ outbox. Waiting/expired notifies: 200 `ok`, no state change. `redirect_url` never fulfils.
