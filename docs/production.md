# Production runbook (shop.kesiv.com)

## Topology

```
visitor --TLS--> Cloudflare (orange cloud, SSL Full strict)
   --Origin Rule: port 8088, HTTPS + Authenticated Origin Pulls-->
141.98.198.150:8088 --LXD nat--> c4 (10.0.43.40:8088) --> Caddy (web) --> api / SPAs
```

- c4: Ubuntu 22.04, Docker 29 + compose v2, deploy dir `/opt/kesiv/deploy` (only `deploy/` files, no source).
- Images: `ghcr.io/arifulcrypto/goods24-api:main` and `goods24-web:main` (private, pulled with a transient login).
- Same compose as dev/demo plus `docker-compose.prod.yml` (port 8088, certs, `Caddyfile.prod`, memory limits for 4 GB: api 384m, postgres 512m). mockpay is behind `--profile demo` and never starts.
- Only `web` publishes a port. Source IPs at c4 are Cloudflare IPs; the real visitor comes from `CF-Connecting-IP` (trusted only from Cloudflare ranges, list dated in `Caddyfile.prod`).
- Admin paths (`/admin`, `/admin/*`, `/api/v1/admin/*`) return 403 unless the visitor IP is in `ADMIN_ALLOW_IPS` (default `141.98.198.150 38.175.222.75`, space-separated IPs/CIDRs, in `deploy/.env`).
- SSE: the API pings every 15 s, below Cloudflare's 100 s idle limit.
- Secrets on this box (`/root/.secrets`): `c4_ssh_pw`, `ghcr_token`, `zpay.env` (`APP_ZPAY_PID`, `APP_ZPAY_KEY`, `APP_ZPAY_GATEWAY=https://zpayz.cn`), `prod-certs/{origin.pem,origin.key,cf-origin-pull-ca.pem}`. After bootstrap: `prod-admin.txt`.

## First deploy

```bash
scripts/prod.sh sync        # deploy/ files + TLS certs -> c4:/opt/kesiv/deploy
scripts/prod.sh bootstrap   # once: MODE=prod bootstrap.sh on c4, admin login -> /root/.secrets/prod-admin.txt
scripts/prod.sh deploy      # pull images, up -d --wait (migrate -> seed -> api, delivery, web)
scripts/prod.sh smoke       # read-only checks against the public site and the origin
scripts/prod.sh backup && scripts/prod.sh install-backup-cron
```

`bootstrap` refuses to run if `/opt/kesiv/deploy/.env` exists: regenerating would orphan the vault keys and the database.
Then sign in at `https://shop.kesiv.com/admin/` from an allowlisted IP, change the admin password, review the pages and enable the announcement if wanted.

## Update

```bash
scripts/prod.sh deploy      # idempotent; sync first if deploy/ files (compose, Caddyfile.prod) changed
scripts/prod.sh smoke
```

`deploy` logs in to GHCR for the pull only (token on stdin; `docker logout` afterwards) and appends the running image ids to `/opt/kesiv/image-history.log`. Migrations run as the `migrate` one-shot before the API restarts. Seed re-runs are idempotent and do not overwrite admin edits.

Changing the admin allowlist: edit `ADMIN_ALLOW_IPS` in `/opt/kesiv/deploy/.env`, then `cd /opt/kesiv/deploy && docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d web`.

## Rollback

Migrations are forward-only; roll back only to an image whose schema is compatible (restore a backup otherwise).

1. Find the previous digest: `docker image ls --digests ghcr.io/arifulcrypto/goods24-api` on c4, or the GitHub Actions run of the previous good commit.
2. Pin it in `/opt/kesiv/deploy/.env` (login to GHCR first if the image is not local):
   `API_IMAGE=ghcr.io/arifulcrypto/goods24-api@sha256:<digest>` and `WEB_IMAGE=ghcr.io/arifulcrypto/goods24-web@sha256:<digest>`.
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d --wait` (do NOT use `scripts/prod.sh deploy` while pinned: it pulls `api web`, which is fine for digests but remember to unpin).
4. Unpin (set back to `:main`) after the fix is merged and deployed.

## Backups and restore

- `scripts/prod.sh backup` runs `/opt/kesiv/backup.sh` on c4 and copies the files to `/root/kesiv-backups/` (14 generations kept on both sides): `goods24-<ts>.dump` (`pg_dump -Fc`), `uploads-<ts>.tgz`, `config-<ts>.tgz` (`.env`, `env/`, `secrets/`: contains the vault private keys, treat as top secret; without them card stock cannot be revealed).
- `scripts/prod.sh install-backup-cron` installs `/etc/cron.d/kesiv-backup` (03:30 UTC) on c4. Backups on c4 are on the same disk: pull copies regularly with `scripts/prod.sh backup`.
- Restore the database (stack running, app stopped):
  ```bash
  cd /opt/kesiv/deploy; DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env"
  $DC stop api delivery
  $DC exec -T postgres dropdb -U postgres goods24 && $DC exec -T postgres createdb -U postgres -O app_owner goods24
  $DC exec -T postgres pg_restore -U postgres -d goods24 --no-owner --role=app_owner < /opt/kesiv/backups/goods24-<ts>.dump
  $DC up -d --wait
  ```
  Run a restore drill on a scratch host before relying on this (M1 exit gate); the role grants come from the migrations/init scripts, so verify with the smoke checks and a login.
- Uploads: `docker run --rm -i -v goods24_uploads:/data --entrypoint tar postgres:17-alpine -C /data -xzf - < uploads-<ts>.tgz`.

## Rotating the ZPay KEY

1. Change the key in the ZPay merchant console (outstanding payment notifies signed with the old key will then fail verification; do it when no payments are pending).
2. Update `/root/.secrets/zpay.env` and edit `APP_ZPAY_KEY` in `/opt/kesiv/deploy/env/api.env` on c4 (`env/api.env` is only readable by root).
3. `cd /opt/kesiv/deploy && docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d api`.
4. Make a small test purchase.

## Refreshing Cloudflare IP ranges

`curl https://www.cloudflare.com/ips-v4; curl https://www.cloudflare.com/ips-v6`, update `trusted_proxies static ...` and the date comment in `deploy/Caddyfile.prod`, validate (`caddy validate` in the web image), sync, `up -d web`.

## Known issue: runc 1.1 inside c4

The c4 container runs Docker with the default runtime set to runc 1.1 (EOL) because host LXD 4.0 has an AppArmor limitation that breaks newer runc. No compose change is needed. Fix: upgrade the host LXD to 5.21, then return Docker's default runtime to the bundled runc and recreate the containers. Until then, treat container escapes as a risk: containers already run with `cap_drop: ALL`, read-only roots and `no-new-privileges`.
