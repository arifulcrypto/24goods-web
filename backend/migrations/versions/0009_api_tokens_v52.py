"""v5.2 staff API tokens (docs/review-v5.2.md section 5).

Revision ID: 0009_api_tokens_v52
Revises: 0008_manual_stock_v52
Create Date: 2026-11-07

Only the SHA-256 of a token is stored. app_api may update only the usage/revocation columns.
"""

from __future__ import annotations

from alembic import op

revision = "0009_api_tokens_v52"
down_revision = "0008_manual_stock_v52"
branch_labels = None
depends_on = None

PRED = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE api_tokens (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        user_id uuid NOT NULL REFERENCES users(id),
        name varchar(60) NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        prefix varchar(8) NOT NULL,
        token_hash char(64) NOT NULL,
        scopes text[] NOT NULL,
        expires_at timestamptz,
        last_used_at timestamptz,
        last_used_ip varchar(64),
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_api_tokens_hash UNIQUE (token_hash))"""
    )
    op.execute("CREATE INDEX ix_api_tokens_tenant ON api_tokens (tenant_id, created_at DESC)")
    op.execute("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE api_tokens FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON api_tokens USING ({PRED}) WITH CHECK ({PRED})")
    op.execute("GRANT SELECT, INSERT ON api_tokens TO app_api")
    op.execute("GRANT UPDATE (last_used_at, last_used_ip, revoked_at) ON api_tokens TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_tokens")
