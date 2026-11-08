# Security, payment and UX review — 2026-11-08 UTC

Reviewed `main` at `7f36926744546cfe24e65c3c849c7d59c37b887b`, also the local checkout. This is a source review with small isolated behavior checks and primary-source internet research, not an independent penetration test or certification. No production configuration, traffic tests, payments, secrets or business code were changed.

Published tracking issue: [#26 — verified follow-up to #25](https://github.com/arifulcrypto/24goods-web/issues/26).

Evidence legend: **V** = verified in repository source; **T** = isolated behavior reproduced; **I** = inference/design proposal; **U** = not tested/accessible. Severity is an assessment of impact and prerequisites, distinct from delivery priority. See [external research](security-payment-references-2026-11-08.md) for upstream SHA-pinned examples, classic/recent advisories and limitations.

## Overall assessment and issue #25 corrections

Issue [#25](https://github.com/arifulcrypto/24goods-web/issues/25) exists and contains useful requirements, but is not evidence that every listed threat is exploitable. Its `file:///root/...` links are not usable GitHub source citations. Use commit-pinned repository links when triaging.

- **V:** Constant-time EPay signature checking with pinned MD5, merchant/order/amount/status validation, server re-query, database order locking, unique gateway transactions, outbox settlement and balanced ledger writes already exist. The buyer return URL does not fulfil. Relevant code: `payments/epay.py`, `modules/payments/{notify,notify_bepusdt,settle,ledger}.py`, migration `0001_initial.py`.
- **V:** Card secrets use authenticated encryption in `core/cardvault.py`, with a separate delivery process, column grants and tenant-bound AAD. Tenant sessions pin FORCE RLS; owner checks exist for order lookup/reveal. These are significant defenses; runtime enforcement was not checked on production.
- **V:** No direct SQL-injection, webshell execution, unauthenticated free fulfilment or cross-tenant card disclosure was demonstrated in the reviewed paths. This is not a claim that the entire application is free of those vulnerabilities. Bound SQLAlchemy queries and static frontend serving reduce the reviewed attack surface.
- **V:** ZPay's gateway URL, API URL, merchant ID and key are environment-configurable (`core/config.py:115`). The domain is a default, not an unchangeable literal. The real gap is one process-wide provider instance, an Alipay-specific adapter and a fixed channel schema, without a safe admin configuration lifecycle.
- **V:** Arbitrum, X Layer and TRC20 USDT channels already exist; BEpusdt manages watch addresses in its own console. Multiple provider instances and safe rotation of outstanding attempts remain work.
- **V:** Registration can already require an emailed six-digit code, and password reset already requires one (`modules/auth/router.py`, `codes.py`). Settings default registration email verification off. Credential change checks the current password for regular accounts but lacks optional verification/step-up. A separate recharge/payment-password feature was not established by this review.
- **V:** Chat already calls client-side `compressImage` before upload, and the server independently validates/re-encodes images. Long-press actions, recall and media quality preferences are enhancements, not evidence that all compression is absent.
- **V:** Dropping an empty string in optional order creation is not independently a bug: `normalize_value` deliberately permits omitted/empty optional input. The edit path has a separate real inconsistency described below.
- **U:** Xiaodou's actual protocol docs require login. Its homepage/SPA and generic EPay support elsewhere do not prove compatibility. Do not enable it for real payments before checking its merchant documentation and contract fixtures.

## Findings, ordered by security and payment impact

### F0 — BEpusdt HTTP 200 failure replies permanently acknowledge failed callbacks

**V + T (isolated handler); High payment reliability impact; P1, fix first.** `deploy/docker-compose.yml:144` pins BEpusdt `0716485`. In that exact upstream revision, [native POST acknowledgement](https://github.com/v03413/BEpusdt/blob/07164853c38ed43700d3abcaab78e832782d36fa/app/task/notify/notify.go#L148) checks only HTTP status 200, ignores the response body and marks notification success. The [retry selector](https://github.com/v03413/BEpusdt/blob/07164853c38ed43700d3abcaab78e832782d36fa/app/model/order.go#L359) then excludes it. This differs from upstream's EPay GET acknowledgement path.

Our `modules/payments/notify_bepusdt.py:173` returns `FAIL` on transient re-query failure, but `modules/payments/router.py:102` turns it into HTTP **200** with body `fail`. A customer can have paid while the order stays pending and BEpusdt stops retrying. Existing `test_usdt_v52.py:187` asserts the body and manually posts again; it does not establish that the real gateway will retry. The source-extracted handler returned exactly `200 fail` with a failed-pipeline stub. No real transfer was made.

**I:** Return a non-200 status such as 503 for retryable processing/re-query failures. Use 200 only for durably recorded/idempotent success and deliberately harmless waiting/expired notifications. Decide permanent validation rejection statuses explicitly. Add durable reconciliation to recover previously acknowledged-but-unsettled payments.

Acceptance: a signed paid callback with a temporary query outage leaves the order unfulfilled and returns non-200; the pinned upstream retries and subsequently settles once, creating one Payment, ledger transaction and delivery event. Simultaneous callbacks, invalid signatures and harmless status notifications retain their defined behavior. Test HTTP status and the upstream retry contract, not just response text.

### F1 — Sensitive order-input copies escape the retention job

**V; High confidentiality risk; P1.** `modules/payments/settle.py:333` copies `order.buyer_input` into an ordinary plaintext chat message using `client_msg_id=order-input:<id>`. `modules/orders/service.py:562` creates further copies when paid input is edited. `modules/jobs.py:175` clears only `Order.buyer_input` after 30 days for finished orders, leaving these message copies untouched. Data may include session tokens, as the source explicitly documents. Existing issue [#8](https://github.com/arifulcrypto/24goods-web/issues/8) covers order-field encryption; this extends it to copies and retention.

**I:** Replace message content with a typed order-information reference whose reveal is authorized, audited and short lived. Encrypt sensitive values with tenant-bound AEAD; migrate/redact historical copies and edits, and document backup expiry. Keep ordinary user chat policy separate. Avoid putting secrets in audit payloads, notifications or recall tombstones.

Acceptance: pay and edit an order with a synthetic marker, age the completed order, run retention twice, and check order fields, linked messages, history APIs and exports. No retrievable plaintext marker should remain in retained application data; backups follow an explicit expiry policy. Include buyer/staff and cross-tenant authorization cases. No production data was inspected during this review.

### F2 — Busy SSE streams defer session revocation checks indefinitely

**V + T; Medium, potentially High for staff streams; P1.** `modules/realtime/hub.py:124` calls `alive()` only after `queue.get()` times out. An event within every heartbeat resets the wait, so a revoked connection can continue receiving updates. Staff streams include buyer/order/message data. The `_alive` closure checks session existence and tenant only; role changes need a clear revocation policy as well.

Isolated reproduction used the actual `sse_events` generator with a callback returning false and three queued events: all three were yielded before the first callback invocation; the stream stopped only after the queue became idle. No live SSE connection was attacked.

**I:** Use a monotonic authorization deadline independent of event arrival, check before forwarding after that deadline, and optionally publish explicit revocation events. Recheck role/status at a bounded interval or enforce an authorization-version policy.

Acceptance: with events continuously arriving, logout/password reset/suspension/staff-role removal closes the relevant connection within the declared deadline; no post-deadline protected event is delivered. Check reconnection and subscription cleanup.

### F3 — OTP verification does not atomically consume a successful code

**V + T (mocked interleaving); Medium; P2.** `modules/auth/codes.py:106` atomically reads/counts attempts in Lua, but Python compares the MAC and then separately sends `DEL` at line 112. Two verifiers can receive the same MAC before either delete, both return true, and both proceed. This does not let an attacker guess a code without passing verification, but violates single-use semantics and can race password resets.

The source-extracted, unchanged `verify`, `_key` and `_mac` functions returned `[True, True]` under a deterministic Redis scheduling mock. **U:** this session did not run a real Redis/reset-endpoint race.

**I:** Calculate the submitted MAC in Python and compare/consume atomically in Redis, or perform a compare-and-delete claim after constant-time comparison and require exactly one successful claim. Bind purpose, tenant, identity and generation; retain the attempt cap and make replacement/expiration races safe.

Acceptance: exactly one of concurrent identical correct submissions succeeds; incorrect submissions still burn attempts; a stale verifier cannot delete a newly issued code. Add a real Redis integration test and concurrent reset test.

### F4 — Enabled challenge can silently fail open; action binding missing

**V; Medium configuration/binding risk; P1 before expanding coverage.** `core/turnstile.py:57` returns normally when enabled but the stored secret is missing or fails unseal. The frontend widget sets `action`, but the server never compares Siteverify's returned action. Hostname comparison is conditional on the response having a hostname; production test secrets are not explicitly rejected here.

**I:** Refuse enabling an incomplete configuration, expose a clear unhealthy state, and fail closed for configured protected operations. Reject test keys in production; validate the expected action and an approved hostname. Keep an explicit, audited recovery path and a self-hosted challenge alternative for regions where the external widget fails. Confirm current Siteverify semantics against [Cloudflare's documentation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).

Acceptance: enabled+missing/wrong secret, wrong action, wrong/missing hostname and replay fail; disabled works as configured; outage/fallback behavior is explicit and does not silently remove financial-operation protection.

### F5 — Confirmed order-information edit failure and optional-field inconsistency

**V; Medium functional impact; P1.** `OrderDetailPage.vue:217` allows editing any pending/paid order. `editInput()` at line 227 creates `FALLBACK_INPUT` even if input is disabled or product fetching fails. Backend `orders/service.py:542` rejects disabled configuration, returning generic validation. This gives a concrete path to “Please check your input / 请检查输入内容” for values the user cannot fix.

A second path: `buyerinput.normalize_value` returns `None` for empty optional input, but `orders/service.py:545` rejects it anyway. The UI permits confirming an empty optional field. A changed/inactive product can also make the frontend and existing order disagree about current configuration.

**I:** Return server-defined edit capability plus the relevant input schema in the order view, preferably snapshotted/versioned at creation. Never fabricate an enabled schema. Define optional clear-to-null behavior; preserve distinct required/too-long/disabled/state errors with bilingual messages. A failed config fetch needs retry/error UI, not a misleading editable fallback.

Acceptance: disabled, optional-empty, required-empty, multiline, maximum length, invisible pasted characters, product unpublishing/config change and network failure; exercise product buy, cart and order detail. Existing optional-empty creation should continue to work. Add a mounted component or browser regression test.

### F6 — Availability budgets do not bound all admitted work

**V code structure; I production impact; Medium; P2.** `uploads/service.py:38` reads the image bytes before waiting for `_image_slot`; a decode semaphore alone does not cap waiting buffers or parser-spooled uploads. SSE queues cap each stream at 200 entries but subscriber count is unbounded (`realtime/hub.py:62`); an HTTP request-frequency limit is not a concurrent-connection cap. Background notification/email tasks also need an explicit admission budget. No load or OOM test was performed.

**I:** Bound connections and queued uploads per account/IP/tenant/process, reject excess work early, put admission before buffering where feasible, and add storage quotas/orphan cleanup. Keep an image pixel/decoder budget and delivery isolation. Monitor queue depth, RSS, pool waits, Redis capacity and outbox age. Layer edge DDoS protection and origin restrictions above the application; a CAPTCHA is not volumetric DDoS protection.

Acceptance: on isolated staging with a stated memory budget, admitted work remains bounded and overload returns a retryable response while payment callbacks and delivery retain capacity. Never run a flood against production.

### F7 — USDT cancellation depends on a Redis-only payment-attempt record

**V; payment reliability risk; P1, already tracked in #23 / progress.** `payments/usdt.py:105` stores trade ID/address/amount/channel in Redis only. `orders/cancel.py:124` treats an absent record as no transaction ever opened and skips re-query. Redis restart/expiry can therefore permit cancellation while an upstream transfer exists; a subsequent valid callback may land in manual review rather than normal fulfilment. Automatic fund loss was not demonstrated.

**I:** Persist all payment attempts in PostgreSQL and use Redis as a cache. Keep cancelled/superseded attempts queryable; run bounded reconciliation with backoff, alerts and a durable settlement cursor. A cache miss means unknown until durable state is checked, not unpaid. Test Redis loss, process death after create, missed/delayed callback, payment during cancellation and old attempt after channel/price changes.

## Payment/provider implementation proposal

**I:** Separate provider instance from payment method. Admin fields should include label, protocol/version, gateway/query base, merchant ID, encrypted secret reference, allowed methods, currency, enable state and strict limits. Basic EPay v1 setup can expose URL/PID/key, but some providers require additional signing keys/version fields. Do not silently fall back to an unrecognized provider or weaker algorithm.

Persist a payment attempt with tenant, order/checkout, provider instance and immutable configuration version, merchant identity, amount/currency, method, upstream trade ID, secret-version reference, address/chain/asset/exact crypto amount, timestamps and state. Uniqueness must include provider-instance scope, since different merchants can reuse gateway trade numbers. Secrets remain outside API responses/logs; retire old configuration instead of deleting it while callbacks remain possible.

Every adapter must feed the same verified settlement path: pinned verification, merchant/order/amount/currency/status binding, query against the original trusted endpoint, row locks in a consistent order, transaction uniqueness, balanced ledger, audit and outbox. Freeze/reconcile old attempts on configuration/price/channel changes; never fulfil from a browser return. Validate externally configured gateway endpoints against SSRF, redirects, DNS rebinding and query credential logging. Private BEpusdt endpoints need an explicit internal-network policy, not a public-URL escape hatch.

For USDT, use watch-only addresses; distinguish chain and token contract, persist the exact allocated amount and receiving address, enforce positive amounts and a documented confirmation threshold, and retain old-address monitoring through late payments. Zero-amount/exclusive-address mode must stay disabled for fixed-price orders. Reject wrong network/token/amount, underpayment and conflicting chain transaction reuse. Handle expired/late/duplicate/multiple payments in a visible review queue; provider/address rotation applies to new attempts. Verify upstream callback acknowledgement/retry semantics against the exact deployed BEpusdt revision.

Useful references: Sub2API's provider identity snapshots and configurable EasyPay; New API's settings and transactional recharge; Dujiao-Next's provider/method separation; Saleor transaction idempotency; Medusa's shared row-lock discipline. None is a security certification. The [external research](security-payment-references-2026-11-08.md) identifies unsafe details not to inherit, including float tolerances, return-handler completion and plaintext settings.

## Requested access, verification and UX roadmap

- **P2, I:** Add an optional `site_visit` protection checkbox with a short-lived, server-verified tenant/host-bound clearance. Apply it to the actual storefront data routes as well as navigation; a frontend modal alone is bypassable. Plan SEO, accessibility, China reachability and recovery explicitly. Do not put interactive challenges on gateway callbacks or health checks.
- **P1/P2, I:** Make registration/email verification status explicit in settings; use purpose-bound single-use codes and current-password/step-up proofs for password/email/payout-account changes as policy requires. Prefer TOTP/WebAuthn for staff and high-risk financial changes. Do not add a second payment password until its product scope and reset/recovery model are defined. Apply IP/account/device abuse budgets without enabling victim lockout. Verification codes do not replace CSRF or authorization.
- **P2, V + I:** Both apps detect browser language initially, then persist the result (`i18n/index.ts:17`). Add explicit Auto/English/简体中文 choices, preserve only deliberate choices, use the ordered browser-language list and a `languagechange` listener in Auto mode, and keep URL overrides deliberate. Test blocked storage and both UI libraries.
- **P2, I:** Add long-press/right-click plus keyboard-accessible Copy/Reply/Pin actions. Clarify personal vs shared pins. Recall within 180 seconds must use server time, sender/tenant authorization, an atomic idempotent state change, a content-free tombstone and realtime updates. Exclude payment/delivery/system evidence. Revoke media retrieval promptly and remove unreferenced files asynchronously; recall cannot erase copies already downloaded.
- **P2, V + I:** Preserve existing client compression and server sanitation. Add Auto/Data saver/High quality with server-generated small/large variants, lazy loading and a tap-for-original option. Network Information APIs have limited availability; `effectiveType` describes estimated connection quality, not reliable Wi-Fi vs cellular classification. Use feature detection, optional `saveData` and a conservative user-controlled fallback. See [MDN](https://developer.mozilla.org/en-US/docs/Web/API/NetworkInformation).
- **P2, I:** Browser acceptance matrix: iOS Safari, Android Chrome, common embedded WeChat browsers and desktop Chrome/Edge/Firefox/Safari; 375px layout, 44px targets, safe areas, soft keyboard, clipboard failure, voice recording formats, hidden-tab reconnects, slow/offline network and duplicated taps. Backend idempotency remains authoritative. These visual/browser checks were not run in this session.

## Attack-case implications and remaining release gates

The external research documents actual recent advisories, including Paymenter race/transaction-binding findings (October 2026), Saleor upload XSS and account pre-hijack (2026), Adobe Commerce exploitation (2025), classic multi-party payment-state attacks (2011), and HTTP/2 Rapid Reset (2023). Their lesson is to test boundaries and all state writers; they do not establish the same exploit in 24goods.

For stack/ops comparison, [Starlette's multipart advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-2c2j-9gv5-cj73) explains why parser work must be bounded before business handlers. No vulnerable installed version was established here. [runc's maintainer advisory](https://github.com/opencontainers/runc/security/advisories/GHSA-9493-h29p-rfm2) corroborates the upgrade priority already recorded in [#6](https://github.com/arifulcrypto/24goods-web/issues/6); current production runtime version was not queried. Restore/backup proof remains tracked in [#7](https://github.com/arifulcrypto/24goods-web/issues/7). Preserve parameterized SQL, FORCE RLS plus explicit tenant filters, immutable financial rows, no public secret-bearing media, and process-specific credentials. A balanced ledger alone does not prove the commercial transaction was legitimate.

Before real money: repair the prioritized findings, verify gateway contracts with fixtures and staging, exercise forged/replayed/concurrent/late callbacks and cross-tenant attempts, test restore and secret rotation, and obtain the independent security review/pentest required by AGENTS.md. Do not label this source review as passing that gate.

## Validation performed

- Read GitHub issue #25 and current main through the GitHub connector; local HEAD matches the remote SHA. Main [CI run 42361897694](https://github.com/arifulcrypto/24goods-web/actions/runs/42361897694) succeeded; CodeQL run was skipped (opt-in).
- Ran unchanged `test_epay.py` and `test_cardvault.py` in an isolated temporary environment: **25 passed**. Copies were run outside the repository test tree to avoid service fixtures; this does not test the deployed dependency closure.
- Ran three temporary behavior probes: **3 passed**, confirming the problematic SSE behavior, modeled concurrent OTP acceptance, and the native BEpusdt handler's HTTP 200 failure reply. These are diagnostic probes, not fix regression tests; OTP uses a scheduling mock, and the extracted callback handler uses a failed-pipeline stub. Decorators/dependency defaults were removed only in that temporary isolated handler harness.
- Heavy test commands used `.dev/heavy.lock` after a memory check. No full local suite, PG/Redis integration run, dependency vulnerability scan, production penetration/load test, real gateway transaction or visual browser run was performed. Only documentation is changed in this branch; no fix is claimed.
