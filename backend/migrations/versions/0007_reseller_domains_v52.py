"""v5.2 reseller custom domains via Cloudflare for SaaS (docs/cloudflare-reseller-domains.md).

Revision ID: 0007_reseller_domains_v52
Revises: 0006_blocking_v52
Create Date: 2026-11-07
"""

from __future__ import annotations

from alembic import op

revision = "0007_reseller_domains_v52"
down_revision = "0006_blocking_v52"
branch_labels = None
depends_on = None

PRED = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE reseller_domains (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        reseller_id uuid NOT NULL REFERENCES resellers(id),
        hostname varchar(253) NOT NULL,
        cf_id varchar(64),
        status varchar(16) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'active', 'failed', 'deleted')),
        ssl_status varchar(32),
        verification jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        verified_at timestamptz,
        CONSTRAINT uq_reseller_domains_hostname UNIQUE (hostname))"""
    )
    op.execute("CREATE INDEX ix_reseller_domains_reseller ON reseller_domains (reseller_id)")
    op.execute("ALTER TABLE reseller_domains ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE reseller_domains FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON reseller_domains USING ({PRED}) WITH CHECK ({PRED})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON reseller_domains TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reseller_domains")
