#!/usr/bin/env bash
# Pull-based continuous deployment for the demo host (no inbound access from CI).
# Run by goods24-demo-update.timer. Pulls :main from GHCR, recreates changed
# containers, health-checks through Caddy and reports a GitHub commit status.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${REPO:-arifulcrypto/24goods-web}"
COMPOSE=(docker compose -f deploy/docker-compose.yml --env-file deploy/.env --profile demo)
# shellcheck disable=SC1091
source deploy/.env

git fetch -q origin main && git merge -q --ff-only origin/main || echo "warn: git fast-forward skipped"

img_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || echo none; }
before="$(img_id "$API_IMAGE")$(img_id "$WEB_IMAGE")"
"${COMPOSE[@]}" pull -q api web
after="$(img_id "$API_IMAGE")$(img_id "$WEB_IMAGE")"
running=$("${COMPOSE[@]}" ps --status running -q | wc -l)
if [[ "$before" == "$after" && "$running" -ge 6 ]]; then
  exit 0  # nothing new
fi

sha=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$API_IMAGE" 2>/dev/null || true)
status() {
  [[ -n "$sha" && "$sha" != "<no value>" ]] || return 0
  gh api -X POST "repos/$REPO/statuses/$sha" -f state="$1" -f context="deploy/demo" \
    -f description="$2" >/dev/null 2>&1 || true
}
status pending "deploying to demo host"
if "${COMPOSE[@]}" up -d --wait --wait-timeout 300 --remove-orphans \
   && curl -fsS -o /dev/null "http://127.0.0.1:${PUBLISH_HTTP:-80}/api/v1/categories"; then
  status success "demo host running ${sha:0:7}"
  docker image prune -f >/dev/null
else
  status failure "demo deploy failed — see journalctl -u goods24-demo-update"
  exit 1
fi
