# syntax=docker/dockerfile:1
# Builds both SPAs and bakes them + the Caddyfile into a Caddy image.
# Build context: repository root.

FROM node:22-alpine AS build
WORKDIR /src
RUN corepack enable
WORKDIR /src/frontend
# Manifests first: the dependency layer is reused (CI layer cache) unless the lockfile changes.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/apps/web/package.json ./apps/web/
COPY frontend/apps/admin/package.json ./apps/admin/
COPY frontend/packages/shared/package.json ./packages/shared/
RUN corepack install && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# Caddy rebuilt with patched deps (go-jose v4.1.6: CVE-2026-116587; x/net v0.62.0:
# CVE-2026-78661) until upstream
# ships a release containing it. Remove this stage once caddy:2.11-alpine is clean.
FROM caddy:2.11-builder-alpine AS caddy
ENV GOFLAGS=-p=1
RUN xcaddy build v2.11.8 --output /usr/bin/caddy --with github.com/go-jose/go-jose/v4@v4.1.6 \
    --replace golang.org/x/net=golang.org/x/net@v0.62.0
RUN /usr/bin/caddy build-info | grep -qE 'go-jose/go-jose/v4[[:space:]]+v4\.1\.6' \
 && /usr/bin/caddy build-info | grep -qE 'golang.org/x/net[[:space:]]+v0\.62\.0'

FROM caddy:2.11-alpine
# CI passes the ISO week: a cached `apk upgrade` layer is refreshed at least weekly.
ARG OS_PATCH_EPOCH=none
RUN echo "os patches: $OS_PATCH_EPOCH" && apk upgrade --no-cache
COPY --from=caddy /usr/bin/caddy /usr/bin/caddy
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/apps/web/dist /srv/web
COPY --from=build /src/frontend/apps/admin/dist /srv/admin
