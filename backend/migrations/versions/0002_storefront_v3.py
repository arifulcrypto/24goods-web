"""Storefront v3: instant accounts, delivery fields, access codes, articles CMS, after-sales,
card batches/expiry/void, file attachments (docs/review-v3.md §9).

Revision ID: 0002_storefront_v3
Revises: 0001_initial
Create Date: 2026-11-05

Hand-written SQL like 0001. Runs as the schema owner.
"""

from __future__ import annotations

from alembic import op

revision = "0002_storefront_v3"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"
TENANT = "tenant_id uuid NOT NULL REFERENCES tenants(id)"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

SCHEMA = [
    # ---- users ----
    "ALTER TABLE users ADD COLUMN is_instant boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN email_verified boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN handle varchar(16)",
    "CREATE UNIQUE INDEX uq_users_tenant_handle ON users (tenant_id, handle) "
    "WHERE handle IS NOT NULL",
    "CREATE INDEX ix_users_instant_purge ON users (created_at) "
    "WHERE is_instant AND status = 'active'",
    # ---- products ----
    "ALTER TABLE products ADD COLUMN delivery_format text",
    "ALTER TABLE products ADD COLUMN delivery_separator varchar(8) NOT NULL DEFAULT '----'",
    "ALTER TABLE products ADD COLUMN redeem_url varchar(500)",
    "ALTER TABLE products ADD COLUMN usage_i18n jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE products ADD COLUMN usage_html_i18n jsonb NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE products ADD COLUMN after_sales_hours integer "
    "CHECK (after_sales_hours IS NULL OR after_sales_hours BETWEEN 0 AND 8760)",
    "ALTER TABLE products ADD COLUMN group_sort integer NOT NULL DEFAULT 0",
    # ---- orders ----
    "ALTER TABLE orders ADD COLUMN access_code_hash varchar(64)",
    "ALTER TABLE orders ADD COLUMN source varchar(32)",
    "ALTER TABLE orders ADD COLUMN after_sales_until timestamptz",
    "ALTER TABLE orders ADD COLUMN auto_confirm_at timestamptz",
    "CREATE INDEX ix_orders_auto_confirm ON orders (auto_confirm_at) WHERE status = 'delivered'",
    # ---- attachments / messages: text files ----
    "ALTER TABLE attachments DROP CONSTRAINT attachments_kind_check",
    "ALTER TABLE attachments ADD CONSTRAINT attachments_kind_check "
    "CHECK (kind IN ('image', 'voice', 'file'))",
    "ALTER TABLE attachments ADD COLUMN name varchar(120)",
    "ALTER TABLE messages DROP CONSTRAINT messages_type_check",
    "ALTER TABLE messages ADD CONSTRAINT messages_type_check CHECK (type IN ('text', 'image', "
    "'voice', 'file', 'product_card', 'quote_card', 'order_card', 'system'))",
    # ---- articles ----
    f"""CREATE TABLE articles (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        slug varchar(64) NOT NULL,
        kind varchar(8) NOT NULL DEFAULT 'article' CHECK (kind IN ('article', 'page')),
        category varchar(32) NOT NULL DEFAULT 'general',
        title_i18n jsonb NOT NULL,
        body_i18n jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        body_html_i18n jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        status varchar(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
        pinned boolean NOT NULL DEFAULT false,
        views integer NOT NULL DEFAULT 0,
        published_at timestamptz,
        created_by uuid REFERENCES users(id),
        created_at {TS},
        updated_at {TS},
        CONSTRAINT uq_articles_tenant_slug UNIQUE (tenant_id, slug))""",
    "CREATE INDEX ix_articles_list ON articles (tenant_id, kind, status, pinned DESC, "
    "published_at DESC)",
    # ---- card batches ----
    f"""CREATE TABLE card_batches (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        product_id uuid NOT NULL REFERENCES products(id),
        note varchar(200),
        count integer NOT NULL DEFAULT 0,
        expires_at timestamptz,
        created_by uuid REFERENCES users(id),
        created_at {TS})""",
    "CREATE INDEX ix_card_batches_product ON card_batches (tenant_id, product_id, created_at DESC)",
    # ---- card_stock ----
    "ALTER TABLE card_stock ADD COLUMN batch_id uuid REFERENCES card_batches(id)",
    "ALTER TABLE card_stock ADD COLUMN lookup_fp varchar(64)",
    "ALTER TABLE card_stock ADD COLUMN expires_at timestamptz",
    "ALTER TABLE card_stock ADD COLUMN void_reason varchar(64)",
    "ALTER TABLE card_stock ADD COLUMN voided_at timestamptz",
    "ALTER TABLE card_stock ADD COLUMN voided_by uuid REFERENCES users(id)",
    "ALTER TABLE card_stock ADD COLUMN replaced_by uuid REFERENCES card_stock(id)",
    "CREATE INDEX ix_card_stock_batch ON card_stock (batch_id) WHERE batch_id IS NOT NULL",
    "CREATE INDEX ix_card_stock_lookup_fp ON card_stock (tenant_id, lookup_fp) "
    "WHERE lookup_fp IS NOT NULL",
    "CREATE INDEX ix_card_stock_expiry ON card_stock (expires_at) "
    "WHERE expires_at IS NOT NULL AND status = 'available'",
    # ---- after-sales ----
    f"""CREATE TABLE after_sales (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        order_id uuid NOT NULL REFERENCES orders(id),
        buyer_id uuid NOT NULL REFERENCES users(id),
        reason varchar(16) NOT NULL CHECK (reason IN ('not_received', 'invalid_code',
            'wrong_item', 'account_issue', 'other')),
        description text NOT NULL CHECK (char_length(description) <= 2000),
        attachment_ids uuid[] NOT NULL DEFAULT '{{}}' CHECK (cardinality(attachment_ids) <= 4),
        status varchar(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'processing',
            'replaced', 'refunded', 'rejected', 'resolved')),
        resolution_note text,
        resolved_by uuid REFERENCES users(id),
        resolved_at timestamptz,
        created_at {TS},
        updated_at {TS})""",
    "CREATE UNIQUE INDEX uq_after_sales_open_order ON after_sales (order_id) "
    "WHERE status IN ('open', 'processing')",
    "CREATE INDEX ix_after_sales_tenant_status ON after_sales (tenant_id, status, created_at DESC)",
    "CREATE INDEX ix_after_sales_order ON after_sales (order_id, created_at DESC)",
    # ---- card status transition guard for app_api ----
    """CREATE FUNCTION card_stock_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
    BEGIN
        IF current_user = 'app_api' THEN
            IF OLD.status NOT IN ('available', 'reserved') OR NEW.status <> 'void' THEN
                RAISE EXCEPTION 'app_api may only void available or reserved cards'
                    USING ERRCODE = '42501';
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'void_reason' - 'voided_at' - 'voided_by'
                    - 'replaced_by')
               IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'void_reason' - 'voided_at'
                    - 'voided_by' - 'replaced_by') THEN
                RAISE EXCEPTION 'app_api may only change void bookkeeping columns'
                    USING ERRCODE = '42501';
            END IF;
        END IF;
        RETURN NEW;
    END $$""",
    "CREATE TRIGGER card_stock_guard BEFORE UPDATE ON card_stock "
    "FOR EACH ROW EXECUTE FUNCTION card_stock_guard()",
]

