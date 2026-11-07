# 24goods (Kesiv)

**English** · [简体中文](README.zh-CN.md)

**A security-first, mobile-first, bilingual (English / 简体中文) marketplace for virtual goods and digital services.**
Buyers chat with the seller, get a custom quote, pay in the chat and receive their goods automatically
(card keys / codes) or manually. Resellers can open white-label stores with their own markup.

- **Live demo:** https://demo.kesiv.com (mock payments, resets occasionally)
- **Production:** https://shop.kesiv.com
- **Status:** MVP, moving towards the M1 production release — see [docs/progress.md](docs/progress.md).

## Features

| Area | What you get |
|---|---|
| Storefront | Mobile-first buyer app (Vue 3 + Vant), EN / zh-CN, search, categories, product pages, order lookup |
| Delivery | Automatic card-key delivery from an encrypted vault; manual delivery with text + attachments; after-sales window |
| Chat & quotes | Xianyu-style buyer ↔ staff chat with images and voice clips, quote cards, in-chat payment, staff CS inbox |
| Payments | EPay-protocol gateways (ZPay / Alipay); USDT via self-hosted BEpusdt (in progress); per-channel fees and on/off switches |
| Resellers | White-label storefronts (`?store=slug`, subdomains / custom domains planned), markup, earnings ledger |
| Staff console | PC admin (Vue 3 + Element Plus): products, product import from URL/page, inventory, orders, repricing, promos, ledger, audit log, settings |
| Security | Card secrets sealed with asymmetric envelope encryption, PostgreSQL Row-Level Security per tenant, verified payment notifies, Turnstile, rate limits, staff IP allowlist |

## Architecture

```
Browser ──► Cloudflare ──► Caddy (static SPAs + reverse proxy)
                              ├─► api        FastAPI modular monolith (REST + SSE)
                              ├─► delivery   separate process: outbox consumer, the ONLY holder of vault private keys, no internet egress
                              ├─► PostgreSQL (RLS, per-role grants, append-only ledger)
                              └─► Redis      (sessions, rate limits, pub/sub)
```

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, Redis 7.
- Frontend: pnpm workspace with two static Vite SPAs (`apps/web`, `apps/admin`) and `packages/shared`. No Node at runtime.
- Design decisions are recorded as ADRs in [docs/architecture.md](docs/architecture.md).

## Security model (summary)

1. Card secrets are never stored, logged or returned in plaintext; only the delivery worker can open them.
2. Payment notifies are hints: signature → merchant → exact amount → status → server-to-server re-query → row lock → unique trade number → outbox.
3. Prices always come from the server; every price change is audited.
4. Tenant isolation in the repository layer **and** PostgreSQL Row-Level Security.
5. No secrets in git; uploads are treated as hostile; no third-party CDN assets at runtime.

The full list is in [AGENTS.md §1](AGENTS.md). Scans are not an audit: an independent pentest is required before real money.

## Quick start (development)

Requirements: Linux, Docker, Python ≥ 3.10, Node 22 + pnpm (corepack). Works on a 2 GB box.

```bash
git clone https://github.com/arifulcrypto/24goods-web.git && cd 24goods-web
scripts/dev.sh setup     # containers (PostgreSQL, Redis), venv, pnpm install, secrets in .dev/
scripts/dev.sh up        # API + delivery + Vite dev servers with hot reload and mock data
scripts/dev.sh creds     # demo logins (buyer / agent / admin)
scripts/dev.sh down      # stop when idle
```

Details, ports and the SSH tunnel for opening it from your PC: [docs/dev-setup.md](docs/dev-setup.md).

**Whole stack with Docker (mock data, like the demo):**

```bash
PUBLIC_BASE_URL=http://127.0.0.1:8080 PUBLISH_HTTP=8080 \
API_IMAGE=ghcr.io/arifulcrypto/goods24-api:main WEB_IMAGE=ghcr.io/arifulcrypto/goods24-web:main \
  ./deploy/bootstrap.sh
docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile demo up -d --wait
cat deploy/secrets/demo-credentials.txt
python e2e/smoke.py --base http://127.0.0.1:8080 --seed-env deploy/env/seed.env
```

## Testing

```bash
scripts/dev.sh test                       # ruff + pytest (against the dev containers) + frontend lint/typecheck/test
cd backend && pytest -q tests/test_x.py   # on small machines run only what you touched
cd frontend && pnpm lint && pnpm typecheck && pnpm test
```

The full suite runs in GitHub Actions on every PR (see [AGENTS.md §3 and §7](AGENTS.md)).

## Deployment

| Environment | Host | How |
|---|---|---|
| Dev | this repo on a dev box | `scripts/dev.sh` |
| Demo | `demo.kesiv.com` | pull-based: `deploy/demo-update.sh` (systemd timer) pulls the `:main` images from GHCR after CI is green |
| Production | `shop.kesiv.com` (IIJ, Japan) | from the dev box: `scripts/prod.sh sync`, `scripts/prod.sh deploy`, `scripts/prod.sh smoke` — see [docs/production.md](docs/production.md) |

Images are built, scanned with Trivy and pushed to GHCR only after the full-stack E2E smoke passes.
Rollback = `git revert <sha>` and redeploy the previous image digest. Secrets live in git-ignored env
files (`deploy/env/<service>.env`, one per container, least privilege) — never in the repo.

## CI/CD

Backend (Ruff, pytest on PostgreSQL + Redis, Bandit, pip-audit) · frontend (lint, vue-tsc, vitest, build + size budget) ·
Semgrep · gitleaks · image build + Trivy · full-stack E2E smoke · push to GHCR · pull-based demo deploy.
Every GitHub Action is pinned to a commit SHA.

## Roadmap and known issues

Current work and open items live in [docs/progress.md](docs/progress.md) and the latest review spec
([docs/review-v5.2.md](docs/review-v5.2.md)). Highlights:

- USDT payments via BEpusdt (Arbitrum One, X Layer, TRC20) and payment method switches
- Telegram staff notifications
- IP / user blocking
- Reseller subdomains and custom domains (Cloudflare for SaaS)
- Agent API (scoped tokens) for batch listing, SEO (crawler HTML, sitemap, robots), paste-to-upload images
- Before real money: independent security review / pentest (M1 exit gate)

## Documentation

| Doc | What it covers |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules for humans and AI agents: invariants, commands, workflow, resource budget |
| [docs/architecture.md](docs/architecture.md) | Topology, card vault, payment pipeline, negotiation, security matrix, ADRs |
| [docs/api-contract.md](docs/api-contract.md) | REST + SSE contract |
| [docs/progress.md](docs/progress.md) | Current state, done, next |
| [docs/dev-setup.md](docs/dev-setup.md) · [docs/production.md](docs/production.md) | Dev stack · production operations |
| [docs/analysis.md](docs/analysis.md) · [docs/research/](docs/research/) | Why this stack, sourced research |

## Legal

Digital-goods and crypto payments are regulated differently by country. In mainland China, USDT
business is illegal; nothing (data, keys, payment logic) is hosted there. Get legal advice for your market.
