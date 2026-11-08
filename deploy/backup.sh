#!/usr/bin/env bash
# Explicit maintenance action. Encrypted whole-set backup; never invoked by application code.
# Requires age, docker, flock, sha256sum, tar. Public recipients live outside Git.
set -euo pipefail
umask 077
BK=${BACKUP_DIR:-/opt/kesiv/backups}
D=${DEPLOY_DIR:-/opt/kesiv/deploy}
RECIPIENTS=${BACKUP_RECIPIENTS:-/opt/kesiv/backup-recipients.txt}
VAULT_RECIPIENTS=${BACKUP_VAULT_RECIPIENTS:-/opt/kesiv/backup-vault-recipients.txt}
GATEWAY_STATE=${BACKUP_GATEWAY_STATE:-required} # required | disabled (only when no real USDT gateway)
for cmd in age docker flock sha256sum tar; do command -v "$cmd" >/dev/null; done
[[ -s "$RECIPIENTS" && -s "$VAULT_RECIPIENTS" && -f "$D/.env" ]]
[[ "$GATEWAY_STATE" == required || "$GATEWAY_STATE" == disabled ]]
mkdir -p "$BK"; chmod 700 "$BK"
exec 9>"$BK/.backup.lock"
flock -n 9 || { echo 'backup already running' >&2; exit 75; }
TS=$(date -u +%Y%m%dT%H%M%SZ)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')
STAGE=$(mktemp -d "$BK/.partial-$TS-XXXXXX")
DC=(docker compose -f "$D/docker-compose.yml" -f "$D/docker-compose.prod.yml" --env-file "$D/.env")
RUNNING=()
RESUME_FAILED=0
resume() {
  if ((${#RUNNING[@]})); then
    "${DC[@]}" start "${RUNNING[@]}" >/dev/null || RESUME_FAILED=1
    RUNNING=()
  fi
}
cleanup() { resume; rm -rf -- "$STAGE"; }
trap cleanup EXIT
# Fencing writers gives a consistent PG/media/provider capture. Preserve stopped services.
mapfile -t ACTIVE < <("${DC[@]}" ps --status running --services)
for svc in api delivery bepusdt; do
  for active in "${ACTIVE[@]}"; do [[ "$active" != "$svc" ]] || RUNNING+=("$svc"); done
done
if ((${#RUNNING[@]})); then "${DC[@]}" stop -t 45 "${RUNNING[@]}" >/dev/null; fi
"${DC[@]}" exec -T postgres pg_dump -U postgres -Fc goods24 | age -R "$RECIPIENTS" > "$STAGE/database.dump.age"
# No live SQLite copy: gateway was cleanly stopped above, and all WAL/SHM files are included.
archive_volume() {
  docker volume inspect "$1" >/dev/null
  docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --memory 128m --pids-limit 64 -v "$1:/data:ro" --entrypoint tar postgres:17-alpine -C /data -czf - .
}
archive_volume goods24_uploads | age -R "$RECIPIENTS" > "$STAGE/uploads.tgz.age"
if [[ "$GATEWAY_STATE" == required ]]; then
  archive_volume goods24_bepusdt_data | age -R "$RECIPIENTS" > "$STAGE/bepusdt.tgz.age"
fi
# Vault private KEKs use a separate recovery recipient/access policy.
tar -C "$D" --exclude=secrets/vault_private_keys.json -czf - .env env secrets docker-compose.yml docker-compose.prod.yml \
  | age -R "$RECIPIENTS" > "$STAGE/config.tgz.age"
tar -C "$D" -czf - secrets/vault_private_keys.json | age -R "$VAULT_RECIPIENTS" > "$STAGE/vault-keys.tgz.age"
"${DC[@]}" exec -T postgres psql -U postgres -d goods24 -Atc 'SELECT version_num FROM alembic_version' > "$STAGE/schema.txt"
"${DC[@]}" images --format json > "$STAGE/images.json"
printf 'format=24goods-backup-v2\ncreated_at=%s\ngateway_state=%s\nconsistency=writers-fenced\n' "$(date -u +%FT%TZ)" "$GATEWAY_STATE" > "$STAGE/metadata.txt"
resume
((RESUME_FAILED == 0)) || { echo 'writer restart failed; backup not published' >&2; exit 1; }
(cd "$STAGE" && sha256sum -- *.age schema.txt images.json metadata.txt > MANIFEST.sha256 && sha256sum -c MANIFEST.sha256 >/dev/null)
# Final marker is inside the atomic directory rename; partial sets are never complete.
printf '%s\n' "$TS" > "$STAGE/COMPLETE"
sync -f "$STAGE"
mv "$STAGE" "$BK/$TS"
STAGE=$(mktemp -d "$BK/.cleanup-$TS-XXXXXX")
echo "backup complete: $TS (encrypted; offsite/restore verification still required)"
# No automatic pruning: require offsite receipt AND a tested retention/recovery policy.