NEW_RLS_TABLES = ("articles", "after_sales", "card_batches")

GRANTS = [
    # app_api
    "GRANT SELECT, INSERT, UPDATE ON articles, after_sales TO app_api",
    "GRANT DELETE ON articles TO app_api",
    "GRANT SELECT, INSERT ON card_batches TO app_api",
    "GRANT UPDATE (count) ON card_batches TO app_api",
    "GRANT UPDATE (settings) ON tenants TO app_api",
    "GRANT SELECT (batch_id, lookup_fp, expires_at, void_reason, voided_at, voided_by, "
    "replaced_by) ON card_stock TO app_api",
    "GRANT UPDATE (status, void_reason, voided_at, voided_by, replaced_by) ON card_stock "
    "TO app_api",
    "GRANT UPDATE (name) ON attachments TO app_api",
    # app_delivery: claim filter + replacement + after-sales bookkeeping, nothing more
    "GRANT UPDATE (void_reason, voided_at, replaced_by) ON card_stock TO app_delivery",
    "GRANT UPDATE (after_sales_until, auto_confirm_at) ON orders TO app_delivery",
    "GRANT SELECT ON after_sales TO app_delivery",
    "GRANT UPDATE (status, resolution_note, resolved_at, updated_at) ON after_sales "
    "TO app_delivery",
    "GRANT SELECT (id, tenant_id, locale) ON users TO app_delivery",
    # attachment metadata only (file bytes live on disk): after-sales ticket views
    "GRANT SELECT ON attachments TO app_delivery",
]


