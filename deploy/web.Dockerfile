# syntax=docker/dockerfile:1
# Builds both SPAs and bakes them + the Caddyfile into a Caddy image.
# Build context: repository root.

FROM node:22-alpine AS build
WORKDIR /src
RUN corepack enable
COPY frontend/ ./frontend/
WORKDIR /src/frontend
RUN corepack install && pnpm install --frozen-lockfile && pnpm -r build

FROM caddy:2.11-alpine
RUN apk upgrade --no-cache
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/frontend/apps/web/dist /srv/web
COPY --from=build /src/frontend/apps/admin/dist /srv/admin
