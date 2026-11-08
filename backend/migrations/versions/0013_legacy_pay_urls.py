"""Durable record that a legacy environment-key pay URL was issued (issue #32).

Revision ID: 0013_legacy_pay_urls
Revises: 0012_cs_reply_templates
"""

from __future__ import annotations

from alembic import op

revision = "0013_legacy_pay_urls"
down_revision = "0012_cs_reply_templates"
branch_labels = None
depends_on = None
PRED = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# Literal statements, no string building (AGENTS.md §4: no f-string SQL).
BACKFILL = [
    "ALTER TABLE orders NO FORCE ROW LEVEL SECURITY",
    "INSERT INTO legacy_pay_urls (tenant_id, local_ref) "
    "SELECT tenant_id, order_no FROM orders WHERE status = 'pending_payment' "
    "ON CONFLICT DO NOTHING",
    "ALTER TABLE orders FORCE ROW LEVEL SECURITY",
    "ALTER TABLE checkouts NO FORCE ROW LEVEL SECURITY",
    "INSERT INTO legacy_pay_urls (tenant_id, local_ref) "
    "SELECT tenant_id, checkout_no FROM checkouts WHERE status = 'pending_payment' "
    "ON CONFLICT DO NOTHING",
    "ALTER TABLE checkouts FORCE ROW LEVEL SECURITY",
]


def upgrade() -> None:
    op.execute("""CREATE TABLE legacy_pay_urls (
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        local_ref varchar(16) NOT NULL,
        issued_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, local_ref))""")

    # Backfill BEFORE enabling RLS on the new table. Every payable still pending at deploy time
    # may already carry a legacy URL that was never recorded (the old code wrote nothing), so mark
    # them all: the only cost of over-marking is that cancelling one re-queries the legacy gateway
    # (or fails closed while no env key is configured) until it expires. Under-marking would let a
    # cancel proceed over money that may have arrived. orders/checkouts are FORCE RLS, so the
    # owner sees nothing without a pinned tenant; lift FORCE for the read only (as 0003 does) and
    # restore it in the same transaction.
    for statement in BACKFILL:
        op.execute(statement)

    op.execute("ALTER TABLE legacy_pay_urls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE legacy_pay_urls FORCE ROW LEVEL SECURITY")
    scope = f"USING ({PRED}) WITH CHECK ({PRED})"
    op.execute(f"CREATE POLICY tenant_isolation ON legacy_pay_urls {scope}")
    # Append-only for the API: a marker is evidence, so nothing in the app may edit or remove one.
    op.execute("GRANT SELECT, INSERT ON legacy_pay_urls TO app_api")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS legacy_pay_urls")
