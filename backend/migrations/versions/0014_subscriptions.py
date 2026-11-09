"""Subscription products, month-based after-sales and entitlement -> product link (review-v7).

products.after_sales_months / products.subscription, service_entitlements.product_id,
and the one-month default for tenants still on the old seeded 28-day (672 h) setting.

Grants: products and service_entitlements already carry table-level grants (app_api
SELECT/INSERT/UPDATE, app_delivery SELECT on products), so the new columns need none.

Revision ID: 0014_subscriptions
Revises: 0013_legacy_pay_urls
"""

from __future__ import annotations

from alembic import op

revision = "0014_subscriptions"
down_revision = "0013_legacy_pay_urls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE products ADD COLUMN after_sales_months smallint "
        "CHECK (after_sales_months IS NULL OR after_sales_months BETWEEN 1 AND 12)"
    )
    op.execute("ALTER TABLE products ADD COLUMN subscription jsonb")
    op.execute(
        "ALTER TABLE service_entitlements ADD COLUMN product_id uuid REFERENCES products(id)"
    )
    op.execute(
        "ALTER TABLE service_entitlements ADD COLUMN source varchar(8) NOT NULL "
        "DEFAULT 'manual' CHECK (source IN ('manual','auto'))"
    )
    op.execute(
        "UPDATE service_entitlements s SET product_id = o.product_id FROM orders o "
        "WHERE o.id = s.order_id AND o.tenant_id = s.tenant_id AND s.product_id IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_service_entitlements_buyer_product "
        "ON service_entitlements(tenant_id, buyer_id, product_id)"
    )
    # the old seeded 28-day default becomes the new built-in one calendar month
    op.execute(
        "UPDATE tenants SET settings = settings - 'after_sales_default_hours' "
        "WHERE settings->>'after_sales_default_hours' = '672'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_service_entitlements_buyer_product")
    op.execute("ALTER TABLE service_entitlements DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE service_entitlements DROP COLUMN IF EXISTS product_id")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS subscription")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS after_sales_months")
