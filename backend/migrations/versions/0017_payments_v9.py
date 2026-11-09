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


BACKFILL = [
    # orders/checkouts are FORCE RLS: the owner sees nothing without a pinned tenant, so lift FORCE
    # for this cross-tenant statement only (as 0013/0015 do) and restore it in `finally`.
    "ALTER TABLE orders NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE checkouts NO FORCE ROW LEVEL SECURITY",
]
BACKFILL_SQL = """
    UPDATE orders o SET channel = c.channel
    FROM checkouts c
    WHERE o.checkout_id = c.id AND o.tenant_id = c.tenant_id
      AND c.status = 'pending_payment' AND o.status = 'pending_payment'
      AND c.channel IS NOT NULL AND o.channel IS DISTINCT FROM c.channel
"""
RESTORE = [
    "ALTER TABLE orders FORCE ROW LEVEL SECURITY",
    "ALTER TABLE checkouts FORCE ROW LEVEL SECURITY",
]


def backfill(execute) -> None:  # type: ignore[no-untyped-def]
    """Run the member-channel backfill through ``execute`` (op.execute or a test connection)."""
    for stmt in BACKFILL:
        execute(stmt)
    try:
        execute(BACKFILL_SQL)
    finally:
        for stmt in RESTORE:
            execute(stmt)


def upgrade() -> None:
    backfill(op.execute)
    op.execute("ALTER TABLE users ADD COLUMN announcement_ack integer NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN announcement_ack")
