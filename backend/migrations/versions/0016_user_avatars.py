"""users.avatar_key: storage key of the user's re-encoded avatar (review-v8b, issue #58 §5).

app_api already has table-level UPDATE on users (0001); the delivery worker's column grants
(id, tenant_id, locale) are unchanged, so it never sees the new column.

Revision ID: 0016_user_avatars
Revises: 0015_order_integrity
"""

from __future__ import annotations

from alembic import op

revision = "0016_user_avatars"
down_revision = "0015_order_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN avatar_key varchar(32)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN avatar_key")
