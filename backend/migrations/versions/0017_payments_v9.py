"""Payments resilience and storefront UX (review-v9, issue #63).

- Backfill orders.channel for members of pending checkouts (§3): pay_checkout now sets the chosen
  channel on every member order in the same transaction; this aligns older pending checkouts.
- users.announcement_ack: highest announcement version the user acknowledged (§8), so a mandatory
  acknowledgement follows the signed-in user across devices. app_api has table-level UPDATE on
  users (0001); the delivery worker's column grants are unchanged.

Revision ID: 0017_payments_v9
Revises: 0016_user_avatars
"""

from __future__ import annotations

from alembic import op

revision = "0017_payments_v9"
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
    op.execute("ALTER TABLE users ADD COLUMN announcement_ack integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN announcement_ack")
