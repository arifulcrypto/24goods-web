> **Archived (2026-11-10, #72):** PR #31 merged on 2026-11-08. The "keep PR #31 unmerged" instruction below is obsolete; current state is in `docs/progress.md`.

# V6 handoff — stopped at the owner's requested checkpoint

Read this first, then `docs/progress.md`, `AGENTS.md`, `docs/implementation-v6.md` and `docs/reviews/v6-implementation.md`.

## Repository and permissions

- Workspace: `/root/02project/24goods-web`.
- Branch: `feat/issues-25-30-hardening`; implementation HEAD `f414c597391c2dc29a42bf1e2cc190744e259fdf` (a subsequent docs-only checkpoint commit contains this handoff).
- **Draft PR #31:** https://github.com/arifulcrypto/24goods-web/pull/31. Keep unmerged; do not push main, enable auto-merge, deploy, rotate/configure live keys or make real payments.
- Issue #30 was created; English progress comments link #25–#30 to the PR. These broad issues remain open.
- `gh` CLI authenticated as a collaborator can push/comment. The connected GitHub app's writes previously returned 403; CLI was the working fallback.
- A live merchant secret/PID appeared in the owner's conversation. Neither was used or written into source, docs, issues, PR or fixtures. Do not copy/repeat them. Use synthetic test credentials; recommend owner rotation before eventual live activation.

## Completed implementation (not production deployed)

1. Core security: BEpusdt HTTP retry acknowledgements; no automatic plaintext order-input chat copies; immediate legacy redaction/purge; bounded SSE/auth rechecks/subscriptions; atomic constant-time OTP consumption; fail-closed Turnstile action/host/config checks; pre-buffer upload admission; backend-owned input edit capability.
2. Payments: reviewed `/root/temp` Xiaodou text references imported under `docs/vendor/xiaodou` with English index/provenance/hashes (17 text files, no archive/live credentials); EPay MD5 is separate from native RSA. Versioned tenant gateway configs, encrypted write-only keys, fresh admin authentication/activation acknowledgement, durable reservations, original-provider routing, private local QR, bounded streaming/numeric parsing and 64-character trade identity digests. Existing channel IDs retained. Only newly configurable Alipay EPay is within the supplied approved-method scope.
3. USDT: durable attempts/original encrypted credentials/receiver evidence, recovery after Redis loss, unknown-create fail-closed behavior, receiver-mismatch/missing-evidence holds; existing BEpusdt manages its supported wallet pools/networks.
4. Buyer/staff UI: Auto browser locale, input feedback, copy/quoted drafts/shared pins, sender-only 180-second recall, private thumbnails/compression/data preferences. Optional payment and storefront checks use bounded self-hosted PoW plus optional Turnstile; recovery/status/callbacks are exempt. Gated data is private/no-store/Vary Cookie.
5. Operations/services: platform-admin read-only overview, bounded sanitized events/login/task heartbeats, credential-age reminders; staff activation/timezones/calendar/fixed periods/verified once-only renewals; durable opt-in staff mail/TG reminders with stable IDs and bounded uncertain retries. No recurring charge.
6. Recovery code: age-encrypted fenced complete backup sets, separate vault recipients, lock/manifest/atomic publication/offsite receipts; safe optional metadata collector and isolated restore runbook. No production cron/collector/backup/restore was run. `age` is a new operational tool dependency, not an app dependency.

## Immediate next work — latest re-review FAILED

The first review found eight security/payment and ten frontend issues; the follow-up patch fixed them. A second read-only review of `f414c59` still reports **two Medium blockers**:

1. `backend/app/modules/orders/cancel.py:98–113`: after successfully checking unpaid managed USDT history, cancellation falls through to legacy EPay. With no EPay credentials/cutover this contacts the default gateway and blocks normal USDT-only cancellation. Guard legacy draining by actual available credentials and applicable legacy history. Add a USDT-only/no-EPay regression; preserve legitimate old EPay URLs after channel switching.
2. `backend/app/modules/payments/reconcile.py:70–83,258–282,296–300`: one original can settle/record a duplicate durably, then a different unavailable/unknown original raises. The runner loses the committed outcome and skips broadcasts/duplicate-payment alerts. Return/propagate committed outcomes separately from unresolved evidence. Emit those committed events/alerts while cancellation still fails closed. Add paid+unavailable, duplicate+unknown and retry/idempotency tests for both EPay and USDT.

Minor remainder: `frontend/apps/admin/src/pages/OperationsPage.vue:48` still displays raw event-family enums; use the existing translated labels.

Do not mark review approved or close issues on the basis of previous test counts. Re-run read-only security/payment/mobile review after fixes. Review files are under `.claude/agents/`; respect AGENTS §7 instead of their older instruction to run the entire backend suite locally.

## Validation and debugging history

- Initial CI found a real OTP burst race (all correct concurrent verifications could fail); fixed with one bounded fixed-size MAC/count/consume Lua script.
- CI on `0335f87` found five channel-switch failures from an over-broad legacy routing guard plus an account-deletion view-shape regression. Both fixed: EPay-only durable history restricts legacy EPay routing; ordinary redacted messages keep their established payload shape.
- Follow-up targeted review run: 27 passed, one concurrent switch returned 503 instead of the expected conflict. Corrected changed-target outcomes to 409; next targeted USDT/EPay/concurrent-switch run: **36 passed**.
- Earlier integrated EPay/challenge/operations/chat/OTP run: **38 passed**. Dedicated review regressions cover long IDs, numeric bounds, capped streams, query failures, multi-attempt recovery, missing receiver and sampled login events. Counts overlap; do not add them into a unique total.
- Latest local frontend lint and both app typechecks passed; shared **95**, buyer **6**, admin **53** tests passed. Synthetic backup/collector tests **5 passed**; Bash syntax and Caddy adaptation passed. Bandit reported no Medium/High issues.
- At checkpoint, CI for implementation `f414c59`: https://github.com/arifulcrypto/24goods-web/actions/runs/42484703719 — frontend/build/size, gitleaks and Semgrep passed; backend was pending; image/E2E checks had not completed. The docs checkpoint push may create a newer run. Inspect the newest head/checks and logs; do not infer a green full suite.
- A test in `test_payment_review_v6.py` initially assumed a second paid trade always changes an already-paid order to manual_review. Existing settlement intentionally preserves paid/delivered state and emits a critical duplicate audit/refund alert; the regression now checks detection/identity and alert outcome without pretending an already delivered item can be undone.

## Local environment and resource rules

- No local tests/servers remain running at handoff. Dedicated throwaway `24goods-v6-pg` / `24goods-v6-redis` are stopped/removed during checkpoint cleanup. Do not touch unrelated `store-postgres-test`, demo or production containers.
- `.dev/venv` has backend dependencies; use `PYTHONPATH="$PWD/backend"` so current code overrides the installed snapshot. Node 22/corepack pnpm and frontend dependencies are installed.
- For targeted tests, recreate disposable PG/Redis if needed. Prior endpoints were `postgresql+asyncpg://postgres:test@127.0.0.1:55432/postgres` and `redis://127.0.0.1:56379/15`, with `TEST_REQUIRE_SERVICES=1`. The test DB is `/postgres`, not `/test` in that local setup; fixtures create/drop `g24_test` and roles.
- Check `free -m`; heavy finite commands only under `flock .dev/heavy.lock`. Max two active agents including orchestrator; no local full backend suite. Run only changed/related regressions locally; full suite/security/build/E2E runs in CI. No long-running server under flock.
- Old implementation worktrees were removed, no user work was reset. Check `git status` before continuing. `.dev/*` logs/bodies are ignored; committed evidence is in docs.

## Not started / not verified / outside this checkpoint

- Production/live merchant authorization, independent settlement checks, actual key rotation and a real test charge; native RSA/Lantu/official Alipay or unapproved WeChat/QQ/bank adapters.
- Physical mobile Safari/Chrome/embedded-wallet/assistive-technology verification, clipboard/long-press/modal behavior and same-phone QR save/open-wallet UX. Source review and CI do not prove these.
- Production installation/configuration of age recipients/cron/collector, transition of historical plaintext backups, independently held recovery keys, restore drill, RPO/RTO/PITR, retention/capacity policy and off-host heartbeat/pressure alerts. Overview reports missing host/recovery evidence as unknown.
- A combined overdue digest (currently per entitlement/day), richer host inventory/geolocation, optional static canary detection; no vulnerable honeypot was built or deployed.
- Order buyer-input encryption at rest remains separate issue #8. No real money before independent human review/pentest. Scans/tests/reviewer checks are not an audit certificate or DDoS/anonymity guarantee.
