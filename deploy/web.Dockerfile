# syntax=docker/dockerfile:1
# Builds both SPAs and bakes them + the Caddyfile into a Caddy image.
# Build context: repository root.

# Node 26 = Active LTS (since 2026-10-28, EOL 2029-04-30). Node >= 25 images ship without
# Corepack, so pnpm is installed with npm at the exact version pinned in package.json
# `packageManager` (single source of truth; Dependabot bumps it there). The bundled npm (11.20)
# runs no install scripts unless allowed; pnpm's own install.js links its native musl binary and
# fails loudly if it is missing (instead of the placeholder downloading one on first use).
FROM node:26-alpine@sha256:8996bb5821d62a8fd9bb27c1cb07462f3fdce6e47d640c6a881ead0b064ce216 AS build
WORKDIR /src/frontend
COPY frontend/package.json ./
RUN pm="$(node -p "require('./package.json').packageManager.split('+')[0]")" \
 && case "$pm" in pnpm@[0-9]*) ;; *) echo "unexpected packageManager: $pm" >&2; exit 1 ;; esac \
 && npm install --global --no-audit --no-fund --no-update-notifier --allow-scripts=pnpm "$pm" \
 && test "pnpm@$(pnpm --version)" = "$pm"
# Manifests first: the dependency layer is reused (CI layer cache) unless the lockfile changes.
COPY frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/apps/web/package.json ./apps/web/
COPY frontend/apps/admin/package.json ./apps/admin/
COPY frontend/packages/shared/package.json ./packages/shared/
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# Caddy rebuilt with patched deps (go-jose v4.1.6: CVE-2026-116587; x/net v0.62.0:
# CVE-2026-78661) until upstream
# ships a release containing it. Remove this stage once caddy:2.11-alpine is clean.
FROM caddy:2.11-builder-alpine@sha256:3dbe908afe8ecb4f5505a80153c8de0fecd1b14d560798a6955c5cf115951561 AS caddy
ENV GOFLAGS=-p=1
RUN xcaddy build v2.11.8 --output /usr/bin/caddy --with github.com/go-jose/go-jose/v4@v4.1.6 \
    --replace golang.org/x/net=golang.org/x/net@v0.62.0
RUN /usr/bin/caddy build-info | grep -qE 'go-jose/go-jose/v4[[:space:]]+v4\.1\.6' \
 && /usr/bin/caddy build-info | grep -qE 'golang.org/x/net[[:space:]]+v0\.62\.0'

FROM caddy:2.11-alpine@sha256:78bd35025c7ba85961dd149ae670315129954e77abe2383ac47c89106b35ade9
# CI passes the ISO week: a cached `apk upgrade` layer is refreshed at least weekly.
ARG OS_PATCH_EPOCH=none
RUN echo "os patches: $OS_PATCH_EPOCH" && apk upgrade --no-cache
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/apps/web/dist /srv/web
COPY --from=build /src/frontend/apps/admin/dist /srv/admin
