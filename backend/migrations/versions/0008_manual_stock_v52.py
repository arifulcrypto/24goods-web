"""v5.2 manual-delivery stock (docs/review-v5.2.md section 2).

Revision ID: 0008_manual_stock_v52
Revises: 0007_reseller_domains_v52
Create Date: 2026-11-07

NULL = unlimited, so every existing listing keeps selling. app_api already holds a
table-level UPDATE on products (0001), so no extra grant is needed.
"""

from __future__ import annotations

from alembic import op

revision = "0008_manual_stock_v52"
down_revision = "0007_reseller_domains_v52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE products ADD COLUMN manual_stock integer")
    op.execute(
        "ALTER TABLE products ADD CONSTRAINT ck_products_manual_stock "
        "CHECK (manual_stock IS NULL OR manual_stock >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS ck_products_manual_stock")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS manual_stock")
