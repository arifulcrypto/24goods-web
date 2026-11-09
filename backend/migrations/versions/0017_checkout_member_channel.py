"""Backfill orders.channel for members of pending checkouts (review-v9 §3).

pay_checkout now sets the chosen channel on every member order in the same transaction; this
aligns the pending checkouts that were created before that. Data only, no schema change.

Revision ID: 0017_checkout_member_channel
Revises: 0016_user_avatars
"""

from __future__ import annotations

from alembic import op

revision = "0017_checkout_member_channel"
down_revision = "0016_user_avatars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE orders o SET channel = c.channel
        FROM checkouts c
        WHERE o.checkout_id = c.id AND o.tenant_id = c.tenant_id
          AND c.status = 'pending_payment' AND o.status = 'pending_payment'
          AND c.channel IS NOT NULL AND o.channel IS DISTINCT FROM c.channel
        """
    )


def downgrade() -> None:
    pass
