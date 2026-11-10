"""v11 W3: announcements (articles.kind 'notice' + announcement_reads), manual service records,
product/category pinning, generic avatar presets.

- ``articles.kind`` accepts ``notice``; ``announcement_reads (user_id, article_id)`` PK with RLS
  (ENABLE + FORCE + ``tenant_isolation``) and app_api SELECT/INSERT/DELETE.
- ``service_entitlements``: ``buyer_id`` / ``order_id`` become NULLable (a record may stand alone),
  new ``customer_label``, ``contact``, ``notes``, ``reminder_lead_days`` and a human ``ref``
  (unique per tenant, backfilled for existing rows). ``service_renewals.order_id`` NULLable +
  ``note`` (manual renewal). Both tables keep their table-level grants (new columns inherit).
- ``products.pinned_at`` / ``categories.pinned_at`` (NULL = not pinned); products and categories
  already carry table-level grants for app_api.
- ``users.avatar_preset``: app_api holds table-level SELECT/UPDATE on users (0001), so the new
  column inherits it; the delivery worker's column grants are unchanged.

Revision ID: 0022_v11_content_ops
Revises: 0021_chat_v11
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import op
from sqlalchemy import text

revision = "0022_v11_content_ops"
down_revision = "0021_chat_v11"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


# Tables with FORCE ROW LEVEL SECURITY whose rows this migration reads or rewrites. FORCE applies
# RLS to the owner (the migrate role) too, so without a pinned tenant a backfill SELECT sees no
# rows (production 2026-11-10: the existing service record kept a NULL ref and SET NOT NULL
# failed). The data steps run with FORCE lifted and restored in the same transaction.
_DATA_TABLES = ("service_entitlements", "service_renewals", "service_reminders", "articles")


@contextmanager
def _owner_sees_all_rows() -> Iterator[None]:
    for t in _DATA_TABLES:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
    yield
    for t in _DATA_TABLES:
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")


def _ref() -> str:
    return "SV-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))


def upgrade() -> None:
    # ---- announcements
    op.execute("ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_kind_check")
    op.execute(
        "ALTER TABLE articles ADD CONSTRAINT articles_kind_check "
        "CHECK (kind IN ('article', 'page', 'notice'))"
    )
    op.execute("""CREATE TABLE announcement_reads (
        tenant_id uuid NOT NULL REFERENCES tenants(id),
        user_id uuid NOT NULL REFERENCES users(id),
        article_id uuid NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        read_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, article_id))""")
    op.execute("CREATE INDEX ix_announcement_reads_article ON announcement_reads (article_id)")
    op.execute("ALTER TABLE announcement_reads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE announcement_reads FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON announcement_reads USING ({TENANT_PREDICATE}) "
        f"WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON announcement_reads TO app_api")

    # ---- manual service records
    op.execute("ALTER TABLE service_entitlements ALTER COLUMN buyer_id DROP NOT NULL")
    op.execute("ALTER TABLE service_entitlements ALTER COLUMN order_id DROP NOT NULL")
    op.execute("ALTER TABLE service_entitlements ADD COLUMN customer_label varchar(120)")
    op.execute("ALTER TABLE service_entitlements ADD COLUMN contact varchar(200)")
    op.execute("ALTER TABLE service_entitlements ADD COLUMN notes text")
    op.execute(
        "ALTER TABLE service_entitlements ADD COLUMN reminder_lead_days smallint "
        "CHECK (reminder_lead_days IS NULL OR reminder_lead_days BETWEEN 1 AND 60)"
    )
    op.execute("ALTER TABLE service_entitlements ADD COLUMN ref varchar(12)")
    conn = op.get_bind()
    with _owner_sees_all_rows():
        rows = conn.execute(text("SELECT id, tenant_id FROM service_entitlements")).all()
        used: set[tuple[object, str]] = set()
        for ident, tenant in rows:
            ref = _ref()
            while (tenant, ref) in used:
                ref = _ref()
            used.add((tenant, ref))
            conn.execute(
                text("UPDATE service_entitlements SET ref = :r WHERE id = :i"),
                {"r": ref, "i": ident},
            )
    op.execute("ALTER TABLE service_entitlements ALTER COLUMN ref SET NOT NULL")
    op.execute(
        "ALTER TABLE service_entitlements ADD CONSTRAINT uq_service_ref UNIQUE (tenant_id, ref)"
    )
    op.execute("ALTER TABLE service_renewals ALTER COLUMN order_id DROP NOT NULL")
    op.execute("ALTER TABLE service_renewals ADD COLUMN note varchar(200)")

    # ---- pin & sort
    op.execute("ALTER TABLE products ADD COLUMN pinned_at timestamptz")
    op.execute("ALTER TABLE categories ADD COLUMN pinned_at timestamptz")

    # ---- avatars
    op.execute("ALTER TABLE users ADD COLUMN avatar_preset varchar(16)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_preset")
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS pinned_at")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS pinned_at")
    op.execute("ALTER TABLE service_renewals DROP COLUMN IF EXISTS note")
    # standalone records/renewals cannot satisfy the old NOT NULLs
    with _owner_sees_all_rows():
        op.execute(
            "DELETE FROM service_reminders WHERE entitlement_id IN "
            "(SELECT id FROM service_entitlements WHERE order_id IS NULL)"
        )
        op.execute("DELETE FROM service_renewals WHERE order_id IS NULL")
        op.execute(
            "DELETE FROM service_renewals WHERE entitlement_id IN "
            "(SELECT id FROM service_entitlements WHERE order_id IS NULL OR buyer_id IS NULL)"
        )
        op.execute(
            "DELETE FROM service_reminders WHERE entitlement_id IN "
            "(SELECT id FROM service_entitlements WHERE buyer_id IS NULL)"
        )
        op.execute("DELETE FROM service_entitlements WHERE order_id IS NULL OR buyer_id IS NULL")
    op.execute("ALTER TABLE service_renewals ALTER COLUMN order_id SET NOT NULL")
    op.execute("ALTER TABLE service_entitlements DROP CONSTRAINT IF EXISTS uq_service_ref")
    for col in ("ref", "reminder_lead_days", "notes", "contact", "customer_label"):
        op.execute(f"ALTER TABLE service_entitlements DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE service_entitlements ALTER COLUMN order_id SET NOT NULL")
    op.execute("ALTER TABLE service_entitlements ALTER COLUMN buyer_id SET NOT NULL")
    op.execute("DROP TABLE IF EXISTS announcement_reads")
    with _owner_sees_all_rows():
        op.execute("DELETE FROM articles WHERE kind = 'notice'")
    op.execute("ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_kind_check")
    op.execute(
        "ALTER TABLE articles ADD CONSTRAINT articles_kind_check "
        "CHECK (kind IN ('article', 'page'))"
    )
