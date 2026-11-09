"""EPay gateway routing (review-v10): priority / enabled / health columns and test payments.

- payment_gateways: priority, enabled, last_ok_at, last_fail_at, last_fail_reason. Backfill: the
  gateway a tenant currently has active (settings.payments.epay_provider_id) stays enabled with
  priority 10; every other existing gateway is disabled (it was never used for new payments).
- payment_tests: admin test payments, never linked to an order/checkout.
- app_api may now UPDATE the routing columns of payment_gateways and DELETE a gateway/test rows.

Revision ID: 0018_gateway_routing
Revises: 0017_payments_v9
"""

from __future__ import annotations

from alembic import op

revision = "0018_gateway_routing"
down_revision = "0017_payments_v9"
branch_labels = None
depends_on = None

BACKFILL_SQL = """
    UPDATE payment_gateways g
    SET enabled = (g.id::text = t.settings #>> '{payments,epay_provider_id}'),
        priority = CASE WHEN g.id::text = t.settings #>> '{payments,epay_provider_id}'
                        THEN 10 ELSE 100 END
    FROM tenants t WHERE t.id = g.tenant_id
"""


def upgrade() -> None:
    op.execute("""ALTER TABLE payment_gateways
        ADD COLUMN priority integer NOT NULL DEFAULT 100,
        ADD COLUMN enabled boolean NOT NULL DEFAULT true,
        ADD COLUMN last_ok_at timestamptz, ADD COLUMN last_fail_at timestamptz,
        ADD COLUMN last_fail_reason varchar(32)""")
    # payment_gateways is FORCE RLS: lift it for this cross-tenant statement only
    op.execute("ALTER TABLE payment_gateways NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute(BACKFILL_SQL)
    finally:
        op.execute("ALTER TABLE payment_gateways FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE TABLE payment_tests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
        provider_id uuid NOT NULL, local_ref varchar(32) NOT NULL,
        gateway_ref varchar(32) NOT NULL UNIQUE,
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        status varchar(16) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','paid','expired','failed')),
        created_by uuid NOT NULL REFERENCES users, paid_at timestamptz, trade_no varchar(128),
        data jsonb NOT NULL DEFAULT '{}', expires_at timestamptz, checked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, provider_id) REFERENCES payment_gateways(tenant_id, id))""")
    op.execute("CREATE INDEX ix_payment_tests_tenant ON payment_tests (tenant_id, created_at DESC)")
    op.execute("""CREATE UNIQUE INDEX uq_payment_tests_trade ON payment_tests
        (tenant_id, provider_id, trade_no) WHERE trade_no IS NOT NULL""")
    op.execute("ALTER TABLE payment_tests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_tests FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON payment_tests
        USING (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid)""")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON payment_tests TO app_api")
    op.execute(
        "GRANT UPDATE (priority, enabled, last_ok_at, last_fail_at, last_fail_reason) "
        "ON payment_gateways TO app_api"
    )
    op.execute("GRANT DELETE ON payment_gateways TO app_api")


def downgrade() -> None:
    op.execute("REVOKE DELETE ON payment_gateways FROM app_api")
    op.execute("REVOKE UPDATE ON payment_gateways FROM app_api")
    op.execute("DROP TABLE payment_tests")
    op.execute("""ALTER TABLE payment_gateways DROP COLUMN priority, DROP COLUMN enabled,
        DROP COLUMN last_ok_at, DROP COLUMN last_fail_at, DROP COLUMN last_fail_reason""")
