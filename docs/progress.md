# Progress

Update this file in every PR. Phases are defined in [review-v2.md §8](review-v2.md#8-revised-plan-and-timeline-solo-developer--agents-honest-estimate).

## Current state — hand-off (read this first in a new session)

_Last updated 2026-11-08 UTC (v5.1 + v5.2 implementation; security/operations research added)._

- **Prod** https://shop.kesiv.com (c4, IIJ Japan) runs **v4**; deployed by the owner from another session
  (`scripts/prod.sh`). This box = dev + demo. Prod needs `APP_SETTINGS_KEY` and the new env vars
  (BEpusdt, see docs/production.md) before the v5 deploy.
- **Demo** https://demo.kesiv.com runs `:main` (timer `goods24-demo-update`, manual
  `/opt/24goods-demo/deploy/demo-update.sh`). Logins: `/opt/24goods-demo/deploy/secrets/demo-credentials.txt`.
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

### Follow-up requirements — documented, not implemented (2026-11-08 UTC)

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
- Remaining implementation/review in progress: durable USDT recovery, entry/payment challenge UI,
  chat/locale/media preferences, operations/backups and service reminders; full CI/review gates pending.

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
