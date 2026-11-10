"""Chat v11 (review-v11 W2): quotes carry the product variant they were negotiated for.

``quotes.variant_id`` is NULL for products without variants (and all existing quotes). The
quotes table already has RLS + grants; a new column inherits the table-level grants.

Revision ID: 0021_chat_v11
Revises: 0020_product_variants
"""

from __future__ import annotations

from alembic import op

revision = "0021_chat_v11"
down_revision = "0020_product_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE quotes ADD COLUMN variant_id uuid REFERENCES product_variants(id)")


def downgrade() -> None:
    op.execute("ALTER TABLE quotes DROP COLUMN IF EXISTS variant_id")
