# Progress

Update this file in every PR. Milestones are defined in [analysis.md §8](analysis.md#8-development-priority-solution-a).

## Current milestone: **M1 — MVP**

## Done

- **2026-11-05 — M0 bootstrap**
  - Research with four parallel agents: platforms, chat/voice, payments, infra. Results in `docs/research/*.md`; recommendation in `docs/analysis.md`.
  - Architecture v0.1 and ADR-001…008 (`docs/architecture.md`).
  - `AGENTS.md` (rules, security invariants, validation commands, agent workflow) and `.claude/agents/` (security-reviewer, payment-auditor, i18n-mobile-reviewer).
  - Backend seed:
    - FastAPI app with security headers and `/healthz`.
    - **Asymmetric card vault** (X25519 + AES-256-GCM, AAD binding, rotation, fingerprints).
    - **EPay notify verifier** (pinned sign_type, constant time, merchant/order/status/amount checks).
    - 26 tests.
  - CI:
    - `ci.yml` runs Ruff, pytest, Bandit, pip-audit, Semgrep, gitleaks, Docker build, Trivy, and pushes to GHCR. All actions are SHA-pinned.
    - CodeQL is behind the `ENABLE_CODEQL` variable.
    - Dependabot.

## Next (M1, in order)

1. [ ] **Infra:** measure SoftBank↔IIJ RTT and CN→SoftBank / CN→CF latency (Tencent GZ probe or itdog.cn). Set up the WireGuard mesh, nftables default deny, and SSH over WireGuard only.
2. [ ] **DB foundation:** SQLAlchemy 2 async, Alembic, Postgres roles (`app_api`, `app_delivery`, `app_migrate`) with column grants, an RLS helper, and a docker-compose for local dev. Add a CI service container for PG and Redis.
3. [ ] **Auth:** register, login, email verification, Argon2id, refresh-token rotation, TOTP for staff, rate limits, ALTCHA + Turnstile verification.
4. [ ] **Catalog:** products (i18n jsonb, images to R2), listing page, product page, PG search.
5. [ ] **Inventory:** card upload (seal + fingerprint dedup), masked admin view.
6. [ ] **Orders + payments:** order state machine, BEpusdt adapter (with test vectors), EPay adapter wired into the notify endpoint, outbox, reconciliation job.
7. [ ] **Delivery worker:** isolated unit, claiming stock with `FOR UPDATE SKIP LOCKED`, reveal endpoint, circuit breaker. Manual delivery flow.
8. [ ] **Frontend:** Nuxt 4 + i18n + Vant scaffold, mobile layouts (home, search, product, checkout, orders, me), EN/ZH.
9. [ ] **Deploy:** Caddy + CrowdSec on SoftBank, cloudflared on IIJ, pgBackRest + restic to US/R2, `deploy.yml`, first restore drill.

## Open questions for the owner

- [ ] Legal review of USDT and aggregator payments for mainland users.
- [ ] Which EPay-compatible provider to use first (ZPay or another), and the settlement limits.
- [ ] Should the repo become public? That makes CodeQL and secret scanning free, but publishes the security design.
- [ ] Brand name, and which of the two domains is international vs CN entry.

## Known issues

- `filelock` 4.1.0, a transitive dependency of the dev tooling, has advisories fixed in 4.2.1. That version isn't on the local mirror yet. It isn't a runtime dependency, so the CI dev audit is advisory only.
