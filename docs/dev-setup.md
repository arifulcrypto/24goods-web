# Local development

One script runs the whole stack with hot reload and mock data: `scripts/dev.sh`.

| What | Where | Reload |
|---|---|---|
| Buyer app (Vite) | http://localhost:5173/ | instant (HMR) |
| Admin console (Vite) | http://localhost:5174/admin/ | instant (HMR) |
| API (uvicorn) | 127.0.0.1:18000, proxied by Vite at `/api` | on save |
| Delivery service | 127.0.0.1:18001 | on save (watchfiles) |
| Mock ZPay | 127.0.0.1:18002, proxied by Vite at `/mockpay` | on save (watchfiles) |
| PostgreSQL 17 / Redis 7 | Docker `kesiv-dev-pg` :55440 / `kesiv-dev-redis` :56390 | persistent |

Everything binds to **127.0.0.1 only**. Vite dev servers must never be exposed to the internet: they serve source files, and some Vite versions have had file-read vulnerabilities.

The security shape is the same as production:
- separate DB roles (`app_owner`, `app_api`, `app_delivery`) and FORCE RLS;
- vault public key in the API, private key only in the delivery process.

Only rate limits and PoW difficulty are relaxed, because in dev every request reaches the API from 127.0.0.1.

## First run

Requirements: Docker, Node 26 LTS (same as CI and the web image; ≥ 22.12 also works) with the pnpm version pinned in `frontend/package.json` `packageManager` (`npm install -g pnpm@<version>`; Node ≥ 25 no longer bundles Corepack), `openssl`, and [`uv`](https://docs.astral.sh/uv/) (`pip install uv`). `uv` provides Python 3.12, the same version as CI and production.

```bash
scripts/dev.sh setup     # venv backend/.venv (Python 3.12), backend[dev], pnpm install, keys + env in .dev/
scripts/dev.sh up        # containers → migrate → seed → api, delivery, mockpay, web, admin
scripts/dev.sh creds     # seeded logins (buyer / agent / admin)
```

All generated secrets, logs and uploads live in `.dev/`, which is git-ignored and `chmod 700`.

## Opening it from your computer (SSH tunnel)

If the code runs on the server (107.173.10.79), forward the two Vite ports. Run this on **your** computer:

```bash
ssh -N -L 5173:127.0.0.1:5173 -L 5174:127.0.0.1:5174 root@107.173.10.79
```

Then open http://localhost:5173/ and http://localhost:5174/admin/ in your browser.

- `localhost` counts as a secure context, so WebCrypto, the PoW worker, notifications and the voice recorder all work.
- **VS Code / Cursor Remote-SSH** forwards the ports automatically. Open the "Ports" tab after `scripts/dev.sh up`.
- **Windows:** the same `ssh` command works in PowerShell (OpenSSH is built in). With PuTTY, use Connection → SSH → Tunnels: `5173 → 127.0.0.1:5173` and `5174 → 127.0.0.1:5174`.
- **Phone testing:** use the deployed demo (http://107.173.10.79:18888/), which updates from `main`. The dev servers are deliberately not reachable from outside.

## Daily use

```bash
scripts/dev.sh status            # processes, ports, memory
scripts/dev.sh logs api          # tail a log (api | delivery | mockpay | web | admin)
scripts/dev.sh restart delivery  # restart one process
scripts/dev.sh up api web        # start only what you need (saves RAM on the 2 GB host)
scripts/dev.sh down              # stop processes (keeps DB);  down --all also stops containers
scripts/dev.sh test [pytest args]  # ruff + pytest (own `test` DB in the dev container) + frontend lint/typecheck/test
scripts/dev.sh reset             # wipe dev DB + .dev/ and start over
```

To run the full end-to-end test against the dev stack:

```bash
backend/.venv/bin/python e2e/smoke.py --dev --base http://127.0.0.1:5173 --seed-env .dev/env/seed.env
```

**Schema changes:** add an Alembic migration under `backend/migrations/versions/`, then run `scripts/dev.sh up` again. It always runs `alembic upgrade head` and the idempotent seed first.

**Payments in dev:** checkout redirects to `/mockpay/submit.php`. Press "Pay" and the mock gateway sends a signed notify to the API, exactly as ZPay would. Automatic-delivery codes then appear on the order and in the chat.

## Memory on the 2 GB host

The demo stack (about 270 MB) and the full dev stack (about 600 MB: two Vite servers plus three Python processes) together get close to the limit. If the box gets slow:
- use `scripts/dev.sh up api web` while working on the buyer app, or `up api admin` for the console;
- or stop the demo temporarily: `cd /opt/24goods-demo/deploy && docker compose --env-file .env stop`, then `start` to bring it back.

## Shipping a change

AGENTS.md §5 applies:
1. Branch `feat/<topic>`.
2. Commit, then run `scripts/dev.sh test`.
3. Open a PR; CI runs the full stack plus E2E.
4. Merge. The demo picks up `:main` images through `deploy/demo-update.sh`.
