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

## 2. Repository layout

```
backend/            FastAPI service (Python ≥3.10, CI on 3.12)
  app/core/         config, cardvault (envelope encryption)
  app/payments/     one adapter per payment channel
  tests/            pytest; every security invariant has a test
docs/               architecture, progress, analysis, research/
.github/workflows/  ci.yml (lint/test/Bandit/pip-audit/Semgrep/gitleaks/Trivy), codeql.yml
.claude/agents/     project sub-agents (security-reviewer, payment-auditor, i18n-mobile-reviewer)
```

`frontend/` (Nuxt 4 + @nuxtjs/i18n + Vant 4) is added in milestone M1 — see progress.md.

## 3. Validation commands (run before every commit)

```bash
cd backend
pip install -e '.[dev]'
ruff check . && ruff format --check .
pytest -q
bandit -c pyproject.toml -r app -ll
pip-audit --strict .            # runtime dependency closure
```

CI runs the same plus Semgrep, gitleaks, Docker build + Trivy, and (when `ENABLE_CODEQL=true`) CodeQL.

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
- **Implementation**: one agent per independent module, in a git worktree; the orchestrator merges and runs §3.
- Agents must not: push to `main`, change CI permissions, weaken a security invariant, or add a dependency without noting it in the PR.
