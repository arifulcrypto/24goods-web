"""v5 core: API may mark its own ``email.order`` outbox rows processed, and purge old audit
rows through a bounded SECURITY DEFINER function (no table-level DELETE on audit_log).

Revision ID: 0005_v5_core_grants
Revises: 0004_v5_growth
Create Date: 2026-11-06
"""

from __future__ import annotations

from alembic import op

revision = "0005_v5_core_grants"
down_revision = "0004_v5_growth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT UPDATE (processed_at, attempts, last_error, available_at) ON outbox TO app_api"
    )
    # Retention purge (settings audit.retention_days). Only rows older than 30 days can ever be
    # deleted, only for the tenant the session is pinned to (checked against
    # app.tenant_id, not the argument).
    op.execute(
        """CREATE FUNCTION purge_audit_log(p_tenant uuid, p_before timestamptz) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE n bigint;
                pinned uuid := NULLIF(current_setting('app.tenant_id', true), '')::uuid;
        BEGIN
            -- never trust the argument: the session's pinned tenant is the only one allowed
            IF pinned IS NULL OR p_tenant IS DISTINCT FROM pinned THEN
                RAISE EXCEPTION 'audit purge: tenant mismatch';
            END IF;
            IF p_before > now() - interval '30 days' THEN
                RAISE EXCEPTION 'audit retention below 30 days is not allowed';
            END IF;
            DELETE FROM audit_log WHERE tenant_id = p_tenant AND created_at < p_before;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION purge_audit_log(uuid, timestamptz) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION purge_audit_log(uuid, timestamptz) TO app_api")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_audit_log(uuid, timestamptz)")
    op.execute(
        "REVOKE UPDATE (processed_at, attempts, last_error, available_at) ON outbox FROM app_api"
    )