def upgrade() -> None:
    for stmt in SCHEMA:
        op.execute(stmt)
    for t in NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING ({TENANT_PREDICATE}) "
            f"WITH CHECK ({TENANT_PREDICATE})"
        )
    # tenants carries per-tenant site settings. Reads stay global (Host -> tenant resolution
    # runs before a tenant is known); writes are confined to the pinned tenant's own row.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenants_read ON tenants FOR SELECT USING (true)")
    op.execute(
        "CREATE POLICY tenants_own_update ON tenants FOR UPDATE "
        "USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    for stmt in GRANTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenants_own_update ON tenants")
    op.execute("DROP POLICY IF EXISTS tenants_read ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS card_stock_guard ON card_stock")
    op.execute("DROP FUNCTION IF EXISTS card_stock_guard()")
    op.execute("DROP TABLE IF EXISTS after_sales CASCADE")
    for col in (
        "replaced_by",
        "voided_by",
        "voided_at",
        "void_reason",
        "expires_at",
        "lookup_fp",
        "batch_id",
    ):
        op.execute(f"ALTER TABLE card_stock DROP COLUMN IF EXISTS {col}")
    op.execute("DROP TABLE IF EXISTS card_batches CASCADE")
    op.execute("DROP TABLE IF EXISTS articles CASCADE")
    op.execute("DELETE FROM messages WHERE type = 'file'")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_type_check")
    op.execute(
        "ALTER TABLE messages ADD CONSTRAINT messages_type_check CHECK (type IN ('text', "
        "'image', 'voice', 'product_card', 'quote_card', 'order_card', 'system'))"
    )
    op.execute(
        "DELETE FROM attachments WHERE kind = 'file' AND NOT EXISTS "
        "(SELECT 1 FROM messages m WHERE m.attachment_id = attachments.id)"
    )
    op.execute("ALTER TABLE attachments DROP COLUMN IF EXISTS name")
    op.execute("ALTER TABLE attachments DROP CONSTRAINT IF EXISTS attachments_kind_check")
    op.execute(
        "ALTER TABLE attachments ADD CONSTRAINT attachments_kind_check "
        "CHECK (kind IN ('image', 'voice'))"
    )
    for col in ("auto_confirm_at", "after_sales_until", "source", "access_code_hash"):
        op.execute(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col}")
    for col in (
        "group_sort",
        "after_sales_hours",
        "usage_html_i18n",
        "usage_i18n",
        "redeem_url",
        "delivery_separator",
        "delivery_format",
    ):
        op.execute(f"ALTER TABLE products DROP COLUMN IF EXISTS {col}")
    op.execute("DROP INDEX IF EXISTS uq_users_tenant_handle")
    for col in ("handle", "email_verified", "is_instant"):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {col}")
