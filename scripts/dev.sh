#!/usr/bin/env bash
# Kesiv local development stack — hot reload, mock data, everything on 127.0.0.1.
#
#   scripts/dev.sh setup     one-time: Python 3.12 venv (uv), backend[dev], pnpm install, keys + env
#   scripts/dev.sh up        start PG + Redis (Docker), migrate, seed, API, delivery, mockpay, web, admin
#   scripts/dev.sh up api    start only some processes (api|delivery|mockpay|web|admin), deps implied
#   scripts/dev.sh down      stop all processes (containers too with --all)
#   scripts/dev.sh status    process / port / memory overview
#   scripts/dev.sh logs X    tail a process log (api|delivery|mockpay|web|admin)
#   scripts/dev.sh restart X restart one process (delivery/mockpay have no hot reload of their own)
#   scripts/dev.sh test      backend tests against the dev containers (own DB), frontend checks
#   scripts/dev.sh reset     drop the dev database volume and secrets (fresh seed on next `up`)
#   scripts/dev.sh creds     print the seeded demo logins
#
# URLs (open through an SSH tunnel, see docs/dev-setup.md):
#   buyer  http://localhost:5173/        admin  http://localhost:5174/admin/
#
# Same security shape as production (separate DB roles, RLS, vault keys split between
# api [public] and delivery [private]); only rate limits and PoW difficulty are relaxed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV="$ROOT/.dev"
VENV="$ROOT/backend/.venv"
PY="$VENV/bin/python"
RUN="$DEV/run"
LOGS="$DEV/logs"
ENVD="$DEV/env"
KEYS="$DEV/keys"

PG_NAME=kesiv-dev-pg
REDIS_NAME=kesiv-dev-redis
PG_PORT="${DEV_PG_PORT:-55440}"
REDIS_PORT="${DEV_REDIS_PORT:-56390}"
API_PORT=18000
DELIVERY_PORT=18001
MOCKPAY_PORT=18002
WEB_PORT=5173
ADMIN_PORT=5174
ALL_PROCS=(api delivery mockpay web admin)

c_ok() { printf '\033[32m%s\033[0m\n' "$*"; }
c_warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
rnd() { openssl rand -hex "${1:-24}"; }

need() { command -v "$1" >/dev/null || die "missing '$1' — $2"; }

# ------------------------------------------------------------------ setup
cmd_setup() {
  need docker "install Docker"
  need pnpm "install Node 20+ and pnpm (corepack enable)"
  need openssl "install openssl"
  if [[ ! -x "$PY" ]]; then
    need uv "pip install uv  (or see https://docs.astral.sh/uv/)"
    uv python install 3.12
    uv venv -p 3.12 "$VENV"
  fi
  VIRTUAL_ENV="$VENV" uv pip install -q -e "$ROOT/backend[dev]"
  (cd "$ROOT/frontend" && pnpm install --frozen-lockfile)
  mkdir -p "$RUN" "$LOGS" "$ENVD" "$KEYS"
  chmod 700 "$DEV"
  [[ -f "$DEV/.secrets" ]] || write_env
  c_ok "setup done — next: scripts/dev.sh up"
}

