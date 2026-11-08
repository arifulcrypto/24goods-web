#!/usr/bin/env bash
# Kesiv production operations, run from the dev box. Never run on c4 itself.
#
#   scripts/prod.sh sync                  copy deploy/ files (+ TLS certs) to c4:/opt/kesiv/deploy
#   scripts/prod.sh bootstrap             FIRST RUN ONLY: generate secrets/env on c4 (MODE=prod), fetch admin login
#   scripts/prod.sh deploy                pull :main images and (re)start the stack (idempotent)
#   scripts/prod.sh status | ps           container state, health, disk, memory
#   scripts/prod.sh logs [svc] [args]     e.g. logs api -f --tail 100
#   scripts/prod.sh backup                run a backup on c4 now and copy it to /root/kesiv-backups/
#   scripts/prod.sh install-backup-cron   nightly backup on c4 (03:30), 14-day retention
#   scripts/prod.sh smoke                 read-only checks against https://shop.kesiv.com
#
# Secrets (never in git): /root/.secrets/{c4_ssh_pw,ghcr_token,zpay.env,prod-certs/}. See docs/production.md.
set -euo pipefail

HOST="${PROD_HOST:-141.98.198.150}"
PORT="${PROD_SSH_PORT:-2204}"
RUSER="${PROD_SSH_USER:-root}"
SECRETS="${PROD_SECRETS_DIR:-/root/.secrets}"
REMOTE="/opt/kesiv"
RDEPLOY="$REMOTE/deploy"
DOMAIN="${PROD_DOMAIN:-shop.kesiv.com}"
ORIGIN_PORT=8088
GHCR_USER="${GHCR_USER:-arifulcrypto}"
LOCAL_BACKUPS="${PROD_LOCAL_BACKUPS:-/root/kesiv-backups}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

