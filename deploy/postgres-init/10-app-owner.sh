#!/bin/sh
# Runs once on an empty data dir (docker-entrypoint-initdb.d).
# The schema owner is NOT a superuser: it can create the app roles (CREATEROLE) and
# the trusted pg_trgm extension, but it is bound by FORCE ROW LEVEL SECURITY.
set -eu
psql -v ON_ERROR_STOP=1 -v pw="$APP_OWNER_PASSWORD" --username "$POSTGRES_USER" --dbname postgres <<'SQL'
CREATE ROLE app_owner LOGIN NOSUPERUSER NOCREATEDB CREATEROLE NOBYPASSRLS PASSWORD :'pw';
CREATE DATABASE goods24 OWNER app_owner;
REVOKE ALL ON DATABASE goods24 FROM PUBLIC;
SQL
