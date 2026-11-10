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

**Review note (2026-11-08; implementation pending):** the current script below is a starting
point, not a complete verified disaster-recovery system. It does not encrypt the config/key
archive, include `bepusdt_data`, or publish a verified whole-set manifest. Permissions and SSH
transport do not replace backup encryption/off-host recovery. Cron installation and restore
success must be verified on the actual host. See [observed gaps](research/payment-qr-ops-2026-11-08.md#newly-observed-backup-and-logging-gaps)
and the [proposed recovery requirements](review-v6-payments-ops.md#7-complete-encrypted-and-recoverable-backups).
Restore drills must disable outbound notifications/callbacks and prevent duplicate delivery
until provider payments and the restored outbox are reconciled.

- `scripts/prod.sh backup` runs `/opt/kesiv/backup.sh` on c4 and copies the files to `/root/kesiv-backups/` (14 generations kept on both sides): `goods24-<ts>.dump` (`pg_dump -Fc`), `uploads-<ts>.tgz`, `config-<ts>.tgz` (`.env`, `env/`, `secrets/`: contains the vault private keys, treat as top secret; without them card stock cannot be revealed).
- `scripts/prod.sh install-backup-cron` installs `/etc/cron.d/kesiv-backup` (03:30 UTC) on c4. Backups on c4 are on the same disk: pull copies regularly with `scripts/prod.sh backup`.
- Recover on an **isolated scratch host** with the selected complete backup set, compatible
  image/schema versions and separately recovered keys. Keep API/delivery stopped and block
  external payment callbacks, SMTP/TG and outbound provider calls while restoring. If an
  operator needs controlled read-only provider reconciliation later, grant only that path.
- Recreate database ownership/RLS/role grants using the reviewed init/migration procedure;
  restore the PG archive with `pg_restore`, matching uploads, BEpusdt state and configuration
  into explicitly selected recovery volumes. Validate checksums, media references and secret
  recovery; do not point a scratch restore at the production Compose project or volumes.
- Before enabling any fulfilment, reconcile restored payments/outbox/entitlements against
  the provider's post-backup history using an authorized controlled recovery task. Existing
  background loops do not establish this recovery capability; implementing and testing it
  is part of the open release gate. The restore task itself must not deliver or notify.
- Start only the explicitly approved services after those checks and record the release,
  recovery point, RLS/grant/synthetic-vault/private-media checks and measured recovery time.
  Then re-enable callbacks/delivery/notifications in a controlled order and observe duplicate
  or late-payment handling. A blanket full-stack restart is not a safe restore completion step.
  The measured scratch-host drill remains an M1 exit gate.

## Rotating the ZPay KEY

1. Change the key in the ZPay merchant console (outstanding payment notifies signed with the old key will then fail verification; do it when no payments are pending).
2. Update `/root/.secrets/zpay.env` and edit `APP_ZPAY_KEY` in `/opt/kesiv/deploy/env/api.env` on c4 (`env/api.env` is only readable by root).
3. `cd /opt/kesiv/deploy && docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env up -d api`.
4. Make a small test purchase.

## USDT (self-hosted BEpusdt)

**Known payment reliability defect, #26:** the configured upstream native notifier treats
any HTTP 200 as acknowledgement, while the current API can return `200 fail` on temporary
query failure. Do not treat the existing body-only retry test as upstream retry proof. Repair
and verify the [ACK contract](research/security-payment-audit-2026-11-08.md#f0--bepusdt-http-200-failure-replies-permanently-acknowledge-failed-callbacks)
and durable reconciliation before enabling real-money USDT.

BEpusdt v1.24.3 (`v03413/bepusdt:0716485@sha256:0a7d85…`, pinned by digest; compose profile `usdt`) receives USDT on Arbitrum One, X Layer and
TRON. It is INTERNAL: docker network `usdt` only (shared with `api`), no published port, no Caddy route.
Wallet addresses and the API token live in its console/volume, never in git.

1. Start it: `$DC --profile usdt up -d bepusdt` (`$DC` = `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env`).
   The first start creates the SQLite DB in the `bepusdt_data` volume. Pin the image digest after the first pull.
2. Open the console over an SSH tunnel (nothing is exposed): find the container IP
   `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' goods24-bepusdt-1`, then
   `ssh -L 8080:<container-ip>:8080 <host>` and browse http://127.0.0.1:8080. Change the default admin
   credentials immediately (first login, see the upstream docs shown in the console).
3. In the console add the receiving wallets (public addresses, safe to keep in docs):
   - Arbitrum One and X Layer (both EVM): `0xe2dc319ed904b2f798f262e3274b0f0c60e0653c`
   - TRON (TRC20): `TQkeoASc6X5itH4MyPZPPCfLM4M62cvMu5`
   Enable the networks `usdt.arbitrum`, `usdt.xlayer`, `usdt.trc20` and check the CNY/USDT rate source.
4. System settings -> API: copy the **API token** and put it in `env/api.env` as
   `APP_BEPUSDT_API_TOKEN=...` (with `APP_BEPUSDT_URL=http://bepusdt:8080`); `$DC up -d api`.
   Only `api` gets the token. The notify URL sent to BEpusdt is `http://api:8000/api/v1/pay/notify/bepusdt`
   (`APP_BEPUSDT_NOTIFY_URL` to override).
5. Admin -> Settings -> Payments: enable the USDT channels you want and set the surcharge (default 0).
   A channel stays invisible to buyers until enabled.
6. Test with a small real transfer on each network before announcing it (M1 exit gate: independent review
   before real money, AGENTS.md §1.11).

Operations: BEpusdt retries a failed paid notify 10 times with back-off (2, 4, 8 ... min). An order that
reaches `manual_review` (amount or re-query mismatch, late payment) is resolved by staff; the on-chain
transaction hash is in the payment record (`block_transaction_id`). Back up the `bepusdt_data` volume with
the other volumes (it only holds addresses, settings and trade history, no private keys).

## Xiaodou EPay relay (since 2026-11-09)

c4's route into mainland China loses packets in both directions (measured against Xiaodou and a
Tencent Cloud Guangzhou VPS at the same moments; the same targets are fine from other networks).
The ZPay client therefore has a fallback path (`backend/app/payments/egress.py`):

- **Sticky route, per gateway host:** the path that worked last goes first; the other is tried
  only when its connection cannot be opened (never after a request may have been sent, so no
  duplicate create/query). Direct (from c4) is the default; a failure moves the preference to the
  relay and only a relay failure moves it back. The last working IP is remembered; when every path
  fails, the domain is resolved again and direct retried once (moved load balancer).
- **Config (c4 `deploy/env/api.env`, not in git):** `APP_ZPAY_RELAYS=pay.xiaodouap.cn=107.173.10.79:20443`.
  Empty = direct only.
- **Relay (dev box):** `kesiv-xiaodou-relay.service` (socat :20443, accepts c4's IPv4 only via
  `range=` + ufw) dials `pay.xiaodouap.cn:443` by name. TLS is end to end between the app and
  Xiaodou; the relay forwards ciphertext. The dev box IP is on Xiaodou's merchant whitelist.
- Buyers never use the relay: their browsers open Xiaodou's pay page directly.
- Check: Admin -> Settings -> Payment -> Test now (expects `-1 订单不存在` for the random probe).
- Removal: clear `APP_ZPAY_RELAYS`, redeploy the api, stop the relay service.

## SEO and Google Search Console (review-v5.2 §6, ADR-024)

- Public pages (`/`, `/p/*`, `/a/*`, `/help`) are rendered by the API into the SPA shell for crawlers;
  `/sitemap.xml` and `/robots.txt` are dynamic. Caddy needs no extra config beyond the shipped
  `Caddyfile.prod` (it routes those paths to `api:8000` and serves the internal shell as `Host: web`
  on the container-internal `:80`). The API fetches `http://web/index.html` (override: `APP_WEB_INDEX_URL`).
- Production: Settings -> SEO -> "Allow search engines to index this site" **on** (the code default).
  Demo/staging: switch it **off** (everything becomes `noindex` and `robots.txt` = `Disallow: /`).
  White-label reseller stores stay `noindex` unless "Allow indexing of white-label reseller stores" is on.
- After the first prod deploy: add the property in Google Search Console (DNS TXT verification on the
  apex), **submit `https://<domain>/sitemap.xml`**, request indexing of `/`, and check the Pages report
  for "Excluded by noindex" on anything that should rank. Bing Webmaster Tools can import the property.
- Cloudflare: do not cache HTML aggressively (the API sets `s-maxage=300` and `max-age=0`); after a deploy
  purge `/` and `/p/*` if a "cache everything" rule exists, because HTML embeds hashed asset URLs.
- Optional share image: Settings -> SEO -> social share image (e.g. an `/og.png` shipped in the web image).

## Refreshing Cloudflare IP ranges

`scripts/check-cf-ips.sh` reports drift (exit 1); the weekly `Edge drift` workflow runs it and fails when Cloudflare publishes new ranges.

`curl https://www.cloudflare.com/ips-v4; curl https://www.cloudflare.com/ips-v6`, update `trusted_proxies static ...` and the date comment in `deploy/Caddyfile.prod`, validate (`caddy validate` in the web image), sync, `up -d web`.

## Known issue: runc 1.1 inside c4

The c4 container runs Docker with the default runtime set to runc 1.1 (EOL) because host LXD 4.0 has an AppArmor limitation that breaks newer runc. No compose change is needed. Fix: upgrade the host LXD to 5.21, then return Docker's default runtime to the bundled runc and recreate the containers. Until then, treat container escapes as a risk: containers already run with `cap_drop: ALL`, read-only roots and `no-new-privileges`.
