# v6 proposal: direct payment QR, provider trust, lightweight operations and renewals

Date: 2026-11-08 UTC. **Status: researched proposal; no features or production controls implemented by this document.** Extends [#25](https://github.com/arifulcrypto/24goods-web/issues/25), [verified findings #26](https://github.com/arifulcrypto/24goods-web/issues/26) and the [November 8 source review](research/security-payment-audit-2026-11-08.md). Implementation remains subject to milestone planning and the independent security-review gate. Security/payment fixes in #26 precede UI expansion.

Delivery tracking: [#27 payment API/QR and verification](https://github.com/arifulcrypto/24goods-web/issues/27),
[#28 security/operations and recovery](https://github.com/arifulcrypto/24goods-web/issues/28), and
[#29 monthly-service staff reminders](https://github.com/arifulcrypto/24goods-web/issues/29).

中文要点：可以后端直连并在本站显示二维码，但不能保证隐藏主站或上游身份，也不能规避支付平台的商户审核。真实密钥和成功验签不等于交易合法或已经结算。先补支付回调、持久化及核对，再增加支付验证、轻量监控、完整加密备份和代充到期提醒。本文件是需求与验收设计，不表示已上线。

Evidence: **V** verified source/docs; **I** proposed design; **U** unverified. Source/version details and vendor limits: [QR/operations research](research/payment-qr-ops-2026-11-08.md), [Web3/security research](research/web3-ops-risks-2026-11-08.md). No new runtime dependency or service is installed by this change.

## 1. API direct mode and truthful privacy boundaries

**I:** A buyer requests an attempt from 24goods; the backend calls the chosen provider; a typed response describes QR/native/deep-link/redirect presentation. The buyer page renders QR data using a bundled renderer or a backend-generated image. No merchant secret, provider credentials, raw provider HTML/JavaScript or arbitrary iframe goes to the browser. Existing authenticated-encryption/tenant/payment invariants remain mandatory.

```mermaid
sequenceDiagram
    participant B as Buyer browser
    participant A as 24goods API
    participant D as PostgreSQL
    participant P as Configured provider
    B->>A: Owned order + idempotency key + optional challenge
    A->>D: Validate price/version; reserve attempt
    Note over A,D: Commit before external network calls
    A->>P: Create payment with original provider configuration
    P-->>A: Typed QR/native/redirect data
    A->>D: Persist provider trade ID and presentation expiry
    A-->>B: Attempt ID, safe presentation, amount and expiry
    P->>A: Signed callback hint
    A->>P: Server query of the original transaction
    A->>D: Locked settlement + ledger + audit + outbox
    A-->>B: Authorized status update
```

### Provider capability matrix

| Provider/protocol | Verified capability | Adapter boundary / enablement gate |
|---|---|---|
| Z-Pay EPay-style v1 | **V:** `mapi.php` accepts a backend request and can return QR data, image or redirect information. | Preserve server-configured algorithm and precise money; parse each presentation type distinctly. Current 24goods adapter creates `submit.php` redirects only. |
| Other EPay/Caihong-compatible providers | **I/U:** a protocol-family adapter can share transport and signing when the contract really matches. | Contract fixtures for endpoints, field inclusion, encodings, signature/version, merchant and query fields, response/ACK rules; no universal compatibility claim. |
| Xiaodou | **U:** supplied docs need authorized login; the public catalogue returned 401 during the earlier review. | Obtain the current merchant documentation/export. No guessed `mapi.php`, key format, algorithm or compatibility. |
| Lantu / 蓝兔 | **V:** native payment API returns QR payload; its signing and uppercase callback acknowledgement differ from EPay. | Separate native adapter with request/notify/query fixtures; confirm actual product/domain/IP permissions and mobile limitations. |
| Alipay direct / 当面付 | **V:** official SDK models `alipay.trade.precreate` and `qr_code`. | Separate Alipay adapter with server-configured app/merchant/certificate identity, response verification and query checks. Actual product contract/online-use permission must be confirmed in the merchant account. |
| BEpusdt | **V:** existing native address/amount page covers three networks. | Fix #26's HTTP-ACK bug; persist attempts and validate chain/token/address/amount/finality. This is not the same adapter as EPay-to-BEpusdt. |

These are compatibility observations, not endorsements of provider solvency, regulatory status or merchant approval. QR code is a presentation format, not a shared payment protocol.

### What this hides, and what it cannot hide

| Observation | Effect of server API + local QR |
|---|---|
| Merchant API credentials and backend API requests in browser developer tools | Can be kept entirely server-side. |
| Visible third-party cashier page | Can be avoided only when the approved channel returns usable native QR data. A redirect-only channel still needs a redirect or must be marked unsupported. |
| Storefront hostname already visited by the buyer | Remains visible in the address bar, history, shared links and screenshots. |
| Upstream identity | May be inferred from decoded QR data, merchant/payee name, payment-app receipts, deep links, customer support or transaction metadata. Do not promise anonymity. |
| Main domain known to the provider | Callback/return URLs, required domain registration and other merchant data can disclose it. Backend direct calls also reveal the backend's public egress address to the provider. |
| Origin-server IP | Separate edge/origin routing can reduce direct exposure, but DNS/history, infrastructure configuration and other egress can still leak it. Requires an independent infrastructure review. |
| Complaints, merchant restrictions or payment-app risk controls | API mode does not prevent or lawfully bypass them. Keep truthful merchant/product/payee information and a usable support/refund route. |

An optional dedicated payment hostname can separate traffic and cookies, but requires an approved domain, narrow callback routing and an opaque one-time handoff. No cross-site sharing of a broad session cookie, no order access code in a URL, and no change to the real merchant identity. It is not a ban-evasion feature.

### Presentation and attempt contract

**I:** Return an object such as `attempt_id`, `order_version`, `presentation={kind, payload}`, server-formatted `amount/currency`, `expires_at`, `status` and authorized `status_url`. `kind` is an enum, not inferred from whichever field is nonempty. Payment URLs/payloads have length/scheme/provider-host checks. Native schemes are explicit per adapter; never navigate arbitrary upstream strings or inject SVG/HTML. Keep QR/status responses `no-store`; do not send third-party image requests by default. If remote-only images are unavoidable, use the existing bounded SSRF-safe fetch/re-encode design with an approved host list, not an open proxy.

Buyer device cases: desktop cross-device scan; same-phone supported deep link; unsupported embedded browser; expired QR; camera/gallery restrictions; clipboard denied; slow/offline state. Do not promise that a QR screenshot can be recognized by every wallet. Always display amount, expiry, expected merchant/payee where available and a status refresh/support path. UI may use a neutral channel label without making a false anonymity claim.

The database reserves one active create operation per order/version/provider using an idempotency key and a unique constraint. Provider timeouts are **unknown**, not safe to charge again: query the original merchant order before retrying or changing provider. Never hold row locks across provider calls. A cancel/reprice racing a provider response must recheck version/state before attaching presentation; retain the orphan/superseded attempt for reconciliation and late payments. A QR appearing successfully is not payment success.

## 2. Authentic credentials are necessary but insufficient

### Trust and evidence ladder

| Check | Establishes | Does not establish |
|---|---|---|
| TLS, pinned configured signature algorithm, valid signing key | An authenticated response from the configured identity under the assumed endpoint/key security. | That its operator is honest, the key is uncompromised, the configured merchant is ours or the transaction was authorized. |
| Original-provider order query | Status/amount/order/merchant consistency within that provider. | Independent settlement if the same compromised provider lies in both callback and query. |
| Approved merchant account + out-of-band configuration review | Ownership, scopes and intended payee/endpoint according to separately obtained evidence. | All future transactions or withdrawals being legitimate. |
| Clearing statements / merchant account settlement / on-chain receipt checks | Additional evidence about actual received value, subject to confirmation and source quality. | An irreversible payment in every rail or that suspicious funds are acceptable. |
| Controlled low-value end-to-end staging/approved live test | That the tested configuration exercised its success/failure/refund lifecycle. | Safety for all amounts, future provider changes or provider solvency. No live payment is authorized just by this plan. |

**I:** Provider state: `draft -> validation_pending -> restricted -> active -> draining -> disabled`, with a separate health signal. Enabling requires verified endpoint provenance, actual merchant ownership/authorization, product/method permissions, secret version, contract fixtures, approved callback/query paths, strict limits and an operator checklist. Important changes require fresh staff authentication and an audited second review/out-of-band owner verification where practical. A hostname supplied in a chat/email is not authority to change the gateway.

Keep provider config versions immutable and attempts bound to the original version. New secret/address/merchant applies to new attempts; old obligations drain with controlled verification overlap if upstream supports it. An expired/invalid merchant authorization stops new attempts and alerts staff. Do not silently switch a pending attempt to a fresh merchant. Validly signed but mismatched transactions go to a recorded review state, not fulfilment.

**I:** Schedule bounded preflight/health checks and post-payment reconciliation with jitter, timeouts, circuit breakers and independent operation budgets. Preflight checks are cached and cannot reserve database locks or run on every page view. Active checking means safe read-only probes by default; it does not mean charging automatically. Record source, check time, evidence freshness and `unknown` distinctly from healthy/unpaid. Use provider-instance-scoped transaction uniqueness and the shared settlement pipeline for callback, poll, cancel and reconciliation.

### Additional malicious-but-authorized scenarios to test

- A stolen staff session changes gateway URL, notify host, QR destination, payout account or renewal price. Require step-up, audited immutable change history, review and alerting; protect existing attempts.
- A real provider key is used for another merchant/order, or a signed callback belongs to an old configuration. Verify the full binding. Signature validity alone is not consent.
- A query returns paid while bank/chain settlement evidence is missing or contradictory. Hold high-risk cases and reconcile; do not loop queries until the desired answer appears.
- A domain expires or DNS changes to an attacker. Endpoint provenance, approved host policy, certificate/authorization checks and unusual destination alerts accompany versioning; revalidate on provider edits and network connections.
- A QR is replaced through malicious JS, browser extension, compromised staff UI or a poisoned provider response. CSP/bundled code, server-owned attempt identity, visible payee/amount and provider-response validation reduce risk; browser compromise remains a trust boundary.
- Valid staff API tokens should not gain access to new host-health, secret-management or raw-log routes through broad default scope mappings. Require explicit role and scope eligibility; test session-only operations and cross-tenant attempts.

## 3. Optional challenge before creating a payment

**V:** Current `PayIn` has only `channel`; both order and checkout pay routes rate-limit but do not run `require_turnstile`. Current Turnstile/PoW action lists do not include payment initiation. This is a requested feature gap, not proof of unauthenticated payment bypass.

**I:** Add an admin security setting for `payment_initiation`, with enable/disable, provider (`self_hosted_pow` or Turnstile) and an audited policy version. Make defaults explicit per environment; do not silently enable a barrier for existing buyers. Fix #26's fail-open/action-binding issues first. Protect single order, cart checkout and every alternate QR/create route on the server before an upstream create call. A frontend-only checkbox or middleware limited to one route is insufficient.

Turnstile difficulty is provider-managed; do not expose a fake numeric difficulty slider for it. For self-hosted PoW, offer bounded Low/Standard/High presets backed by server-signed difficulty, benchmarked on low-end mobile devices in a Web Worker. Use a measured solve-time budget and a hard maximum; the existing helper allows up to 40 bits, which is not a recommended payment UI default. Retry/accessibility and region-compatible fallback must remain usable. PoW increases bot cost but does not prove human identity or authorize a financial change.

Bind successful clearance to tenant, buyer/session, action, order/checkout ID, order version, provider/method, expiry and an opaque nonce. Consume atomically with an idempotent attempt reservation; the same idempotency key may retrieve the same outcome without creating a second charge. A changed price, channel or session requires fresh authorization. On configuration changes, define whether an existing attempt remains viewable; do not let a new protection policy invalidate a real payment in progress.

Never challenge callbacks, server reconciliation, delivery, necessary status recovery or payment-provider retries. Already-issued QR/status views stay owner-authorized and rate-limited. Verification failure must create no upstream attempt. Missing secret/outage must follow the selected explicit fail-closed/fallback policy. Rate limits and concurrency budgets apply before expensive verification; cap Siteverify concurrency too. CAPTCHA, OTP and staff step-up are different controls.

Acceptance: direct API bypass, two concurrent correct submissions, cross-user/order/tenant replay, expired challenge, altered difficulty, switch/reprice, expired session, duplicate taps, upstream timeout and a crash after reservation; no duplicate charge and no loss of callback processing. Test both languages, keyboard/screen-reader access, iOS/Android/embedded browsers and slow networks.

## 4. Small operations overview, inspired by 1Panel

**V:** The admin already has business stats, operation audit filtering/export and API-token expiry/last-use metadata. It does not have a host overview or unified login/task/system incident console. Compose already rotates container logs; keep that protection. Refer to the [1Panel DTOs and official docs](research/payment-qr-ops-2026-11-08.md#operations-and-security-references) for presentation ideas only.

**I:** Build one server-admin-only page with Overview, Security events, Tasks and Backups tabs. CS retains business-order visibility, not host or credential metadata. No terminal, container management, arbitrary command execution, file explorer or full 1Panel deployment is needed for this feature.

| Overview card | Minimum content |
|---|---|
| Environment / release | DEV/DEMO/STAGING/PROD badge, deployed commit/image digest, collected-at time, uptime, schema version; no secrets or public detailed health page. |
| Resource health | CPU/load, available memory/swap, cgroup memory limit/usage, disk free bytes/percent/inodes and selected volume growth; host vs container labelled. Missing collector data is `unknown`, not zero/healthy. |
| Service health | API readiness, DB pool saturation/query timeout trend, Redis capacity/errors, delivery heartbeat, outbox age/depth, failed task count. |
| Payment health | Per-provider active/draining/disabled state, query latency/error rate, rejected-signature counters, paid-but-unsettled count, mismatch/late-payment review backlog. |
| Recovery / maintenance | Last complete off-host backup, last successful restore test, key/certificate review due, software advisory relevance and next maintenance owner. |

### Collection and resource limits

Use a narrow unprivileged host collector on a timer if host data is needed; it writes a bounded sanitized JSON snapshot into a dedicated directory mounted read-only into the app. The API only reads known metric fields and caches them. Do not mount the Docker/LXD socket, host root filesystem, broad process environment or delivery private keys into the API. A socket mounted `:ro` still permits privileged daemon requests; it is not an authorization boundary. Do not give a dashboard root or delivery credentials.

Proposed initial budget: collection 30 seconds, visible-page polling 30 seconds, pause in hidden tabs, bounded 24-hour aggregates and a 10–20 MiB collector target subject to measurement. No Prometheus/Grafana/Elasticsearch stack in the first iteration. The collector should survive API restarts; an external heartbeat is needed to alert when the whole host is down. Suggested initial alerts: disk <15% or <2 GiB, inode pressure, memory near process budget for five minutes, repeated OOM/restart, failed backup, stale delivery/reconciliation and repeated provider mismatches. Thresholds are configurable proposals, not installed values or universal guarantees. Alert deduplication, hysteresis, recovery notices and escalation prevent notification floods.

## 5. Logs and defensive detection

### Minimum event contract

All event families use UTC event time and ingestion time, environment, tenant scope where applicable, event/request/task ID, owner/actor type and ID, resource type/ID, action, outcome (`success/denied/failed/unknown`), severity and a stable reason code. Financial state changes retain before/after records excluding secrets. Add the status field explicitly; do not infer every failure from English strings.

| Family | Additional fields / examples |
|---|---|
| Operation log | Resource, owner, action, changed field names, policy/config version, actor/session or API-token attribution, date and outcome; reveal/export/config/role changes. |
| Login / security log | Success/failure, trusted-proxy-derived client IP, approximate IP country/region/ASN with data-source version, bounded escaped User-Agent/browser family, attempted identity hash, MFA/challenge outcome, session issuance/revocation. No passwords, OTPs or cookie values. |
| System log | Service, release, severity, stable error code, correlation ID, restart/OOM/collector status; sanitized operator-visible detail only. |
| Task log | Task name/ID, owner/service, scheduled/start/end/heartbeat, attempt and next retry, duration, outcome, bounded redacted excerpt and link to approved diagnostics. |
| Payment / reconciliation log | Original provider/config/merchant references, local and upstream trade IDs, signature result, exact expected/observed amount/currency, query evidence, review reason and settlement/outbox IDs. Never store signing secrets or full QR bearer payloads. |

IPs from arbitrary request headers are untrusted. Honor forwarded client identity only through the configured trusted proxy chain. Location is approximate, not the visitor's physical address, and VPN/CGNAT/IPv6 make certainty impossible. Prefer a local licensed/updated geolocation database; do not leak every visitor IP to a free third-party API. UA, location, referrer and request IDs are hints, not proof of identity or reasons for an automatic financial action.

**V:** Application access logging intentionally omits queries, but both Caddy files enable ordinary JSON access logs without an explicit query filter. OAuth `code/state` and EPay callback parameters travel in query strings. **I:** Add edge-level allowlisted fields/query redaction and preserve default credential-header redaction. Do not log all request headers/bodies. Test at edge, app, tracing, exception and exported-log layers using synthetic secrets. Never render logs as HTML or interpolate them into shell commands; retain existing CSV formula protection and bound all pagination/export work.

Retain critical/auth/financial events predictably; sample or aggregate noisy bad signatures/scans with counters so an attacker cannot exhaust disk through logs. A proposed starting retention is 14 days raw edge traffic, 90 days auth/task detail and 180 days operational audit, with separately governed financial records; reconcile with existing retention, legal requirements and backup expiry before enablement. Use off-host immutable summaries/exports when tamper evidence is required: append-only app grants alone do not stop a compromised DB owner. Apply retention by tenant/environment and record retention-policy changes.

### Useful detection before a honeypot

Track failed-login diversity, success after unusual failures, new staff location/device, failed MFA/challenges, signature rejection spikes, money mismatches, repeated IDOR denials, access to secret-file paths, 404/403 rate, unusual SSE/upload admission, provider-config changes and missing task heartbeats. Correlate several signals; search-engine user agents and large request counts alone do not establish malice. Bots can forge UA strings, and real customers may share one IP. Alerts should link to filtered sanitized events and a runbook, not automatically delete accounts or refund orders.

### Optional low-interaction decoy

**I:** Start with canary routes that return a static 404/deny response and a sampled security event for probes such as `/.env`, `/.git/config` or a deliberately unused login path. No vulnerable runtime, fake usable credentials, uploaded executable, real database access or callback to attacker infrastructure. Existing real routes/health checks must never become decoys. Disable by default in development tests; tune noisy internet scanning separately from staff alerts. Automated blocking requires corroborating evidence, bounded TTL and recovery. This is detection, not retaliation or credential phishing; it does not replace WAF/edge resource controls.

## 6. Credential and software maintenance

**V:** Tenant setting secrets already have encrypted storage, `updated_at` and masked/configured status; staff API tokens have scopes, expiry and last-use metadata. Existing status output does not establish that an upstream credential is currently authorized, nor expose a complete review schedule.

**I:** Maintain secret **metadata**, not a second secret copy: name/type, owner, environment, provider/merchant identity, version, fingerprint/public identifier, created/rotated/reviewed/due/expiry times, last verified time, allowed scope and last use. Display `configured`, `decryptable`, `upstream authorization verified` and `settlement checked` as separate states. For vault private material the API sees only expected key IDs/worker health, never the private key. Reminders go to authorized staff, without secret suffixes/values in email/TG.

Have an explicit review schedule (for example monthly ownership/scope review, expiry warnings at 30/7/1 days and incident-driven immediate replacement), rather than blindly rotating every type at one interval. Provider keys, database passwords, TLS certificates, API tokens and vault wrapping keys have different safe procedures. Use out-of-band recovery and dry-run/rollback checks; support staged old/new versions while legitimate attempts drain when the upstream permits overlap. Never delete a vault key until all related stock/backups and retention obligations have a tested recovery path. Backup decryption keys must not live solely beside the encrypted backup.

Software inventory includes OS/kernel, LXD **on its host**, Docker/containerd/runc, Caddy, PG/Redis, Python packages, frontend build dependencies, BEpusdt and any cohosted PHP/admin panels. Record deployed version/digest, advisory feed/check time, exposure, affected/not-affected/unknown result, owner and remediation deadline. Do not equate "latest available" with "tested safe" or "scanner clean" with audited. Verify signed releases/digests; stage upgrades, back up, test migrations/restore and keep compatible rollback artifacts. Critical exploited issues require rapid triage; reminders should distinguish absent PHP from a vulnerable cohosted PHP process. New [PHP2Shell/LXD findings](research/web3-ops-risks-2026-11-08.md) are separate from the runc problem already tracked in #6.

## 7. Complete, encrypted and recoverable backups

**V:** `scripts/prod.sh:115` currently creates a full logical PG dump, upload tar and a tar of `.env/env/secrets`, with 14 generations and an optional nightly cron. The script provides permission controls and SSH copying but no archive encryption, whole-set completion manifest, checksum verification or backup lock. It does not include `bepusdt_data`. A container-log rotation policy already exists; do not describe all logs as unbounded. Installed cron, remote storage encryption and last restore success were not inspected.

**I:** Treat this as an M1 release gate linked to #7. A local copy on the same disk does not survive host loss. Split operational data and encrypted vault/config recovery material with separate least-privilege access and off-host storage. Prefer a proven backup tool with authenticated encryption and retention verification, or age-encrypted archives plus a manifest when resources demand it; choose the tool in implementation research, not by adding a dependency here. Include immutable/offline copies protected from compromised production deletion.

Minimum backup set:

- PostgreSQL logical full backup initially; migrations/role-grant reconstruction and application release/digest metadata. For a proposed production RPO of 15 minutes, add tested base backups plus WAL/PITR; daily dumps alone have approximately a one-day loss window. Do not call a plain dump incremental.
- Private uploads and references with a consistent snapshot or documented deletion/quiescence policy; a DB snapshot and a later live `tar` are not automatically a consistent cross-store snapshot.
- BEpusdt database/config/watch-address state through its supported consistent backup or SQLite backup API, including WAL-related consistency as applicable. A blind live-volume copy is insufficient. No wallet seed/private signing material should be added to this watch-only service.
- Encrypted tenant-settings recovery key, delivery vault keys/key IDs, required service config and managed certificates with separate access/recovery controls. Exclude logs/caches/temp data unless deliberately needed.
- Manifest: environment, backup ID, app/schema/version, timestamps, component hashes/sizes, encryption/key reference, PG timeline/LSN if used, off-host receipt and final completion marker. Publish/mark complete atomically only after every required component and verification succeeds.

Serialize nightly/manual jobs with an independent backup lock and unique backup IDs; never reuse the dev test lock. Ignore partials when choosing recovery points. Quota backup staging, clean failures safely and prune only after verified off-host completion. Retention applies to whole sets and WAL dependencies, not three independently counted globs. Proposed initial retention: seven daily + four weekly copies and a separately approved longer-term/offline policy; align secret-data deletion requirements and costs before deployment.

Restore on an isolated network with outbound email/TG/webhooks disabled and new environment-specific credentials. Verify schema, RLS/grants, ledger invariants, synthetic vault reveal, private-media authorization, BEpusdt state, durable attempts and reconciliation checkpoints. Reconcile post-backup provider payments **before** re-enabling delivery: replayed outbox entries and a rolled-back DB can otherwise redeliver paid goods. Never restore an old backup over a live system from a web button. Record measured RPO/RTO and a restore report monthly/after major changes. Candidate targets (not achieved): dev/demo RPO 24h/RTO 8h; production RPO 15min/RTO 2h after WAL/restore evidence. Offsite heartbeat alerts when backups stop even if the host itself is down.

## 8. Dev/demo/staging/production separation

Use distinct DBs, Redis ACL identities, keys, merchant credentials, watch addresses, webhook hostnames and notification destinations. DEV/DEMO must visibly mark simulated payment and cannot send real money. Production should fail startup when mock providers/test captcha keys or demo seed credentials are active. Keep existing mock-mode guards; verify them rather than assuming a new environment badge enforces isolation. Never copy a production `.env`, vault key or unredacted backup into a normal developer checkout.

Staging uses synthetic/anonymized data and restricted/test recipients. Restore drills need an explicit network fence. A schema/env capability manifest and release inventory support compatibility checks; dashboards may show summarized state, not arbitrary environment dumps. User authorization for this documentation push is not permission to alter real credentials, deploy new services or make test payments.

## 9. Manual monthly-service expiry reminders

**V:** Existing order expiry is an unpaid-order deadline and card expiry belongs to stock. Neither establishes a recurring-service entitlement/reminder model. Existing SMTP/TG and outbox jobs can be reused with explicit new task ownership; delivery's topic allowlist must remain scoped.

**I:** Add a small staff-managed service entitlement linked to tenant, buyer and original order: service label, assigned owner/CS, status, start/end time, billing timezone, period policy (`calendar_month` vs fixed days), grace policy, cancellation/renewal markers and source payment/order. No automatic wallet debit or subscription charge. Start the period from actual service activation/delivery, not merely order creation or payment unless the product policy explicitly says so. A reminder is an obligation to contact, not evidence that a customer owes an unapproved charge.

Renewal creates a separate server-priced order and a unique renewal-period reference. Extend once only after verified payment plus the required staff service-completion confirmation; do not extend on callbacks twice or from a staff reminder click. Define early renewal as extending from the existing end, lapsed renewal as starting from reactivation, month-end clamping (Jan 31 -> Feb's last day), leap years and timezone/DST behavior. A refund, cancellation or service change adjusts future reminders through versioned state, not by rewriting financial history.

Defaults proposed for staff notifications: 7/3/1 days before expiry, on expiry and a daily deduplicated overdue summary; configurable per product/tenant and staff timezone. Unique reminder key `(tenant, entitlement, period_version, reminder_kind, scheduled_at)` prevents duplicate scheduling. External SMTP/TG delivery is at least once unless the provider supports idempotency: a send may succeed before the completion record commits. Use stable notification IDs, bounded retries and an explicit uncertain-send outcome; do not promise exactly-once external delivery. Renewal/cancellation/version change invalidates obsolete queued reminders with a final check at send time. Store UTC deadlines and an explicit business timezone; date-only reminders use that timezone consistently.

Email/TG messages contain environment, masked buyer/reference, service label, due date and an authenticated admin link only. No login credentials, session token, payment key or full customer-supplied top-up material. Restrict bot destination edits, log them and test destinations without bulk notifications. Customer reminders/automatic recurring billing are a separate future scope; the first delivery notifies admin/CS only as requested.

Acceptance: end-of-month/leap-year/DST, early/lapsed/partial/failed/refunded renewal, concurrent duplicate callbacks, manual due-date adjustment with reason/audit, cancelled service, server down across a reminder time, worker retry, send-success-before-recording crash and invalid notification configuration; no duplicate extension or scheduled record, bounded/identifiable external-message retries, and no secret in notification/logs. One operator page with upcoming/overdue/assigned-to-me filters is sufficient.

## 10. Delivery order and review gates

1. Fix #26 payment ACK, durable attempts/reconciliation, sensitive-input retention, session/OTP/challenge defects. Add backup-set/encryption/restore proof before real money.
2. Implement provider-instance versioning, explicit capabilities, direct QR and callback/query contract fixtures, including adverse upstream responses. Validate the merchant's real product authorization independently.
3. Add optional payment challenge after its atomic and fail-closed semantics are proven; keep payment recovery/callbacks operational.
4. Add minimal overview, structured logs/tasks, alert budgets and key/software review metadata; defer advanced metrics infrastructure and optional decoys.
5. Add staff-only monthly-service reminders using the existing outbox/notification infrastructure.

Each implementation PR updates progress, has EN/zh-CN strings, targeted local checks under the heavy lock, full GitHub CI and relevant security/payment/mobile reviewers. Proposed dependencies must be named and reviewed. This document does not weaken the independent review/pentest gate or declare the current project safe for real money.
