"""Immutable EPay credentials and durable payment attempts.

Revision ID: 0010_payment_attempts
Revises: 0009_api_tokens_v52
"""

from alembic import op

revision = "0010_payment_attempts"
down_revision = "0009_api_tokens_v52"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE payment_gateways (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
        label varchar(80) NOT NULL, gateway varchar(500) NOT NULL, merchant_id varchar(64) NOT NULL,
        key_ciphertext bytea NOT NULL, mode varchar(16) NOT NULL CHECK (mode IN ('qr','redirect')),
        created_by uuid NOT NULL REFERENCES users, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id,id))""")
    op.execute("""CREATE TABLE payment_attempts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
        local_ref varchar(16) NOT NULL, gateway_ref varchar(64) NOT NULL UNIQUE,
        provider_id uuid, channel varchar(32) NOT NULL,
        amount_minor bigint NOT NULL CHECK(amount_minor>0),
        currency varchar(3) NOT NULL, status varchar(16) NOT NULL DEFAULT 'preparing',
        data jsonb NOT NULL DEFAULT '{}', expires_at timestamptz,
        attempts integer NOT NULL DEFAULT 0,
        checked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id,provider_id) REFERENCES payment_gateways(tenant_id,id))""")
    op.execute(
        "CREATE INDEX ix_attempts_target ON payment_attempts (tenant_id,local_ref,created_at DESC)"
    )
    op.execute("""CREATE UNIQUE INDEX uq_attempts_active ON payment_attempts (tenant_id,local_ref)
        WHERE status IN ('preparing','ready','unknown') AND provider_id IS NOT NULL""")
    op.execute("ALTER TABLE payment_gateways ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_gateways FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_attempts FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON payment_gateways
        USING (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
    op.execute("""CREATE POLICY tenant_isolation ON payment_attempts
        USING (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
    op.execute("GRANT SELECT, INSERT ON payment_gateways TO app_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON payment_attempts TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE payment_attempts")
    op.execute("DROP TABLE payment_gateways")
