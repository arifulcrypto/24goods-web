"""v5.2 blocking: user suspension columns + blocked_ips (docs/review-v5.1.md §5).

Revision ID: 0006_blocking_v52
Revises: 0005_v5_core_grants
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import op

revision = "0006_blocking_v52"
down_revision = "0005_v5_core_grants"
branch_labels = None
depends_on = None

PRED = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN suspended_at timestamptz")
    op.execute("ALTER TABLE users ADD COLUMN suspended_until timestamptz")
    op.execute(
        "ALTER TABLE users ADD COLUMN suspend_reason varchar(200) "
        "CHECK (suspend_reason IS NULL OR char_length(suspend_reason) <= 200)"
    )
    op.execute(
        """CREATE TABLE blocked_ips (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        cidr varchar(64) NOT NULL,
        note varchar(200),
        expires_at timestamptz,
        created_by uuid REFERENCES users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_blocked_ips_tenant_cidr UNIQUE (tenant_id, cidr))"""
    )
    op.execute("ALTER TABLE blocked_ips ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE blocked_ips FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON blocked_ips USING ({PRED}) WITH CHECK ({PRED})")
    op.execute("GRANT SELECT, INSERT, DELETE ON blocked_ips TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS blocked_ips")
    for c in ("suspend_reason", "suspended_until", "suspended_at"):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {c}")
