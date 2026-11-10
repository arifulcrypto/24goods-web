# AGENTS.md — Project rules for humans and AI agents

24goods is a **security-first, mobile-first, bilingual (EN primary / zh-CN)** marketplace for virtual goods and digital services: manual C2C delivery plus automatic card-key/API delivery, Xianyu-style chat-to-negotiate, multi-channel payments and reseller white-label stores.

Read before working: [`docs/architecture.md`](docs/architecture.md) (design + decisions), [`docs/progress.md`](docs/progress.md) (what's done / next), [`docs/analysis.md`](docs/analysis.md) (why this stack).

## 1. Non-negotiable security invariants

Any change that violates one of these must be rejected in review.

1. **Card secrets are never stored, logged or returned in plaintext.** Only `app/core/cardvault.py` may seal/open them. Only the delivery worker holds KEKs. Admin UIs show `mask()` output only.
2. **Payment notifies are hints, not truth.** Fulfilment requires: pinned-algorithm signature check (constant-time) → merchant id → exact `Decimal` amount → status → server-to-server re-query → row lock + state machine → `UNIQUE(channel, gateway_trade_no)` → outbox. `return_url` never fulfils.
3. **Prices come from the server.** The client never sends an amount that is trusted. A quote/price change is a server-side, audited, versioned record.
4. **Tenant isolation everywhere.** Every query on tenant data filters by `tenant_id`/`merchant_id` (enforced in the repository layer and by PostgreSQL Row-Level Security once enabled). Tests must include a cross-tenant access attempt.
5. **No secrets in git.** Use `.env` (git-ignored) locally and SOPS+age / systemd credentials in prod. gitleaks runs in CI.
6. **Uploads are hostile.** Validate by magic bytes, re-encode images, strip EXIF, size-limit, store under random keys, serve from a separate origin with `Content-Disposition` and no script execution.
7. **No mainland-China hosting of data, keys or payment logic** (see `docs/research/infra.md` §0).
8. **No runtime third-party CDN / Google assets** on the frontend (China reachability + supply chain).
9. **Pin every GitHub Action to a full commit SHA** with a `# vX.Y.Z` comment.
10. **Least privilege per process.** Each container gets only its own env file (`deploy/env/<service>.env`); only `delivery` holds vault private keys and it has no internet egress; DB roles `app_api`/`app_delivery` and Redis ACL users are scoped per role.
11. **Scans are not an audit.** No real money before an independent security review/pentest (M1 exit gate).

## 2. Repository layout

```
backend/            FastAPI modular monolith (Python ≥3.10 dev, 3.12 CI/prod)
  app/core/         config, db (RLS tenant pinning), security, middleware, cardvault
  app/modules/      auth, catalog, chat (quotes), orders, payments (notify, reconcile, gateways), inventory, uploads,
                    realtime (SSE), admin, aftersales, promo, reseller, operations, challenges, seo, content, lookup, me
  app/payments/     epay.py (protocol), zpay.py (adapter)
  app/delivery/     SEPARATE process: outbox consumer + internal reveal API (only holder of private keys)
  app/devtools/     mock_zpay (dev/test only), seed, keygen
  migrations/       Alembic: tables, FORCE RLS, roles, column grants, ledger trigger
  tests/            pytest (needs PG + Redis; see tests/conftest.py)
frontend/           pnpm workspace — static Vite SPAs, no Node at runtime
  apps/web          buyer app, mobile-first (Vue 3 + Vant 4)
  apps/admin        staff/CS console, PC (Vue 3 + Element Plus)
  packages/shared   API client, types, SSE, i18n, voice recorder
deploy/             docker-compose.yml, Caddyfile, bootstrap.sh, web.Dockerfile, postgres-init/
e2e/smoke.py        full-stack smoke test (CI job `e2e`)
docs/               analysis, review-v2 (current decisions), architecture, api-contract, progress, research/
.github/workflows/  ci.yml, codeql.yml (opt-in)
.claude/agents/     security-reviewer, payment-auditor, i18n-mobile-reviewer
```

## 3. Validation commands (run before every commit)

Shortcut on a dev machine: `scripts/dev.sh check` (inner loop: ruff on changed files, only the
tests that touch changed modules, typecheck/vitest for the touched app) or `scripts/dev.sh test`
(ruff + full pytest against the dev containers + frontend lint/typecheck/test). Dev stack: [`docs/dev-setup.md`](docs/dev-setup.md).

```bash
# backend — needs PG + Redis (throwaway containers are fine)
cd backend && pip install -e '.[dev]'
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@127.0.0.1:55432/test \
       TEST_REDIS_URL=redis://127.0.0.1:56379/15 TEST_REQUIRE_SERVICES=1
ruff check . && ruff format --check . && pytest -q
bandit -c pyproject.toml -r app -ll
pip-audit --strict .            # runtime dependency closure (run on Python 3.12)

# frontend
cd frontend && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck && pnpm test && pnpm build

# full stack (mock data)
PUBLIC_BASE_URL=http://127.0.0.1:8080 PUBLISH_HTTP=8080 ./deploy/bootstrap.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile demo up -d --wait
python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env
```

CI runs all of the above plus Semgrep, gitleaks, Trivy, and (when `ENABLE_CODEQL=true`) CodeQL; images are pushed to GHCR only after the E2E smoke passes.

## 4. Coding rules

- Python: type hints everywhere, `ruff` clean, no `eval/exec/pickle/yaml.load`, no `shell=True`, no f-string SQL (SQLAlchemy Core/ORM bound params only).
- Money: `Decimal` or integer minor units — never `float`. Currency always stored next to amount.
- Every state change on orders, quotes, payments, payouts writes an audit row (who, what, before, after, when, ip).
- All user-facing strings go through i18n keys (`en` is the source locale; `zh-CN` must be updated in the same PR).
- Mobile first: design at 375 px width first; touch targets ≥ 44 px.
- Keep the MVP small: if a feature isn't in the current milestone in `docs/progress.md`, don't build it.

## 5. Workflow

1. Branch from `main`: `feat/<topic>`, `fix/<topic>`, `sec/<topic>`.
2. Small commits, conventional messages (`feat:`, `fix:`, `sec:`, `docs:`, `ci:`, `refactor:`, `test:`). Each commit must pass §3 so any commit is a safe rollback point.
3. Open a PR; CI must be green. Security-sensitive paths (`cardvault`, `payments/`, auth, tenant filters) require the **security-reviewer** agent pass and a human review.
4. Update `docs/progress.md` in the same PR (move items to Done, add Next). Record any design decision in `docs/architecture.md` §Decisions (ADR-style: context → decision → consequences).
5. Rollback = `git revert <sha>` + redeploy the previous image digest from GHCR.

## 6. Using agents

- **Research** (new library/vendor): spawn parallel research agents, require citations and a verified-vs-inferred legend, store output in `docs/research/<topic>.md`.
- **Review**: run `.claude/agents/security-reviewer.md` on any PR touching §1 invariants; `payment-auditor` on `app/payments/**`; `i18n-mobile-reviewer` on frontend PRs.
- **Implementation**: one `implementer` agent (Sonnet, `.claude/agents/implementer.md`) per independent module, in a git worktree; the orchestrator merges and runs §3.
- **Low-resource rules (owner decision 2026-11-06; dev box = 2 vCPU / 2 GB):** see §7.
- Agents must not: push to `main`, change CI permissions, weaken a security invariant, or add a dependency without noting it in the PR.

## 7. Resource and token budget (dev box: 2 vCPU / 2 GB RAM / 2.5 GB swap)

The full backend suite peaks at ~1 GB RSS and takes 7–8 min when the box swaps. Rules:

1. **Full test suite runs in GitHub CI, not locally.** Push the branch / open the PR and let CI
   (backend, frontend, E2E) be the gate. Locally run only what you touched:
   `scripts/dev.sh check` (or `pytest -q tests/test_x.py` + `ruff` by hand). CI skips the
   backend/frontend/image jobs a PR does not touch (docs-only PRs finish in ~1 min).
2. **At most 2 agents at a time** (normally 1 implementer + the orchestrator). Reviews
   (security-reviewer, payment-auditor) are read-only and may run while no tests are running.
3. **Heavy finite commands only under the lock:** `flock .dev/heavy.lock <cmd>` for pytest,
   vue-tsc, vitest, vite build. **Never start long-running servers under `flock`**: they inherit
   the lock and block everyone (`scripts/dev.sh up` must run without it).
4. **Dev stack only while needed:** `scripts/dev.sh down` when idle; view work on the demo
   (https://demo.kesiv.com) instead. The demo stack is small (~70 MB) and stays up.
5. Before starting heavy work check `free -m`; if available < 300 MB, wait or stop the dev stack.
6. Keep worktrees only while an agent works in them (`git worktree remove` after merge); each
   carries its own `node_modules`.

Token/cache hygiene for sessions: start each new session from `docs/progress.md` → "Current state"
(the hand-off), not from history; give agents short specs that point to docs instead of pasting
context; avoid dumping long logs (use `tail`, `grep`); one topic per session, `/clear` between
unrelated topics.
