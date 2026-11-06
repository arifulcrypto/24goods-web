"""v5 growth: categories table, product archive/import fields, promo codes, resellers
(storefront subdomains, markup, sign-up fee, payouts), encrypted tenant secrets, Google
login (docs/review-v5.md §10).

Revision ID: 0004_v5_growth
Revises: 0003_commerce_v4
Create Date: 2026-11-06

Hand-written SQL like 0001-0003. Runs as the schema owner.
"""

from __future__ import annotations

from alembic import op

revision = "0004_v5_growth"
down_revision = "0003_commerce_v4"
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"
TENANT = "tenant_id uuid NOT NULL REFERENCES tenants(id)"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

SCHEMA = [
    # ---- categories (was a hard-coded dict in app/modules/catalog/categories.py) ----
    f"""CREATE TABLE categories (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        slug varchar(32) NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9_-]{{0,31}}$'),
        name_i18n jsonb NOT NULL,
        icon varchar(32),
        sort integer NOT NULL DEFAULT 0,
        enabled boolean NOT NULL DEFAULT true,
        created_at {TS},
        updated_at {TS},
        CONSTRAINT uq_categories_tenant_slug UNIQUE (tenant_id, slug))""",
    # ---- products ----
    "ALTER TABLE products DROP CONSTRAINT IF EXISTS products_status_check",
    "ALTER TABLE products ADD CONSTRAINT products_status_check "
    "CHECK (status IN ('active', 'draft', 'archived'))",
    # NULL = the tenant default (settings.delivery.manual_eta_hours, 24)
    "ALTER TABLE products ADD COLUMN delivery_eta_hours integer "
    "CHECK (delivery_eta_hours IS NULL OR delivery_eta_hours BETWEEN 1 AND 720)",
    # import / middleman fields: staff-only, never in buyer responses
    "ALTER TABLE products ADD COLUMN source_url varchar(1000)",
    "ALTER TABLE products ADD COLUMN cost_minor bigint "
    "CHECK (cost_minor IS NULL OR cost_minor >= 0)",
    "ALTER TABLE products ADD COLUMN archived_at timestamptz",
    # ---- encrypted tenant secrets (AES-256-GCM, key APP_SETTINGS_KEY, api only) ----
    f"""CREATE TABLE tenant_secrets (
        {TENANT},
        name varchar(64) NOT NULL,
        ciphertext bytea NOT NULL,
        last4 varchar(4),
        updated_by uuid REFERENCES users(id),
        updated_at {TS},
        PRIMARY KEY (tenant_id, name))""",
    # ---- users: Google sign-in ----
    "ALTER TABLE users ADD COLUMN google_sub varchar(64)",
    "CREATE UNIQUE INDEX uq_users_tenant_google_sub ON users (tenant_id, google_sub) "
    "WHERE google_sub IS NOT NULL",
    # ---- promo codes ----
    f"""CREATE TABLE promo_codes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        code varchar(32) NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{{2,31}}$'),
        kind varchar(8) NOT NULL CHECK (kind IN ('percent', 'fixed')),
        -- percent: permille off (1..1000); fixed: minor units off
        value bigint NOT NULL CHECK (value > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        min_subtotal_minor bigint NOT NULL DEFAULT 0 CHECK (min_subtotal_minor >= 0),
        max_discount_minor bigint CHECK (max_discount_minor IS NULL OR max_discount_minor > 0),
        starts_at timestamptz,
        ends_at timestamptz,
        usage_limit integer CHECK (usage_limit IS NULL OR usage_limit > 0),
        per_user_limit integer NOT NULL DEFAULT 1 CHECK (per_user_limit BETWEEN 1 AND 1000),
        used_count integer NOT NULL DEFAULT 0 CHECK (used_count >= 0),
        product_ids uuid[] NOT NULL DEFAULT '{{}}',
        category_slugs text[] NOT NULL DEFAULT '{{}}',
        enabled boolean NOT NULL DEFAULT true,
        note varchar(200),
        created_by uuid REFERENCES users(id),
        created_at {TS},
        updated_at {TS},
        CONSTRAINT uq_promo_codes_tenant_code UNIQUE (tenant_id, code),
        CONSTRAINT ck_promo_percent CHECK (kind <> 'percent' OR value <= 1000),
        CONSTRAINT ck_promo_window CHECK (starts_at IS NULL OR ends_at IS NULL
                                          OR starts_at < ends_at))""",
    # one row per order or checkout that used a code; 'reserved' counts toward the limits
    f"""CREATE TABLE promo_redemptions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        promo_code_id uuid NOT NULL REFERENCES promo_codes(id),
        user_id uuid NOT NULL REFERENCES users(id),
        order_id uuid UNIQUE REFERENCES orders(id),
        checkout_id uuid UNIQUE REFERENCES checkouts(id),
        discount_minor bigint NOT NULL CHECK (discount_minor > 0),
        status varchar(16) NOT NULL DEFAULT 'reserved'
            CHECK (status IN ('reserved', 'used', 'released')),
        created_at {TS},
        updated_at {TS},
        CONSTRAINT ck_promo_redemptions_target CHECK (num_nonnulls(order_id, checkout_id) = 1))""",
    "CREATE INDEX ix_promo_redemptions_code_user ON promo_redemptions "
    "(promo_code_id, user_id) WHERE status <> 'released'",
    # ---- resellers ----
    f"""CREATE TABLE resellers (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        user_id uuid NOT NULL UNIQUE REFERENCES users(id),
        slug varchar(32) NOT NULL UNIQUE
            CHECK (slug ~ '^[a-z0-9]([a-z0-9-]{{1,30}}[a-z0-9])$'),
        store_name varchar(64) NOT NULL,
        logo_url varchar(500),
        welcome_i18n jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        contact varchar(200),
        status varchar(16) NOT NULL DEFAULT 'pending_payment'
            CHECK (status IN ('pending_payment', 'active', 'suspended')),
        markup_kind varchar(8) NOT NULL DEFAULT 'percent'
            CHECK (markup_kind IN ('percent', 'fixed')),
        -- percent: permille (100 = 10.0%); fixed: minor units added per unit
        markup_value bigint NOT NULL DEFAULT 100 CHECK (markup_value >= 0),
        payout_account jsonb,
        signup_order_id uuid,
        activated_at timestamptz,
        created_at {TS},
        updated_at {TS})""",
    "CREATE INDEX ix_resellers_tenant_status ON resellers (tenant_id, status)",
    f"""CREATE TABLE reseller_prices (
        {TENANT},
        reseller_id uuid NOT NULL REFERENCES resellers(id),
        product_id uuid NOT NULL REFERENCES products(id),
        markup_kind varchar(8) NOT NULL CHECK (markup_kind IN ('percent', 'fixed')),
        markup_value bigint NOT NULL CHECK (markup_value >= 0),
        hidden boolean NOT NULL DEFAULT false,
        updated_at {TS},
        PRIMARY KEY (reseller_id, product_id))""",
    f"""CREATE TABLE reseller_payouts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        reseller_id uuid NOT NULL REFERENCES resellers(id),
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        status varchar(16) NOT NULL DEFAULT 'requested'
            CHECK (status IN ('requested', 'paid', 'rejected')),
        account_snapshot jsonb,
        note varchar(200),
        decided_by uuid REFERENCES users(id),
        decided_at timestamptz,
        created_at {TS},
        updated_at {TS})""",
    "CREATE INDEX ix_reseller_payouts_reseller ON reseller_payouts (reseller_id, created_at DESC)",
    # ---- orders: kind, promo discount, reseller attribution ----
    "ALTER TABLE orders ADD COLUMN kind varchar(24) NOT NULL DEFAULT 'goods' "
    "CHECK (kind IN ('goods', 'reseller_signup'))",
    "ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_delivery_mode_check",
    "ALTER TABLE orders ADD CONSTRAINT orders_delivery_mode_check "
    "CHECK (delivery_mode IN ('auto_card', 'manual', 'system'))",
    "ALTER TABLE orders ADD COLUMN discount_minor bigint NOT NULL DEFAULT 0 "
    "CHECK (discount_minor >= 0)",
    "ALTER TABLE orders ADD COLUMN promo_code_id uuid REFERENCES promo_codes(id)",
    "ALTER TABLE orders ADD COLUMN reseller_id uuid REFERENCES resellers(id)",
    "ALTER TABLE orders ADD COLUMN reseller_markup_minor bigint NOT NULL DEFAULT 0 "
    "CHECK (reseller_markup_minor >= 0)",
    "CREATE INDEX ix_orders_reseller ON orders (reseller_id, created_at DESC) "
    "WHERE reseller_id IS NOT NULL",
    # amount (what the gateway charges) = subtotal - discount + fee
    "ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_amount_split",
    "ALTER TABLE orders ADD CONSTRAINT ck_orders_amount_split "
    "CHECK (amount_minor = subtotal_minor - discount_minor + fee_minor)",
    "ALTER TABLE orders ADD CONSTRAINT ck_orders_discount_le_subtotal "
    "CHECK (discount_minor < subtotal_minor)",
    "ALTER TABLE orders ADD CONSTRAINT ck_orders_markup_le_subtotal "
    "CHECK (reseller_markup_minor <= subtotal_minor)",
    """CREATE OR REPLACE FUNCTION orders_subtotal_default() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.subtotal_minor IS NULL THEN
            NEW.subtotal_minor := NEW.amount_minor - COALESCE(NEW.fee_minor, 0)
                                  + COALESCE(NEW.discount_minor, 0);
        END IF;
        RETURN NEW;
    END $$""",
    "ALTER TABLE checkouts ADD COLUMN reseller_id uuid REFERENCES resellers(id)",
    "ALTER TABLE checkouts ADD COLUMN promo_code_id uuid REFERENCES promo_codes(id)",
    # ---- conversations: which storefront the buyer came from (CS context) ----
    "ALTER TABLE conversations ADD COLUMN reseller_id uuid REFERENCES resellers(id)",
    # ---- audit log filters ----
    "CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_action ON audit_log "
    "(tenant_id, action, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_tenant_actor ON audit_log "
    "(tenant_id, actor_id, created_at DESC)",
]