c_ok() { printf '\033[32m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null || die "missing '$1' on this box"; }
need sshpass; need ssh; need tar; need curl
[[ -r "$SECRETS/c4_ssh_pw" ]] || die "missing $SECRETS/c4_ssh_pw"

SSH_OPTS=(-p "$PORT" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o LogLevel=ERROR)
# stdin passes through (used to pipe the GHCR token / tar streams)
rsh() { sshpass -f "$SECRETS/c4_ssh_pw" ssh "${SSH_OPTS[@]}" "$RUSER@$HOST" "$@"; }
rcp_from() { sshpass -f "$SECRETS/c4_ssh_pw" scp -P "$PORT" -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "$RUSER@$HOST:$1" "$2"; }
DC="cd $RDEPLOY && docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env"

ghcr_login() {
  [[ -r "$SECRETS/ghcr_token" ]] || die "missing $SECRETS/ghcr_token"
  # token travels over the ssh channel on stdin: never in argv, never written on c4 except docker's own login
  rsh "docker login ghcr.io -u '$GHCR_USER' --password-stdin >/dev/null" < "$SECRETS/ghcr_token"
}
ghcr_logout() { rsh "docker logout ghcr.io >/dev/null 2>&1 || true" || true; }

# ------------------------------------------------------------------ sync
cmd_sync() {
  local certs="$SECRETS/prod-certs"
  for f in origin.pem origin.key cf-origin-pull-ca.pem; do
    [[ -r "$certs/$f" ]] || die "missing $certs/$f"
  done
  rsh "mkdir -p $RDEPLOY/certs $REMOTE/backups && chmod 700 $REMOTE/backups"
  # code/config only: no .env, env/, secrets/, certs/ (handled below)
  (cd "$ROOT/deploy" && tar --exclude='./.env' --exclude='./env' --exclude='./secrets' --exclude='./certs' \
      --exclude='./systemd' --exclude='./demo-update.sh' -cf - .) \
    | rsh "tar -xf - -C $RDEPLOY --no-same-owner"
  # TLS material: origin cert/key (Cloudflare Origin CA) + the CA we trust for Authenticated Origin Pulls
  tar -C "$certs" -cf - origin.pem origin.key cf-origin-pull-ca.pem | rsh "tar -xf - -C $RDEPLOY/certs --no-same-owner"
  rsh "chmod 600 $RDEPLOY/certs/origin.key; chmod 644 $RDEPLOY/certs/origin.pem $RDEPLOY/certs/cf-origin-pull-ca.pem; chmod +x $RDEPLOY/bootstrap.sh"
  c_ok "sync ok -> $HOST:$RDEPLOY"
}

# ------------------------------------------------------------------ bootstrap (first run only)
cmd_bootstrap() {
  [[ -r "$SECRETS/zpay.env" ]] || die "missing $SECRETS/zpay.env"
  cmd_sync
  if rsh "test -f $RDEPLOY/.env"; then
    die "c4 is already bootstrapped ($RDEPLOY/.env exists). Use 'deploy'. (Regenerating secrets would orphan the vault keys and DB.)"
  fi
  local rzpay="$REMOTE/zpay.env.tmp"
  trap 'rsh "rm -f '"$rzpay"'" || true; ghcr_logout' EXIT
  rsh "umask 077; cat > $rzpay" < "$SECRETS/zpay.env"
  ghcr_login
  rsh "cd $RDEPLOY && MODE=prod PUBLIC_BASE_URL=https://$DOMAIN ZPAY_ENV_FILE=$rzpay ./bootstrap.sh"
  rsh "rm -f $rzpay"
  ghcr_logout
  trap - EXIT
  umask 077
  rcp_from "$RDEPLOY/secrets/prod-admin.txt" "$SECRETS/prod-admin.txt"
  chmod 600 "$SECRETS/prod-admin.txt"
  c_ok "bootstrap ok. Admin login saved to $SECRETS/prod-admin.txt. Next: scripts/prod.sh deploy"
  c_warn "Back up c4:$RDEPLOY/{secrets,env,.env} now ('scripts/prod.sh backup' does it): the vault private keys cannot be regenerated."
}

# ------------------------------------------------------------------ deploy
cmd_deploy() {
  rsh "test -f $RDEPLOY/.env" || die "not bootstrapped: run 'scripts/prod.sh bootstrap' first"
  trap ghcr_logout EXIT
  ghcr_login
  # remember what ran before, for rollback (docs/production.md)
  rsh "$DC images --format '{{.ContainerName}} {{.Repository}}:{{.Tag}} {{.ID}}' >> $REMOTE/image-history.log 2>/dev/null || true; date -u +%FT%TZ >> $REMOTE/image-history.log"
  rsh "$DC pull --quiet api web"
  rsh "$DC up -d --wait --wait-timeout 300 --remove-orphans"
  ghcr_logout
  trap - EXIT
  rsh "docker image prune -f >/dev/null"
  rsh "$DC ps"
  c_ok "deploy ok. Run: scripts/prod.sh smoke"
}

cmd_status() {
  rsh "$DC ps -a; echo; df -h $REMOTE | tail -1; free -m | head -2; echo; docker inspect --format '{{.Name}} {{.Config.Image}} {{ index .Config.Labels \"org.opencontainers.image.revision\" }}' \$(docker ps -q --filter label=com.docker.compose.project=goods24) 2>/dev/null | sed 's#^/##'"
}
cmd_logs() {
  local svc="${1:-}"; [[ $# -gt 0 ]] && shift
  # shellcheck disable=SC2086
  rsh "$DC logs --tail 200 $* $svc"
}

# ------------------------------------------------------------------ backups
# The remote script is the single source for manual + cron backups.
remote_backup_script() {
  cat "$ROOT/deploy/backup.sh"
}

install_backup_script() {
  remote_backup_script | rsh "cat > $REMOTE/backup.sh && chmod 700 $REMOTE/backup.sh"
}
cmd_backup() {
  rsh "test -f $RDEPLOY/.env" || die "not bootstrapped"
  install_backup_script
  rsh "$REMOTE/backup.sh"
  mkdir -p "$LOCAL_BACKUPS"; chmod 700 "$LOCAL_BACKUPS"; umask 077
  local latest staging
  latest=$(rsh "find $REMOTE/backups -mindepth 2 -maxdepth 2 -name COMPLETE -printf '%h\n' | sort | tail -1" | sed 's#.*/##')
  [[ "$latest" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] || die "no complete v2 backup found"
  staging=$(mktemp -d "$LOCAL_BACKUPS/.partial-$latest-XXXXXX")
  # Copy one immutable complete directory, then verify locally before publishing a receipt.
  rsh "tar -C $REMOTE/backups -cf - $latest" | tar -xf - -C "$staging" --no-same-owner
  (cd "$staging/$latest" && sha256sum -c MANIFEST.sha256)
  [[ -s "$staging/$latest/COMPLETE" ]] || die "incomplete backup"
  [[ ! -e "$LOCAL_BACKUPS/$latest" ]] || die "backup generation already exists locally"
  mv "$staging/$latest" "$LOCAL_BACKUPS/$latest"
  rmdir "$staging"
  rsh "umask 077; date -u +%FT%TZ > $REMOTE/backups/$latest/OFFSITE_VERIFIED"
  c_ok "encrypted complete set copied and verified: $LOCAL_BACKUPS/$latest"
  c_warn "Retain independently held recovery keys and run an isolated restore test. No automatic pruning."

}
cmd_install_backup_cron() {
  install_backup_script
  rsh "printf '%s\n' 'SHELL=/bin/bash' 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' \
    '30 3 * * * root $REMOTE/backup.sh >> /var/log/kesiv-backup.log 2>&1' > /etc/cron.d/kesiv-backup && chmod 644 /etc/cron.d/kesiv-backup"
  rsh "test -x /usr/sbin/cron -o -x /usr/sbin/crond" || c_warn "cron daemon not found on c4: apt-get install -y cron"
  c_ok "nightly encrypted backup installed (03:30 UTC, no automatic pruning, log /var/log/kesiv-backup.log). Pull copies with 'scripts/prod.sh backup'."
}

# ------------------------------------------------------------------ smoke (read-only)
cmd_smoke() {
  local base="https://$DOMAIN" fail=0 tmp
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  ok() { c_ok "PASS $*"; }
  bad() { printf '\033[31mFAIL %s\033[0m\n' "$*" >&2; fail=1; }

  local code
  code=$(curl -sS -m 15 -o "$tmp/site.json" -w '%{http_code}' "$base/api/v1/site" || echo 000)
  if [[ "$code" == 200 ]] && grep -q '"brand" *: *"Kesiv"' "$tmp/site.json"; then ok "/api/v1/site 200, brand Kesiv"
  else bad "/api/v1/site -> $code (expected 200 with brand Kesiv)"; fi
  if grep -qi 'mock data\|demo' "$tmp/site.json"; then bad "/api/v1/site mentions demo/mock text"; else ok "no demo/mock text in /api/v1/site"; fi

  curl -sS -m 15 -D "$tmp/h.txt" -o "$tmp/index.html" "$base/" || true
  for h in strict-transport-security x-content-type-options x-frame-options content-security-policy referrer-policy; do
    grep -qi "^$h:" "$tmp/h.txt" && ok "header $h" || bad "SPA missing header $h"
  done
  grep -qi '^server: *caddy' "$tmp/h.txt" && bad "Server header leaked" || ok "no Server header"
  grep -qi '<div id="app"' "$tmp/index.html" && ok "SPA index served" || bad "SPA index not served"
  # Unknown paths fall through to the SPA index (client-side 404), so check the body, not the status.
  mp=$(curl -sS -m 15 "$base/mockpay/api.php?act=order&out_trade_no=probe" || true)
  if grep -q '<div id="app"' <<<"$mp" || ! grep -qiE 'trade_no|"code"' <<<"$mp"; then
    ok "/mockpay gateway not served (SPA fallback only)"
  else bad "/mockpay gateway is served"; fi

  for path in /admin/ /admin/index.html /api/v1/admin/me; do
    code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$base$path" || echo 000)
    if [[ "$code" == 403 ]]; then ok "$path -> 403 from this (non-allowlisted) box"
    else bad "$path -> $code (expected 403; is this box in ADMIN_ALLOW_IPS?)"; fi
  done

  # direct hit on the origin without the Cloudflare client cert must not complete
  code=$(curl -ksS -m 10 -o /dev/null -w '%{http_code}' "https://$HOST:$ORIGIN_PORT/" 2>/dev/null || true)
  [[ -z "$code" ]] && code=000
  if [[ "$code" == 000 ]]; then ok "origin $HOST:$ORIGIN_PORT rejects clients without the AOP cert"
  else bad "origin $HOST:$ORIGIN_PORT answered HTTP $code without a client cert"; fi
  code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "http://$HOST:$ORIGIN_PORT/" 2>/dev/null || true)
  [[ -z "$code" || "$code" == 000 || "$code" == 400 ]] && ok "origin plain HTTP not served" || bad "origin plain HTTP answered $code"

  [[ $fail -eq 0 ]] && c_ok "smoke: all checks passed" || die "smoke: FAILURES above"
}

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

cmd="${1:-}"; [[ $# -gt 0 ]] && shift
case "$cmd" in
  sync) cmd_sync ;;
  bootstrap) cmd_bootstrap ;;
  deploy) cmd_deploy ;;
  status|ps) cmd_status ;;
  logs) cmd_logs "$@" ;;
  backup) cmd_backup ;;
  install-backup-cron) cmd_install_backup_cron ;;
  smoke) cmd_smoke ;;
  *) usage ;;
esac
