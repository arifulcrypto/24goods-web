"""Product variants / SKUs (review-v11 W1).

- ``product_variants``: per-variant price, floor, manual stock (NULL = unlimited), optional
  after-sales / subscription terms (NULL inherits the product's), sort, status. RLS + FORCE +
  ``tenant_isolation`` like every tenant table.
- ``card_stock.variant_id`` (NULL = product-level stock), ``orders.variant_id`` +
  ``orders.variant_name`` snapshot.

Grants: app_api gets full CRUD on product_variants and SELECT on the new ``card_stock.variant_id``
column (card_stock has column-level SELECT; INSERT is table-level so the import can set it).
The delivery worker already holds table-level SELECT on card_stock and orders, so its claim
filter by variant needs nothing new; it never reads product_variants (terms are snapshotted
on the order).

Revision ID: 0020_product_variants
Revises: 0019_attempt_closed
"""

from __future__ import annotations

from alembic import op

revision = "0020_product_variants"
down_revision = "0019_attempt_closed"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("""CREATE TABLE product_variants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        product_id uuid NOT NULL REFERENCES products(id),
        name_i18n jsonb NOT NULL,
        price_minor bigint NOT NULL CHECK (price_minor > 0),
        floor_price_minor bigint NOT NULL DEFAULT 0 CHECK (floor_price_minor >= 0),
        manual_stock integer CHECK (manual_stock IS NULL OR manual_stock >= 0),
        sort integer NOT NULL DEFAULT 0,
        status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
        after_sales_months smallint,
        after_sales_hours integer,
        subscription jsonb,
        sold integer NOT NULL DEFAULT 0 CHECK (sold >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())""")
    op.execute(
        "CREATE INDEX ix_product_variants_product ON product_variants "
        "(tenant_id, product_id, status, sort)"
    )
    op.execute("ALTER TABLE product_variants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE product_variants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON product_variants USING ({TENANT_PREDICATE}) "
        f"WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON product_variants TO app_api")

    op.execute("ALTER TABLE card_stock ADD COLUMN variant_id uuid REFERENCES product_variants(id)")
    op.execute(
        "CREATE INDEX ix_card_stock_variant_status ON card_stock (tenant_id, variant_id, status) "
        "WHERE variant_id IS NOT NULL"
    )
    op.execute("GRANT SELECT (variant_id) ON card_stock TO app_api")
    op.execute("ALTER TABLE orders ADD COLUMN variant_id uuid REFERENCES product_variants(id)")
    op.execute("ALTER TABLE orders ADD COLUMN variant_name varchar(120)")
    op.execute(
        "CREATE INDEX ix_orders_variant ON orders (tenant_id, variant_id) "
        "WHERE variant_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orders_variant")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS variant_name")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS variant_id")
    op.execute("DROP INDEX IF EXISTS ix_card_stock_variant_status")
    op.execute("ALTER TABLE card_stock DROP COLUMN IF EXISTS variant_id")
    op.execute("DROP TABLE IF EXISTS product_variants")