# categories seed per tenant (same slugs/names as the old hard-coded dict). tenants is not
# RLS-forced; categories is created without RLS and locked down after the seed.
SEED = [
    """INSERT INTO categories (tenant_id, slug, name_i18n, sort)
    SELECT t.id, c.slug, c.name::jsonb, c.sort
    FROM tenants t CROSS JOIN (VALUES
        ('game_topup', '{"en": "Game top-up", "zh-CN": "游戏充值"}', 10),
        ('gift_cards', '{"en": "Gift cards", "zh-CN": "礼品卡"}', 20),
        ('software_keys', '{"en": "Software keys", "zh-CN": "软件激活码"}', 30),
        ('design_services', '{"en": "Design & services", "zh-CN": "设计与服务"}', 40),
        ('accounts', '{"en": "Accounts", "zh-CN": "账号"}', 50),
        ('ai_tools', '{"en": "AI tools", "zh-CN": "AI 工具"}', 60)
    ) AS c(slug, name, sort)
    ON CONFLICT DO NOTHING""",
]

NEW_RLS_TABLES = (
    "categories",
    "tenant_secrets",
    "promo_codes",
    "promo_redemptions",
    "resellers",
    "reseller_prices",
    "reseller_payouts",
)

GRANTS = [
    "GRANT SELECT, INSERT, UPDATE, DELETE ON categories TO app_api",
    "GRANT SELECT ON categories TO app_delivery",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_secrets TO app_api",
    "GRANT SELECT, INSERT, UPDATE ON promo_codes, promo_redemptions TO app_api",
    "GRANT SELECT, INSERT, UPDATE ON resellers, reseller_payouts TO app_api",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON reseller_prices TO app_api",
    "GRANT SELECT ON resellers TO app_delivery",
    # hard delete of never-sold products (review-v5 §2)
    "GRANT DELETE ON products TO app_api",
]


