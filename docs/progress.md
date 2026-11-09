# Progress

Update this file in every PR. Phases are defined in [review-v2.md §8](review-v2.md#8-revised-plan-and-timeline-solo-developer--agents-honest-estimate).

## Current state — hand-off (read this first in a new session)

**Production deployed 2026-11-09 01:30 UTC: `a8160cb`** (#50 + #51, owner-approved) from `e1fb9e7`;
no migrations. Pre-deploy backup with the installed v1 script, sha256-verified off-host copy in
`/root/kesiv-backups/predeploy-20261109T012730Z/`, `pg_restore --list` OK (40 tables). Rollback:
retag `ghcr.io/arifulcrypto/goods24-{api,web}:rollback-e1fb9e7` to `:main` and `up -d`. Smoke all
pass; api->BEpusdt 200. Demo also on `a8160cb`.

**#58 quick items (2026-11-09, `fix/issue-58-quick`):** policy pages name no governing-law
jurisdiction (owner: no Singapore for now) and carry no contact address; `SiteFooter`
("Kesiv · Contact: support@kesiv.com") is the single place, now also on article pages. USDT order
X Layer -> Arbitrum One -> TRC20. Admin Orders: payment-method column + filter (`channel`, `none`).
Unpaid-order cap default 2; order creation throttled per account (3/60 s) and per IP (10/300 s,
looser for carrier NAT) on buy-now, checkout and quote accept, with one high-severity
`risk.order_flood_detected` audit row per identity and window (`orders/riskctl.py`). Declined:
EPay amount jitter (see #58 comment).

**Production deployed 2026-11-09 04:00 UTC: `42de235`** (#53 policy pages/help + #56 subscriptions,
migration 0014; owner-approved). Backup `/root/kesiv-backups/predeploy-20261109T035854Z/` (sha256
match, 40 tables); rollback images `:rollback-a8160cb`. **Cloudflare Redirect Rules (#54):**
`kesiv.com`/`www` -> `https://shop.kesiv.com{path}` (302, query kept) and `http://shop` -> https
(301). HTTP->HTTPS lives at Cloudflare, so the origin's `http_redirect` from #53 was reverted
(a plaintext hit on :8088 fails again, as `prod.sh smoke` expects).

**Subscriptions v7 (2026-11-09, `feat/subscriptions`, spec `docs/review-v7-subscriptions.md`):**
products can be marked as subscriptions (`products.subscription`); after-sales defaults to one
calendar month (`products.after_sales_months`, tenant `after_sales_default {unit,value}`; legacy hours
still honoured); job `sync_subscriptions` turns delivered subscription orders into services (new or
renewed, audited as `system`); Services page shows order no./product/buyer/source/days left,
renewal candidates and a date picker instead of pasted UUIDs; buyers see their service period on
the order. Migration `0014_subscriptions` (additive). Not built: buyer self-service renew, recurring
charge. Follow-up (security review): a refunded *renewal* order does not yet shorten the
service — staff adjust the due date by hand; with two open services for one buyer+product the job
skips the order and logs it for staff.

**Policy pages v2 + help center (2026-11-09, `docs/legal-help-sg`):** Terms / Privacy /
After-sales / Platform rules rewritten (venue positioning, Singapore law, generic payment and
sign-in wording, no hosting/vendor details; footer = brand + law + support@kesiv.com), help guide
rewritten and 6 general help articles added (EN + zh-CN); Help page translates category names,
reloads on language change, links the policies; site footer on Help/Me. Prod Caddy answers
plain-HTTP hits on :8088 with a 308 to https (was a 400 from Cloudflare for `http://shop.kesiv.com`).
See `docs/legal/README.md`.

**Legal text (2026-11-09, `docs/legal-terms-privacy`):** real Terms of service + Privacy policy
replace the template banner (#48 §5, #40); see `docs/legal/README.md` for the approach and the
open governing-law decision.

**CI/local speed-up (2026-11-09, `ci/faster-pipeline`).** Measured on its own PR run: backend tests 616 s -> 107 s. Before: ~16 min, backend pytest
616 s on the critical path, then image builds waited on it (web 119 s, mostly a single-threaded
xcaddy compile), then E2E. Changes:

- `create_app` reuses a once-per-process analysed route table (`_route_table`). FastAPI's
  `include_router` re-analysed every endpoint (~0.5 s) and the suite builds one app per test:
  the same 41 tests went 40.8 s -> 15.0 s locally. Each test still gets its own app/middleware.
- Images build in parallel with the tests (nothing is pushed until `publish`, which now needs
  backend + frontend + e2e + semgrep + gitleaks), with a buildx GHA layer cache; the web
  Dockerfile installs dependencies from manifests before copying sources. A weekly
  `OS_PATCH_EPOCH` build arg keeps cached `apk/apt upgrade` layers from going stale.
- A `changes` job skips backend/frontend/image/E2E work a PR does not touch (pushes to `main`
  always run everything). The image matrix skips per step, never at job level, so its
  per-matrix required checks still report.
- Backend installs with uv (`uv pip install --system`), pytest prints `--durations=15`.
- `scripts/dev.sh check`: changed-files-only local loop under `.dev/heavy.lock`.
**Issues #48 / #49 triage + fixes (2026-11-09 UTC, `fix/issues-48-49`).** #49 is the more accurate
review; where they disagree #49's reading of the code was right. Fixed, with regressions:

- **49 A** cart `buyer_input` (may hold a session token) no longer touches `localStorage`: only
  ids/qty/ticks persist, legacy values are purged on load, and inputs are dropped on sign-out or an
  account switch (guest -> signed in keeps them: same person, same tab).
- **49 B** list summaries now carry `buyer_input` (null = off), so Home/Search quick buy shows the
  form; BuySheet treats a missing field as unknown and fetches the product before allowing submit.
- **49 C** one stock rule (`lib/stock.ts`) for row/card/detail/quick buy: null unlimited, 0 sold
  out, positive caps quantity -- manual stock 0 no longer shows an enabled Buy.
- **49 D** catalog feed, Home strips/categories and My Account's recent strip refetch on a language
  switch; responses from the old locale are discarded.
- **48 §1.1** Vant's icon CSS `//at.alicdn.com` woff fallback is stripped at build time and the
  build now **fails** if any emitted CSS loads a remote `url()` or mentions alicdn (both SPAs).
- **48 §1.2** a 428 on a non-catalog route (My Account, chat, cart) shows an inline access banner
  instead of a silent failure; reads retry after clearance. No blanket gate over recovery routes.
- **48 §2.1/2.2/2.3** one manual-delivery badge + one note per card (BuySheet badge only); admin
  Services page padding; `/me/settings` (language, media prefs, credentials, delete account; closes
  #38) and zh `自动（浏览器）` -> `自动`.

Not done, deliberately: SSE `withCredentials: true` (same-origin EventSource already sends
cookies; #49 is right); Turnstile `connect-src data:` (unverified console line); random payment
tail and USDT rate buffer (no provider evidence; need a pricing decision, AGENTS §1.3); legal
banner removal (needs approved text, #40); agent skill (exists: `skills/24goods-admin`). Image
thumbnails for product grids are a real win but need the public-read vs CPU-admission split #49
describes -- separate change. **Owner:** disable Cloudflare Web Analytics automatic setup for
shop.kesiv.com in the dashboard (our API token has no RUM scope); do not whitelist the beacon.

**USDT receiving wallets replaced on production (2026-11-09 00:36 UTC, owner request).** BEpusdt on
c4: Arbitrum One and X Layer -> `0x5bde040754d531a6c25027845adc99b2d8100741`, TRON TRC20 ->
`TTAWAWPXTouM5Pztv6Tu5dhqFZXJtSh1N2`. The owner supplied X Layer as `XKO5bde…`; `XKO` is OKX's
display prefix for the same EVM `0x` address. Done with zero waiting BEpusdt orders; SQLite
backup first (`/opt/kesiv/backups/bepusdt-prewallet-20261109T003615Z.db`). The old wallets
(`0xe2dc…653c`, `TQkeo…vMu5`) are no longer watched -- any late transfer to them must be
reconciled by hand.

**v6 blockers closed + owner UX round (2026-11-08 UTC).** Read [HANDOFF-v6.md](HANDOFF-v6.md) for the
implementation inventory. Since that checkpoint, on `feat/issues-25-30-hardening`:

- Both Medium re-review blockers are fixed with regressions that fail without the fix:
  USDT-only cancellation no longer falls through to an unconfigured legacy EPay gateway (the legacy
  key, not the current channel, gates the drain, so an old EPay URL stays drainable after a switch);
  and a durably committed settlement now travels with unresolved evidence
  (`UnresolvedEvidence.settled`) so its broadcast and duplicate-payment alert still fire while
  cancellation keeps failing closed. Admin Operations shows translated event families.
- Owner UX round: four-tab storefront (Home / Messages / Cart / My Account) with Orders moved under
  My Account plus `?tab=` deep links; presence defaults to busy for support and offline for admin
  (never online); CS quick replies (built-in bilingual starters + tenant-scoped, RLS-enforced
  `cs_reply_templates`, personal vs admin-only shared); staff browsing the shop are told which
  account is in the way and sent to the console inbox instead of buyer chat (`/chat` is buyer-only).

Read-only security-reviewer and payment-auditor passes were run on those commits; both found the
first cut of the cancel fix **fail-open** (it cancelled over an unverifiable legacy pay URL) and
both findings are fixed, with regressions that fail against the fail-open version:

- `cancel.py` now gates on the payable's own channel: with no usable legacy key an EPay payable
  raises 503 (fail closed) and only a USDT payable cancels. While the key is present the legacy
  query still runs for USDT, so an EPay URL issued before a switch stays drainable.
- `reconcile.py` now carries committed outcomes out of **every** post-commit failure (`_carrying`),
  not only the unresolved/unavailable flags, and no longer re-queries an already-settled original
  (which replayed the duplicate-payment audit trail and re-broadcast on every tick).
- `cancel_group`'s emit is best-effort so a failed broadcast cannot turn the 503 into a 500; a
  teammate's private reply template answers 404 rather than confirming its id.

**The reviews above were NOT independent** (same session wrote and reviewed). A cold-started
re-review on Opus 5.5 (2026-11-08) found more on the money path, which `sec/legacy-pay-url-marker`
fixed in **#44 (`ad14f01`, merged)** after full CI and a second cold auditor pass that found no remaining cancel-over-money path (ADR-027):

1. Cancel inferred "could a legacy URL exist?" from the current channel / key / provider history.
   Fail-open after a channel switch, a provider cutover, or full de-configuration (#32); and a
   fail-closed false positive -- every EPay cancel 503'd for a tenant on durable providers with no
   env key. Fixed with a durable `legacy_pay_urls` record (migration 0013, backfilled).
2. Reconciliation carried committed outcomes only through `Unavailable`; any other exception, or a
   `wait_for` timeout, lost the broadcast and duplicate alert permanently (#33). Now carried
   through every exception and collected in a caller-owned sink.
3. The attempt was marked settled only after the settlement committed, so a crash in between
   replayed the duplicate audit row and alert. The mark now rides in the settlement's commit.
4. Merging outcomes kept one duplicate trade, dropping a refund alert when three originals paid.

Not affected: **production** (`7f36926`) predates all of this code -- the defects were never live.

Verified on the demo after deploying `ad14f01` (migration `0013`): FORCE RLS restored on `orders` and
`checkouts`; `legacy_pay_urls` RLS-forced, `app_api` = `INSERT, SELECT` only; as `app_api` an insert
is idempotent, other tenants see nothing, `UPDATE`/`DELETE` are refused, a cross-tenant insert
violates the policy. **The backfill processed zero rows there** (the demo had no pending payables), so
it is first exercised for real on production: record the pending-payable count before deploying and
check it equals the marker count after.

Also 2026-11-08: the demo had SEO indexing **on** (mock products and a mock gateway indexable on the
owner's domain); turned off in the DB and it survives redeploys (`seed` never touches it). robots.txt
now refuses training crawlers only; AI search indexers and user-triggered fetchers are allowed (#43).

**Production deployed 2026-11-08 18:42 UTC: `e1fb9e7`** (owner-approved), from `7f36926`, applying
migrations `0010`-`0013`. Pre-deploy backup was taken with the installed v1 script, validated with
`pg_restore --list`, and copied off-host with matching sha256 to
`/root/kesiv-backups/predeploy-20261108T181022Z/`. Rollback images are pinned on c4 as
`goods24-{api,web}:rollback-7f36926`; all four migrations are additive (new tables only), so
**rollback = retag those images, no DB restore**. Verified: revisions `e1fb9e7`, alembic `0013`,
FORCE RLS on all payment tables, `scripts/prod.sh smoke` all pass, `via` gone + CORP present
(`Caddyfile.prod` had drifted from `Caddyfile` -- fixed in #46), `/ai.txt` live, robots refuses
training crawlers only, api->BEpusdt HTTP 200, all 14 background jobs succeeded post-deploy.
Prod had **zero** pending payables, so the 0013 backfill processed no rows (as on the demo).

**`main` is branch-protected** (7 required checks, enforced for admins, linear history). Merge with
`gh pr merge --squash --auto`.

**Backup trap (fixed in code, not on c4):** c4 still runs the **unencrypted v1** backup script from
cron; `deploy/backup.sh` is v2 (age) and c4 lacks `age` and both recipient files. `prod.sh backup` /
`install-backup-cron` used to overwrite the working cron script with v2 and silently break nightly
backups; `install_backup_script` now refuses until c4 is ready. Installing age + recipients is the
owner-only part of #28 -- until then backups contain plaintext secrets.

**Next (owner):** the small live USDT test transfer on https://shop.kesiv.com (now on current code);
#28 age recipients; #30 rotate the Xiaodou secret/PID.

**Merged and deployed (2026-11-08 UTC).** The above landed as PR #31 (`b2e7a1b`) with full CI
green; two follow-up PRs merged on top:

- **#37 → `4d079c9`** — container memory was reported as "Unknown / Unknown" because the overview
  collector read only the cgroup **v2** paths and this host is still on **v1** (older LXD, #6). Now
  falls back to the v1 hierarchy, treats v1's `LONG_MAX` "unlimited" sentinel as unknown rather
  than an ~8 EiB budget, and keeps v2 precedence. Verified live in the running container: **161 MB
  / 192 MB**. Also: the admin Operations page had no horizontal padding because its root carried
  only `.operations`, not the shared `.page` class.
- **#41 → `fed2927`** — the repository had **no LICENSE at all**. Adds a proprietary
  all-rights-reserved LICENSE (prohibited uses, machine-learning use, payment integrity, logging
  as evidence, good-faith security-research carve-out), `/ai.txt`, `robots.txt` refusal for 20
  training/answer-synthesis agents (search crawlers deliberately still allowed, with a test), a
  one-time console copyright notice in both SPAs, and `docs/legal/README.md`.
- **#42 (open)** — a 422 showed only "Please check your input" while the API was already sending
  `"<field>: <constraint>"`; `toastError` discarded it. Now appended, for the `validation` code
  only, so log-oriented prose from other codes cannot reach a toast.

**Deliberately not built:** DevTools detection/blocking, requested by the owner. Everything shipped
to a browser is already on the visitor's machine and `curl` fetches the bundle without executing
any defence, so it stops nobody who is actually copying the work while breaking assistive
technology, hanging some mobile browsers, resembling malware to scanners, and locking staff out of
diagnosing a customer's payment problem. Reasoning in `docs/legal/README.md` and `notice.ts`.

**Also deliberately not built:** VPN/proxy/VPS and "genuine location" detection (#39). The console's
existing wording — IP and headers are untrusted hints, location is not inferred — is correct and
should not be weakened into a verdict staff act on.

**Still not established:** the independent pentest before real money (M1 gate) has not happened,
and the owner has stated an intent to make a small live USDT transfer anyway. No key rotation. The
Xiaodou merchant secret and PID should still be treated as exposed (#30).

_Last updated 2026-11-08 UTC._

- **Prod** https://shop.kesiv.com (c4, IIJ Japan) runs **`7f36926`** (v5.2 + #23), verified
  2026-11-08 via `scripts/prod.sh status`. That is **15 commits behind `main`**, missing migrations
  `0010_payment_attempts`, `0011_operations_renewals`, `0012_cs_reply_templates`. `0010` matters
  most: `PaymentAttempt` is the durable attempt binding the USDT reconcile path depends on, so real
  USDT testing on prod today exercises v5.2, not the current pipeline. **Real BEpusdt v1.24.3 is
  already running there** (compose profile `usdt`) with the owner's three wallets configured —
  (replaced 2026-11-09: Arbitrum One and X Layer share `0x5bde…0741`, TRC20 is `TTAWA…1N2`). Nothing to configure; the
  gap is the deploy. c4 has ~1.8 GB RAM free vs this box's ~180 MB.
- **Demo** https://demo.kesiv.com runs `:main` — updated **manually** via
  `/opt/24goods-demo/deploy/demo-update.sh`; there is **no** `goods24-demo-update` systemd timer on
  this host (checked 2026-11-08), so it only moves when invoked. `seed` is additive — no deletes —
  but it writes an agent status only when none exists, so a stale seeded row persists across
  deploys (the demo's support agent was left reading "online" until corrected directly in the DB).
  USDT here is the **simulator** `mockusdt`, by design; real wallets stay on c4 so two services
  never watch one address set.
  Logins: `/opt/24goods-demo/deploy/secrets/demo-credentials.txt`.
  Demo config set 2026-11-07: rate limit 120/60 s, Telegram on (owner chat), Alipay + 3 USDT channels on
  (USDT = simulator `mockusdt`; env in `deploy/env/{api,mockusdt}.env`), SEO indexing **off**.
- **Done 2026-11-07** (all with security-reviewer / payment-auditor / i18n-mobile-reviewer passes, findings fixed):
  - v5.1: §1 staff reprice (#11), §2 storefront clarity (#12), §6 product auto-fill + inline cards (#13).
  - v5.2 (`docs/review-v5.2.md`): §1 staff access — admin login Turnstile, staff IP allowlist, no 429 for
    staff (#14); §10 Telegram notifications (#15); bilingual README + Cloudflare plan (#16); §7 USDT via
    BEpusdt, ADR-023 (#17); IP/user blocking (#18); reseller subdomains + custom domains app side (#19);
    §2 manual stock default 1 + §3 Ctrl+V paste (#20); §5 agent API tokens + Claude skill (#21);
    §6 SEO crawler HTML, sitemap, robots, 404, ADR-024 (#22).
- **Owner decisions / actions open:**
  1. Cloudflare changes for reseller subdomains + custom domains: `docs/cloudflare-reseller-domains.md`
     (proposal, not applied). Then: Settings → Resellers → custom domains (zone id, token with
     "SSL and Certificates: Edit").
  2. Prod domain: apex `kesiv.com` vs `shop.kesiv.com` as the main host (the other 301s).
  3. Real USDT: start BEpusdt on prod (profile `usdt`), set the Arbitrum/X Layer/TRC20 addresses in its
     console, copy its token into api.env (docs/production.md). Independent pentest before real money (M1 gate).
  4. Staff IPs for Settings → Security → staff IP allowlist. Telegram bot token rotation is the owner's call.
  5. Cloudflare 5-second challenge / IP-list sync for blocked IPs: Cloudflare settings + token permission.
- **Next (code):** v5.2 §8 optional entry check (Turnstile on entry), category pages for SEO, Telegram for
  manual-delivery completion, persisting USDT trade ids on the order (cancel-time re-query without Redis).
- **Security/payment review 2026-11-08 UTC (analysis only, fixes pending; [issue #26](https://github.com/arifulcrypto/24goods-web/issues/26)):** see
  [source findings and acceptance criteria](research/security-payment-audit-2026-11-08.md) and
  [upstream research](research/security-payment-references-2026-11-08.md). Priority: BEpusdt native
  callback HTTP 200 failure acknowledgements suppress retries; sensitive order-input copies in chat
  escape retention; busy SSE streams defer session revocation checks. Also track OTP atomic consumption,
  challenge configuration/action binding, order-input editing, resource admission budgets and durable
  payment attempts. This review does not satisfy the independent pentest gate; no business code changed.
- **Working rules:** AGENTS.md §7 (2 GB box: full suite in CI only, ≤ 2 agents, dev stack down when idle).

### Original follow-up requirements (see implementation status below; 2026-11-08 UTC)

Tracking: [#27 payments](https://github.com/arifulcrypto/24goods-web/issues/27),
[#28 operations/recovery](https://github.com/arifulcrypto/24goods-web/issues/28),
[#29 service reminders](https://github.com/arifulcrypto/24goods-web/issues/29).

- [v6 payment/operations proposal](review-v6-payments-ops.md): API-direct QR with explicit provider
  capabilities and truthful domain/privacy limits; immutable provider/attempt configuration; active
  ownership/permission/settlement cross-checks; optional server-enforced payment challenge with bounded
  self-hosted difficulty; callbacks/recovery stay interactive-challenge-free.
- Lightweight server-admin overview, structured operation/login/system/task/payment events, trusted
  proxy IP and approximate location/browser metadata, bounded alerts, key/software review reminders
  and optional static canary detection; no root panel or Docker/LXD socket in the API.
- [New source observations B1–B5](research/payment-qr-ops-2026-11-08.md): backup script has no archive
  encryption/whole-set manifest/job lock, excludes BEpusdt state, and does not establish cross-store
  consistency/PITR/restore proof; edge query redaction also needs review. Actual remote setup is unverified.
- [GCSA/Web3/runtime research](research/web3-ops-risks-2026-11-08.md): original @GCSAHK reports,
  signed-intent/valid-credential abuse, token/chain/finality checks, and separate PHP2Shell/LXD/runc
  advisories. No current host/wallet compromise is claimed.
- Staff-managed monthly-service entitlements and email/TG expiry reminders, separate from unpaid-order
  expiry; no automatic recurring debit. Reminder scheduling is idempotent; external sends can repeat
  after an uncertain outcome, so use stable IDs and bounded retries.
- Documentation review caught and corrected the unsafe blanket restore restart and an exactly-once
  notification overclaim. Implementation and independent security review remain pending.

## Current phase: **MVP-0: local demo with mock data** (finishing) → next: **M1 production MVP**

### Active owner-authorized implementation: issues #25–#30

- Feature branch `feat/issues-25-30-hardening`; PR must remain unmerged and no production deploy.
- #30: owner-supplied Xiaodou v1.0.0 reference package reviewed/imported under
  `docs/vendor/xiaodou`; English index and integrity manifest; live merchant credentials excluded.
  Supplied credentials select the MD5 EPay compatibility contract, not the separate native RSA API.
- Core fixes implemented with targeted regressions: BEpusdt retry HTTP statuses, buyer-input chat
  references/legacy redaction, bounded SSE revocation/subscriptions, atomic OTP consumption, challenge
  fail-closed/action/host checks, optional input editing and upload admission. Order-field encryption
  remains separately tracked by #8; current input configuration is explicit, not snapshotted.
- EPay implementation: immutable encrypted gateway versions, fresh administrator authentication,
  explicit merchant-ownership acknowledgement, durable reservations, original-provider notify/query
  routing, bounded DNS-pinned HTTPS, typed local QR and reconciliation. Alipay is the enabled-method
  scope supplied by the owner; native RSA and unapproved payment methods are not inferred available.
- USDT: durable encrypted original-provider binding and address/amount/trade history, PostgreSQL
  recovery after Redis loss, fail-closed unknown creation/cancellation, receiver mismatch holds.
- Buyer/staff UX: backend-owned input capabilities, no synthetic input fallback, browser Auto locale,
  bounded sender-only 180-second recall, shared pins/copy/quoted draft, private low-data thumbnails,
  default upload compression and local quality preferences.
- Operations/services: platform-admin overview and sanitized events; staff service activation,
  versioned periods and verified once-only renewal, durable opt-in staff SMTP/TG reminders. No debit.
- Recovery: encrypted complete-set maintenance backup, separate vault recovery recipient, writer
  fencing, lock, manifest, atomic completion and verified offsite receipt; simulated failure tests.
  See [operations-v6.md](operations-v6.md). Operator tools include age (new operational dependency).
- Entry/payment gates are integrated. First security/payment/mobile review found concrete blockers;
  follow-up patches and regressions are recorded in [review record](reviews/v6-implementation.md).
  Final-revision CI and re-review are pending.
  No deployment, live provider credential use, real payment or production backup/restore was performed.
  See [implementation matrix](implementation-v6.md) for concrete completion evidence and limits.

## Done

- v5 product import backend: `core/safefetch.py` (SSRF-safe, IP-pinned), `catalog/importer/` parsers + gmailbuy/maitg/ka8 adapters, `admin/importer.py` endpoints, offline fixture tests.

### 2026-11-05: M0 bootstrap
- Research by four parallel agents; evaluation in `docs/analysis.md`.
- Card vault, EPay verifier, CI security gates, AGENTS.md, project review agents.

### 2026-11-05: v2 review + MVP-0 build
- **Decisions** (`docs/review-v2.md`, ADR-009…014):
  - Static Vite SPAs instead of Nuxt SSR.
  - SSE instead of Centrifugo.
  - Built-in CS Inbox instead of Chatwoot (for now).
  - Voice calls dropped.
  - ZPay Alipay v1 first.
  - One domain.
  - Pull-based deployment.
- **Backend** (agent-built, orchestrator-verified):
  - FastAPI modular monolith with all MVP-0 endpoints from `docs/api-contract.md`.
  - Alembic schema: FORCE RLS, `app_api` / `app_delivery` roles with column grants, ledger Σ=0 trigger.
  - Separate delivery service: outbox, `SKIP LOCKED` card claim, internal reveal with circuit breaker.
  - Full ZPay notify pipeline.
  - Gated mock ZPay, seed data, keygen.
- **Frontend:**
  - `web` (Vant 4, mobile-first, EN/zh-CN): home, search, product, chat with product/quote/order cards, image and voice clips, orders, reveal, me.
  - `admin` (Element Plus): dashboard, products, inventory import, orders (price change, manual delivery), CS Inbox, ledger, audit.
  - 77 KB gzip initial JS.
- **Reviews run, findings fixed:**
  - *i18n-mobile-reviewer.* 4 Medium fixed: safe asset/pay URLs, contrast, focus ring.
  - *payment-auditor.*
    - FAIL #12 (timeout budget) fixed.
    - FAIL #13 (replay guard could lose a payment on crash) fixed.
    - Re-query trade_no/out_trade_no matching added.
    - Very late payments are now recorded.
    - Mock gated to dev/test.
    - https required for the query base in prod.
  - *security-reviewer.*
    - H1 (secret sprawl) fixed: per-service env files and Redis ACL users.
    - M2 (proxy IP) fixed: fixed edge IP set as the trusted proxy.
    - M6 (edge part) fixed: Caddy headers are defaults only.
    - App-side fixes M1/M3/M4/M5/M6/L1/L5/L8/L9: see the commit log.
- **Dependencies:**
  - Pillow pinned to 13.0.0 on Python ≥3.11 (GHSA-78g4-xqwv-q3g8 and 3 CVEs).
  - Uploads restrict decoders to the sniffed format.
- **Deploy:**
  - Compose stack sized for a 2 GB host.
  - Non-superuser schema owner; internal backend network with no egress.
  - `bootstrap.sh` generates all secrets.
  - `demo-update.sh` with a systemd timer.
- **CI:** backend tests against real PG and Redis; frontend checks; Semgrep; gitleaks (scoped allowlist); images + Trivy; **full-stack E2E smoke test**; GHCR publish only after E2E passes.
- **E2E smoke (v3):** also covers site/articles/recommendations, instant account via PoW, multi-field delivery, order lookup, after-sales (replace + refund), staff card find/void/replace, `file` messages and `/me` self-service; the frontends were reconciled against the real API (see `docs/api-contract.md`).

### 2026-11-06: v3 storefront
- **Built** (`docs/review-v3.md`, ADR-015…019; API in `docs/api-contract.md`):
  - list storefront with generated covers and the Kesiv brand config;
  - announcement modal, recently viewed, recommendations;
  - instant accounts with self-hosted PoW;
  - order/card lookup by access code;
  - multi-field delivery with redeem URL and usage notes;
  - articles CMS and fixed pages;
  - chat `.txt` file messages;
  - after-sales (replace / refund record / reject / resolve) with auto-confirm;
  - staff card management (fingerprint find, void, replace, batches, expiry);
  - `/me` self-service including account deletion.
- **Reviews and fixes:** a payment audit and a security review of the above were done; all findings are fixed with tests:
  - replacement card ids are validated, and the staff actor is recorded on replacement;
  - a duplicate notify after a refund is acknowledged quietly;
  - delivered-order windows are backfilled in migration 0002;
  - refund is admin-only inside the service, and opening a ticket bumps the order version (auto-confirm race);
  - instant accounts have no email, and the per-network cap is 200/day with a PoW ramp;
  - lookup lockout is per `order_no:ip` plus a global per-order cap, and a correct code is never blocked;
  - markdown images are same-origin only;
  - card find is audited and limited to 30/h;
  - the 3rd replacement needs an admin;
  - account deletion redacts chat messages, deletes uploads and clears ticket text;
  - `APP_LOOKUP_KEY` is required in prod;
  - `test_message_rate_limit` is now deterministic (clock pinned).

### 2026-11-06: local dev environment
- `scripts/dev.sh` runs the whole stack in one command: Python 3.12 venv via uv, dev PG + Redis containers, migrate + seed, then API, delivery and mockpay with reload, and both Vite apps.
- Everything binds to 127.0.0.1. You open it through an SSH tunnel, see `docs/dev-setup.md`.
- `scripts/dev.sh test` is a shortcut for the backend checks plus the frontend lint, typecheck and tests.
- `e2e/smoke.py --dev` runs the full E2E against the dev stack; it passes.

### 2026-11-06: production deployment support
- Same images and base compose as the demo; `deploy/docker-compose.prod.yml` + `deploy/Caddyfile.prod` add the production edge: Cloudflare (proxied) -> `:8088` HTTPS with an Origin CA cert, Authenticated Origin Pulls required, real visitor IP from `CF-Connecting-IP` (Cloudflare ranges only), `/admin` and `/api/v1/admin/*` limited to `ADMIN_ALLOW_IPS`, no mockpay. ADR-020.
- `deploy/bootstrap.sh` has `MODE=prod` (real ZPay from an env file, `APP_ENV=prod`, secure cookies, admin-only credentials file).
- Seed production mode (`APP_SEED_DEMO=false`): tenant, one admin (`APP_SEED_ADMIN_EMAIL`), pages, tutorials, site settings and a disabled announcement; no demo users, products, cards or orders. Idempotent, never overwrites admin edits.
- `scripts/prod.sh`: sync, bootstrap, deploy, status, logs, backup (+ nightly cron, 14 generations) and read-only smoke checks. See `docs/production.md`.

### 2026-11-06: commerce v4 backend (branch `feat/v4-be`)
- Spec: [review-v4.md](review-v4.md); ADR-021; API in `api-contract.md` ("Commerce v4 additions").
- **Done (server side):**
  - Buyer-paid channel surcharge (ceil, default 30 permille for Alipay), order `subtotal/fee/fee_rate`, ledger `surcharge_revenue` and refund reversal.
  - Cart quote, combined checkout, checkout pay/cancel, checkout notify path sharing one settlement helper, checkout expiry, payables cap.
  - Buyer/staff cancel with gateway re-query first; staff close needs a reason.
  - Product "order information" popup config, `orders.buyer_input` (never audited), PATCH input, buyer chat message after payment.
  - Support welcome/quick questions in site settings, `GET /site/payment-channels`.
  - Staff `product_card` messages built by the server.
  - Markdown product descriptions (`description_html`), idempotent seed backfill, `POST /admin/content/images`.
  - Global per-IP rate limit middleware (default OFF, admin switch, fail open).
  - Tests: `backend/tests/test_commerce_v4.py` (27 tests). Legacy tests run with a 0 permille surcharge configured in the `env` fixture.
- **Follow-ups:** file a GitHub issue to encrypt `orders.buyer_input` at rest; payment-auditor and security-reviewer passes on `payments/settle.py`, `orders/cancel.py`, `orders/checkout.py`; reconcile ledger vs gateway for surcharge.

### 2026-11-06: v5 commerce backend (branch `feat/v5-be-com`)
- Promo codes (percent/fixed, limits under row lock, reserved/used/released lifecycle, largest-remainder allocation), reseller storefronts (host/header resolution in `app/core/storefront.py`, markup pricing, sign-up order, payouts, admin), ledger lines for discount/markup/sign-up/payout. Tests: `backend/tests/test_commerce_v5.py`; E2E `v5_commerce`.
- **Follow-ups:** payment-auditor pass; reseller custom domains slot into `storefront.candidate_slug`; refund after payout makes `reseller_payable` negative (clawback, no automation).

## Next

### v3 follow-up
1. [ ] Owner click-through of the v3 storefront on the demo; collect UX feedback.
2. [ ] Refund API integration (ZPay) so a refund moves money instead of only recording it; reconciliation flags refunded orders without a gateway refund.
3. [ ] Email verification before an address can be trusted (see Known issues).

### MVP-0 wrap-up
1. [ ] Owner click-through on the demo host, then collect UX feedback.

### M1: production MVP (6–8 weeks; exit gate = independent security review + restore drill)
1. [ ] Infra: measure SoftBank↔IIJ and CN→edge latency; WireGuard mesh; nftables; SSH over WireGuard only.
2. [ ] Real ZPay merchant account (¥88). Configure `APP_ZPAY_*` via SOPS. Reconciliation job. External alerting on `manual_review`, `payment.*` critical audits and breaker trips.
3. [ ] Staff TOTP + step-up for card import; admin served on an admin-only path (VPN or Cloudflare Access); email verification; canned replies.
4. [ ] OG share endpoint `/s/{id}` + `sitemap.xml`.
5. [ ] Private R2 bucket for uploads (proxied via the edge); separate media origin; server-side voice transcoding.
6. [ ] pgBackRest → US + R2; restic; automated monthly restore drill; key-compromise runbook.
7. [ ] Security review follow-ups:
   - L2: SSE re-auth on heartbeat.
   - L4: per-domain ask buckets + negative-cache invalidation.
   - L7: SCRAM verifiers in the migration.
   - Frontend Low items: touch targets, aria-labels, image dimensions, plural forms, `console.warn` lint.
8. [ ] Stock reservation at order creation, so the last card can't be sold twice into `manual_review`.
9. [ ] Independent pentest. **No real money before it passes.**

### Scheduled
- [ ] **2026-11-12:** raise `minimumReleaseAge` in `frontend/pnpm-workspace.yaml` to 10080 (7 days) and drop its `nosemgrep`.
- [ ] When an upstream `caddy:2.11-alpine` ships go-jose ≥ v4.1.6: remove the xcaddy stage in `deploy/web.Dockerfile`.

## Open questions for the owner
- [ ] **Legal name for `LICENSE`** (#40). It currently says `Kesiv`, taken from the repo's own
      branding, and `printCopyrightNotice({ owner: 'Kesiv' })` passes the same. Needs the exact
      legal name of the owning person or company.
- [ ] **Governing law and selling entity** (#40), blocking the ToS draft. The host is in Japan
      (IIJ), buyers are international, the owner's jurisdiction is unstated. This determines the
      Mainland China restriction, statutory withdrawal rights for digital goods, and refund terms.
      Anthropic's ToS text will **not** be copied; original text in a comparable structure instead.
      Needs a qualified lawyer before it governs real transactions.
- [ ] **Approval to deploy `main` to production** (c4), prerequisite for the owner's intended live
      USDT transfer — prod is 15 commits behind and missing `0010_payment_attempts`. Wants a
      pre-deploy backup and recorded rollback image digests.
- [ ] Legal review of payments for mainland users (aggregator + later USDT).
- [ ] Which hostname of your domain to use for production. For HTTPS on the demo, point `demo.<domain>` at the demo host's IP.
- [ ] Chatwoot re-evaluation at M2: only if there are ≥3 agents, on a ≥4 GB server.

## Known issues
- Local dev Python is 3.10: Pillow there is capped at 12.3 (advisories fixed only in 13.0.0, which needs ≥3.11). CI and the images use 3.12 + Pillow 13.
- `filelock` (dev-tool transitive) advisories are advisory-only in CI.
- **Email ownership is unverified** until email verification lands (M1): anyone can set an unverified address on their account via `/me/credentials` or register. Conflicts use a generic message so addresses are not enumerable. Do not use email for account recovery or notifications of secrets yet.
- **Anonymization retains audit-log IPs** (and the anonymised order/ledger rows): account deletion redacts chat messages, deletes uploads and clears ticket text, but security audit rows keep IP addresses. The seeded privacy page says so.
- **No refund API:** refunds are recorded (order `refunded` + reversal ledger entries + audit) but the money is returned manually in the Alipay console.
- **runc 1.1 inside c4 (EOL):** the c4 LXD container runs Docker with runc 1.1 because host LXD 4.0 has an AppArmor limitation with newer runc. Upgrade host LXD to 5.21 and return to the default runc (`docs/production.md`).
- **No independent pentest/security review yet** (M1 exit gate, invariant 11). Production is live with real ZPay only under the owner's acceptance of that risk; keep order volume low until the review is done.

### v4 integration notes (2026-11-06)
- Migration 0003 backfill runs with `NO FORCE ROW LEVEL SECURITY` lifted on `orders` for the UPDATE only. With FORCE RLS even the table owner saw no rows, so the backfill silently updated nothing, and the later `SET NOT NULL` failed on existing data. The 0002 backfill had the same blind spot; it was harmless because production launched on v3.
- `scripts/dev.sh up` re-asserts the dev role passwords (`app_owner`, `app_api`, `app_delivery`) before migrating. Backend tests share the dev PG cluster and reset these cluster-wide roles to their test passwords.
- `e2e/smoke.py` gained a v4 section:
  - channel fee
  - cart quote
  - checkout paid in one payment
  - `pay_via_checkout`
  - buyer cancel and staff close
  - staff product card
  - global limiter, checking the exact 429 body

### v5 backend core (2026-11-06)
- Done: product archive/hard delete/restore/bulk; DB-backed categories with admin CRUD; settings sections + encrypted secrets (`APP_SETTINGS_KEY`, added to dev.sh and bootstrap.sh; prod.sh does not generate api.env); feature toggles; Turnstile; SMTP mailer, email codes, password reset, configurable email templates, order emails; Google sign-in; audit filters/CSV/retention; manual delivery ETA + overdue filter; tenant `defaults` applied to pending-order cap and timeout.
- Migration 0005 (`0005_v5_core_grants`): API may update its outbox rows; bounded `purge_audit_log()` function. Rebase note: the commerce agent may add its own 0005; renumber on merge.
- Next: frontend settings UI/consumers; production DNS/Turnstile hostnames; independent review (M1 gate) still open.
