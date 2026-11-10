"""Terminal ``closed`` payment-attempt status and the reconciler's open-set index (issue #72 B3).

``payment_attempts.status`` is an unconstrained varchar, so ``closed`` needs no constraint change.
The partial index matches the reconciler's selection predicate (status in the open set).
Downgrade maps ``closed`` back to ``superseded`` (still a polled, non-terminal value).

Revision ID: 0019_attempt_closed
Revises: 0018_gateway_routing
"""

from __future__ import annotations

from alembic import op

revision = "0019_attempt_closed"
down_revision = "0018_gateway_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE INDEX ix_attempts_open
        ON payment_attempts (tenant_id, checked_at NULLS FIRST, created_at)
        WHERE status IN ('ready','preparing','unknown','superseded')""")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attempts_open")
    op.execute("UPDATE payment_attempts SET status = 'superseded' WHERE status = 'closed'")