write_env() {
  mkdir -p "$ENVD" "$KEYS"
  (cd "$ROOT/backend" && "$PY" -m app.devtools.keygen --out-dir "$KEYS" >/dev/null)
  # shellcheck disable=SC1091
  source "$KEYS/secrets.env"
  local pg_su app_owner db_api db_del redis_pw zpay pow lookup admin agent buyer
  pg_su=$(rnd); app_owner=$(rnd); db_api=$(rnd); db_del=$(rnd); redis_pw=$(rnd)
  zpay=$(rnd 16); pow=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
  lookup=$(head -c 32 /dev/urandom | base64 | tr -d '\n')
  admin=$(rnd 6); agent=$(rnd 6); buyer=$(rnd 6)
  local redis="redis://default:$redis_pw@127.0.0.1:$REDIS_PORT"
  local pg="127.0.0.1:$PG_PORT/goods24"

  cat > "$DEV/.secrets" <<EOF
PG_SUPERUSER_PASSWORD=$pg_su
APP_OWNER_PASSWORD=$app_owner
REDIS_PASSWORD=$redis_pw
EOF
  cat > "$ENVD/migrate.env" <<EOF
APP_ENV=dev
APP_MIGRATE_DATABASE_URL=postgresql+asyncpg://app_owner:$app_owner@$pg
APP_DB_API_PASSWORD=$db_api
APP_DB_DELIVERY_PASSWORD=$db_del
EOF
  cat > "$ENVD/seed.env" <<EOF
APP_ENV=dev
APP_MIGRATE_DATABASE_URL=postgresql+asyncpg://app_owner:$app_owner@$pg
APP_REDIS_URL=$redis/0
APP_UPLOAD_DIR=$DEV/uploads
APP_VAULT_PUBLIC_KEYS_FILE=$KEYS/vault_public_keys.json
APP_VAULT_ACTIVE_KID=$APP_VAULT_ACTIVE_KID
APP_VAULT_FINGERPRINT_KEY=$APP_VAULT_FINGERPRINT_KEY
APP_QUOTE_HMAC_KEY=$APP_QUOTE_HMAC_KEY
APP_SEED_ADMIN_PASSWORD=admin-$admin
APP_SEED_AGENT_PASSWORD=agent-$agent
APP_SEED_BUYER_PASSWORD=buyer-$buyer
EOF
  cat > "$ENVD/api.env" <<EOF
APP_ENV=dev
APP_LOG_LEVEL=INFO
APP_DATABASE_URL=postgresql+asyncpg://app_api:$db_api@$pg
APP_REDIS_URL=$redis/0
APP_TRUSTED_PROXIES=127.0.0.1/32,::1/128
APP_PUBLIC_BASE_URL=http://localhost:$WEB_PORT
APP_COOKIE_SECURE=false
APP_UPLOAD_DIR=$DEV/uploads
APP_VAULT_PUBLIC_KEYS_FILE=$KEYS/vault_public_keys.json
APP_VAULT_ACTIVE_KID=$APP_VAULT_ACTIVE_KID
APP_VAULT_FINGERPRINT_KEY=$APP_VAULT_FINGERPRINT_KEY
APP_QUOTE_HMAC_KEY=$APP_QUOTE_HMAC_KEY
APP_INTERNAL_TOKEN=$APP_INTERNAL_TOKEN
APP_DELIVERY_URL=http://127.0.0.1:$DELIVERY_PORT
APP_POW_KEY=$pow
APP_LOOKUP_KEY=$lookup
APP_ZPAY_PID=1001
APP_ZPAY_KEY=$zpay
APP_ZPAY_GATEWAY=/mockpay
APP_ZPAY_API_BASE=http://127.0.0.1:$MOCKPAY_PORT
# dev-only relaxations: every request comes from 127.0.0.1 through the Vite proxy
APP_POW_BITS=12
APP_INSTANT_PER_IP_HOUR=1000
APP_INSTANT_PER_IP_DAY=10000
APP_INSTANT_PER_NET_DAY=10000
APP_LOOKUP_PER_IP_HOUR=1000
EOF
  cat > "$ENVD/delivery.env" <<EOF
APP_ENV=dev
APP_DELIVERY_DATABASE_URL=postgresql+asyncpg://app_delivery:$db_del@$pg
APP_REDIS_URL=$redis/0
APP_VAULT_PRIVATE_KEYS_FILE=$KEYS/vault_private_keys.json
APP_INTERNAL_TOKEN=$APP_INTERNAL_TOKEN
APP_DELIVERY_HOST=127.0.0.1
APP_DELIVERY_PORT=$DELIVERY_PORT
APP_HEALTH_PORT=$DELIVERY_PORT
APP_REVEAL_MAX_PER_MIN=120
EOF
  cat > "$ENVD/mockpay.env" <<EOF
APP_ENV=dev
APP_REDIS_URL=$redis/0
APP_ZPAY_PID=1001
APP_ZPAY_KEY=$zpay
APP_MOCKPAY_HOST=127.0.0.1
APP_MOCKPAY_PORT=$MOCKPAY_PORT
APP_MOCKPAY_NOTIFY_BASE=http://127.0.0.1:$API_PORT
APP_HEALTH_PORT=$MOCKPAY_PORT
EOF
  chmod 600 "$DEV/.secrets" "$ENVD"/*.env "$KEYS"/*
  mkdir -p "$DEV/uploads"
}

# ------------------------------------------------------------------ containers
secret() { grep "^$1=" "$DEV/.secrets" | cut -d= -f2-; }

ensure_containers() {
  [[ -f "$DEV/.secrets" ]] || die "run: scripts/dev.sh setup"
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_NAME"; then
    if docker ps -a --format '{{.Names}}' | grep -qx "$PG_NAME"; then
      docker start "$PG_NAME" >/dev/null
    else
      docker run -d --name "$PG_NAME" -m 160m --restart unless-stopped \
        -p "127.0.0.1:$PG_PORT:5432" \
        -e POSTGRES_PASSWORD="$(secret PG_SUPERUSER_PASSWORD)" \
        -e APP_OWNER_PASSWORD="$(secret APP_OWNER_PASSWORD)" \
        -v kesiv-dev-pgdata:/var/lib/postgresql/data \
        -v "$ROOT/deploy/postgres-init:/docker-entrypoint-initdb.d:ro" \
        postgres:17-alpine >/dev/null
    fi
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$REDIS_NAME"; then
    if docker ps -a --format '{{.Names}}' | grep -qx "$REDIS_NAME"; then
      docker start "$REDIS_NAME" >/dev/null
    else
      docker run -d --name "$REDIS_NAME" -m 48m --restart unless-stopped \
        -p "127.0.0.1:$REDIS_PORT:6379" redis:7-alpine \
        redis-server --requirepass "$(secret REDIS_PASSWORD)" --save "" --appendonly no >/dev/null
    fi
  fi
  printf 'waiting for postgres'
  for _ in $(seq 60); do
    if docker exec "$PG_NAME" pg_isready -U postgres -d goods24 -q 2>/dev/null; then echo; return; fi
    printf .; sleep 1
  done
  die "postgres did not become ready (docker logs $PG_NAME)"
}

with_env() { # with_env <envfile> <cmd...> — runs in backend/ with only that env file loaded
  local f="$1"; shift
  (cd "$ROOT/backend" && set -a && source "$f" && set +a && exec "$@")
}

migrate_seed() {
  # backend tests (conftest) share this PG cluster and reset the cluster-wide app_owner
  # password to their own value: re-assert ours before migrating.
  # (app_api / app_delivery too: the test migration creates them with test passwords)
  docker exec "$PG_NAME" psql -U postgres -qtAc \
    "ALTER ROLE app_owner PASSWORD '$(secret APP_OWNER_PASSWORD)'" >/dev/null
  local r pw
  for r in app_api:APP_DB_API_PASSWORD app_delivery:APP_DB_DELIVERY_PASSWORD; do
    pw=$(grep "^${r#*:}=" "$ENVD/migrate.env" | cut -d= -f2-)
    docker exec "$PG_NAME" psql -U postgres -qtAc \
      "DO \$\$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='${r%%:*}') THEN
         ALTER ROLE ${r%%:*} PASSWORD '$pw'; END IF; END \$\$" >/dev/null
  done
  echo "migrate…"; with_env "$ENVD/migrate.env" "$PY" -m alembic upgrade head >"$LOGS/migrate.log" 2>&1 \
    || { tail -20 "$LOGS/migrate.log"; die "migration failed"; }
  echo "seed…"; with_env "$ENVD/seed.env" "$PY" -m app.devtools.seed >"$LOGS/seed.log" 2>&1 \
    || { tail -20 "$LOGS/seed.log"; die "seed failed"; }
}

# ------------------------------------------------------------------ processes
pidfile() { echo "$RUN/$1.pid"; }
running() { local p; p=$(cat "$(pidfile "$1")" 2>/dev/null) && kill -0 "$p" 2>/dev/null; }

start_proc() {
  local name="$1"
  if running "$name"; then echo "$name already running"; return; fi
  local log="$LOGS/$name.log"
  : >"$log"
  case "$name" in
    api)
      with_env "$ENVD/api.env" "$PY" -m uvicorn app.main:create_app --factory \
        --host 127.0.0.1 --port "$API_PORT" --reload --reload-dir app >>"$log" 2>&1 &
      ;;
    delivery)
      with_env "$ENVD/delivery.env" "$VENV/bin/watchfiles" --filter python \
        "$PY -m app.delivery" app >>"$log" 2>&1 &
      ;;
    mockpay)
      with_env "$ENVD/mockpay.env" "$VENV/bin/watchfiles" --filter python \
        "$PY -m app.devtools.mock_zpay" app/devtools app/payments >>"$log" 2>&1 &
      ;;
    web | admin)
      (cd "$ROOT/frontend" && VITE_BACKEND="http://127.0.0.1:$API_PORT" \
        VITE_MOCKPAY="http://127.0.0.1:$MOCKPAY_PORT" NODE_OPTIONS=--max-old-space-size=512 \
        exec pnpm --filter "@24goods/$name" exec vite --host 127.0.0.1) >>"$log" 2>&1 &
      ;;
    *) die "unknown process '$name' (api|delivery|mockpay|web|admin)" ;;
  esac
  echo $! >"$(pidfile "$name")"
}

stop_proc() {
  local name="$1" p
  p=$(cat "$(pidfile "$name")" 2>/dev/null) || return 0
  if kill -0 "$p" 2>/dev/null; then
    pkill -TERM -P "$p" 2>/dev/null || true
    kill -TERM "$p" 2>/dev/null || true
    for _ in $(seq 20); do kill -0 "$p" 2>/dev/null || break; sleep 0.25; done
    kill -KILL "$p" 2>/dev/null || true
  fi
  rm -f "$(pidfile "$name")"
}

port_of() {
  case "$1" in api) echo $API_PORT ;; delivery) echo $DELIVERY_PORT ;; mockpay) echo $MOCKPAY_PORT ;;
    web) echo $WEB_PORT ;; admin) echo $ADMIN_PORT ;; esac
}

wait_port() {
  local name="$1" port; port=$(port_of "$1")
  for _ in $(seq 90); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then c_ok "  $name  :$port"; return; fi
    running "$name" || { tail -15 "$LOGS/$name.log"; die "$name exited (log: $LOGS/$name.log)"; }
    sleep 1
  done
  c_warn "  $name not listening on :$port yet (log: $LOGS/$name.log)"
}

cmd_up() {
  local procs=("$@")
  [[ ${#procs[@]} -eq 0 ]] && procs=("${ALL_PROCS[@]}")
  [[ -x "$PY" ]] || die "run: scripts/dev.sh setup"
  mkdir -p "$RUN" "$LOGS"
  ensure_containers
  migrate_seed
  for p in "${procs[@]}"; do start_proc "$p"; done
  echo "starting:"
  for p in "${procs[@]}"; do wait_port "$p"; done
  echo
  echo "buyer  http://localhost:$WEB_PORT/"
  echo "admin  http://localhost:$ADMIN_PORT/admin/"
  echo "API    http://127.0.0.1:$API_PORT/api/v1/site   (docs off by design)"
  echo "logins: scripts/dev.sh creds   ·   from your PC: see docs/dev-setup.md (SSH tunnel)"
}

cmd_down() {
  for p in "${ALL_PROCS[@]}"; do stop_proc "$p"; done
  if [[ "${1:-}" == "--all" ]]; then docker stop "$PG_NAME" "$REDIS_NAME" >/dev/null 2>&1 || true; fi
  c_ok "stopped"
}

cmd_status() {
  for p in "${ALL_PROCS[@]}"; do
    if running "$p"; then
      c_ok "$(printf '%-9s up    :%s  pid %s' "$p" "$(port_of "$p")" "$(cat "$(pidfile "$p")")")"
    else printf '%-9s down\n' "$p"; fi
  done
  docker ps --filter "name=kesiv-dev-" --format '{{.Names}}  {{.Status}}'
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' "$PG_NAME" "$REDIS_NAME" 2>/dev/null || true
  free -m | sed -n 1,2p
}

cmd_creds() {
  local f="$ENVD/seed.env"
  [[ -f "$f" ]] || die "run setup first"
  echo "buyer  buyer@demo.local / $(grep ^APP_SEED_BUYER_PASSWORD "$f" | cut -d= -f2)  → http://localhost:$WEB_PORT/"
  echo "agent  agent@demo.local / $(grep ^APP_SEED_AGENT_PASSWORD "$f" | cut -d= -f2)  → http://localhost:$ADMIN_PORT/admin/"
  echo "admin  admin@demo.local / $(grep ^APP_SEED_ADMIN_PASSWORD "$f" | cut -d= -f2)  → http://localhost:$ADMIN_PORT/admin/"
}

cmd_test() {
  ensure_containers
  local su; su=$(secret PG_SUPERUSER_PASSWORD)
  # capture first: `| grep -q` under pipefail can fail on SIGPIPE and wrongly re-create the DB
  [[ "$(docker exec "$PG_NAME" psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='test'")" == 1 ]] \
    || docker exec "$PG_NAME" createdb -U postgres test
  (cd "$ROOT/backend" \
    && export TEST_DATABASE_URL="postgresql+asyncpg://postgres:$su@127.0.0.1:$PG_PORT/test" \
       TEST_REDIS_URL="redis://default:$(secret REDIS_PASSWORD)@127.0.0.1:$REDIS_PORT/15" \
       TEST_REQUIRE_SERVICES=1 \
    && "$VENV/bin/ruff" check . && "$VENV/bin/ruff" format --check . \
    && "$PY" -m pytest -q "$@")
  (cd "$ROOT/frontend" && export NODE_OPTIONS=--max-old-space-size=768 && pnpm lint && pnpm typecheck && pnpm test)
}

cmd_reset() {
  cmd_down
  docker rm -f "$PG_NAME" "$REDIS_NAME" >/dev/null 2>&1 || true
  docker volume rm kesiv-dev-pgdata >/dev/null 2>&1 || true
  rm -rf "$DEV"
  c_ok "dev data removed — run: scripts/dev.sh setup && scripts/dev.sh up"
}

case "${1:-}" in
  setup) cmd_setup ;;
  up) shift; cmd_up "$@" ;;
  down) shift; cmd_down "${1:-}" ;;
  restart) [[ -n "${2:-}" ]] || die "restart <process>"; stop_proc "$2"; start_proc "$2"; wait_port "$2" ;;
  status) cmd_status ;;
  logs) [[ -n "${2:-}" ]] || die "logs <process>"; tail -n 100 -f "$LOGS/$2.log" ;;
  creds) cmd_creds ;;
  test) shift; cmd_test "$@" ;;
  reset) cmd_reset ;;
  *) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
