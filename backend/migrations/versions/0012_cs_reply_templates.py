"""Shared and personal canned replies for the CS inbox composer.

Revision ID: 0012_cs_reply_templates
Revises: 0011_operations_renewals
"""

from __future__ import annotations

from alembic import op

revision = "0012_cs_reply_templates"
down_revision = "0011_operations_renewals"
branch_labels = None
depends_on = None
PRED = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    # owner_id NULL = shared with every staff member of the tenant; otherwise that agent's own.
    op.execute("""CREATE TABLE cs_reply_templates (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        owner_id uuid REFERENCES users(id),
        title varchar(60) NOT NULL CHECK (length(btrim(title)) > 0),
        body varchar(2000) NOT NULL CHECK (length(btrim(body)) > 0),
        sort integer NOT NULL DEFAULT 0,
        created_by uuid NOT NULL REFERENCES users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute(
        "CREATE INDEX ix_cs_reply_templates_scope "
        "ON cs_reply_templates(tenant_id, owner_id, sort, id)"
    )
    op.execute("ALTER TABLE cs_reply_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cs_reply_templates FORCE ROW LEVEL SECURITY")
    scope = f"USING ({PRED}) WITH CHECK ({PRED})"
    op.execute(f"CREATE POLICY tenant_isolation ON cs_reply_templates {scope}")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cs_reply_templates TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cs_reply_templates")
