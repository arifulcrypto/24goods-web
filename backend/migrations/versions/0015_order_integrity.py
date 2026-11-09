"""Order/stock/refund/subscription integrity (review-v8, issue #59).

- orders.stock_reserved: manual stock actually reserved at creation (G); release gives back
  exactly this and zeroes it. Backfill: pending manual goods orders of products that currently
  have a finite stock keep their quantity (equivalent to the old behaviour).
- orders.terms jsonb: after-sales/subscription terms at purchase time (F). NULL = legacy order.
- service_renewals.reversed_at / service_entitlements.source_reversed_at: refund reversal of
  subscription periods (C).

Grants: the delivery worker only reads ``orders.terms`` (it already has table-level SELECT on
orders); app_api gets UPDATE (reversed_at) only on service_renewals (previously insert-only);
RLS + FORCE on service_renewals come from 0011 (tests/test_integrity_v8.py asserts it).

Revision ID: 0015_order_integrity
Revises: 0014_subscriptions
"""

from __future__ import annotations

from alembic import op

revision = "0015_order_integrity"
down_revision = "0014_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders ADD COLUMN stock_reserved integer NOT NULL DEFAULT 0 "
        "CHECK (stock_reserved >= 0)"
    )
    op.execute("ALTER TABLE orders ADD COLUMN terms jsonb")
    op.execute("ALTER TABLE service_renewals ADD COLUMN reversed_at timestamptz")
    op.execute("ALTER TABLE service_entitlements ADD COLUMN source_reversed_at timestamptz")
    op.execute("GRANT UPDATE (reversed_at) ON service_renewals TO app_api")
    # cross-tenant backfill: lift FORCE RLS for the statement (as 0013/0014)
    op.execute("ALTER TABLE orders NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE orders o SET stock_reserved = o.qty FROM products p "
        "WHERE p.id = o.product_id AND p.tenant_id = o.tenant_id "
        "AND o.status = 'pending_payment' AND o.kind = 'goods' "
        "AND o.delivery_mode = 'manual' AND p.manual_stock IS NOT NULL"
    )
    op.execute("ALTER TABLE orders FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE service_entitlements DROP COLUMN IF EXISTS source_reversed_at")
    op.execute("ALTER TABLE service_renewals DROP COLUMN IF EXISTS reversed_at")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS terms")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS stock_reserved")