def upgrade() -> None:
    for stmt in SCHEMA:
        op.execute(stmt)
    for stmt in SEED:
        op.execute(stmt)
    for t in NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING ({TENANT_PREDICATE}) "
            f"WITH CHECK ({TENANT_PREDICATE})"
        )
    for stmt in GRANTS:
        op.execute(stmt)


def downgrade() -> None:
    # Refuse when money-relevant v5 rows exist (orders with a discount, reseller attribution
    # or a sign-up order). FORCE RLS filters a COUNT, so lift it like the 0003 backfill.
    op.execute("ALTER TABLE orders NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM orders WHERE discount_minor > 0 OR reseller_id IS NOT NULL
                   OR kind <> 'goods') THEN
            RAISE EXCEPTION 'refusing to downgrade 0004: v5 money rows exist';
        END IF; END $$"""
    )
    op.execute("ALTER TABLE orders FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE DELETE ON products FROM app_api")
    for idx in ("ix_audit_log_tenant_action", "ix_audit_log_tenant_actor"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS reseller_id")
    op.execute("ALTER TABLE checkouts DROP COLUMN IF EXISTS promo_code_id")
    op.execute("ALTER TABLE checkouts DROP COLUMN IF EXISTS reseller_id")
    op.execute(
        """CREATE OR REPLACE FUNCTION orders_subtotal_default() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.subtotal_minor IS NULL THEN
                NEW.subtotal_minor := NEW.amount_minor - COALESCE(NEW.fee_minor, 0);
            END IF;
            RETURN NEW;
        END $$"""
    )
    for c in (
        "ck_orders_markup_le_subtotal",
        "ck_orders_discount_le_subtotal",
        "ck_orders_amount_split",
    ):
        op.execute(f"ALTER TABLE orders DROP CONSTRAINT IF EXISTS {c}")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_amount_split "
        "CHECK (amount_minor = subtotal_minor + fee_minor)"
    )
    op.execute("DROP INDEX IF EXISTS ix_orders_reseller")
    for col in ("reseller_markup_minor", "reseller_id", "promo_code_id", "discount_minor"):
        op.execute(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_delivery_mode_check")
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT orders_delivery_mode_check "
        "CHECK (delivery_mode IN ('auto_card', 'manual'))"
    )
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS kind")
    for t in (
        "reseller_payouts",
        "reseller_prices",
        "resellers",
        "promo_redemptions",
        "promo_codes",
        "tenant_secrets",
        "categories",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("DROP INDEX IF EXISTS uq_users_tenant_google_sub")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS google_sub")
    for col in ("archived_at", "cost_minor", "source_url", "delivery_eta_hours"):
        op.execute(f"ALTER TABLE products DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_status_check")
    op.execute(
        "ALTER TABLE products ADD CONSTRAINT products_status_check "
        "CHECK (status IN ('active', 'draft'))"
    )
