"""Initial schema: tables, indexes, pg_trgm, RLS policies, DB roles and grants.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-05

Runs as the schema owner (``app_owner``, needs CREATEROLE). Creates/updates the login
roles ``app_api`` and ``app_delivery`` with passwords from ``APP_DB_API_PASSWORD`` and
``APP_DB_DELIVERY_PASSWORD``. All SQL below is static; passwords are passed as bound
parameters and quoted server-side with ``format(%L)``.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"
TENANT = "tenant_id uuid NOT NULL REFERENCES tenants(id)"

TABLES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    f"""CREATE TABLE tenants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug varchar(64) NOT NULL UNIQUE,
        name text NOT NULL,
        kind varchar(16) NOT NULL DEFAULT 'platform'
            CHECK (kind IN ('platform', 'supplier', 'reseller', 'merchant')),
        status varchar(16) NOT NULL DEFAULT 'active',
        settings jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        created_at {TS})""",
    f"""CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        email varchar(254) NOT NULL CHECK (email = lower(email)),
        pw_hash text NOT NULL,
        role varchar(16) NOT NULL DEFAULT 'buyer' CHECK (role IN ('buyer', 'agent', 'admin')),
        display_name varchar(64) NOT NULL,
        locale varchar(8) NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'zh-CN')),
        status varchar(16) NOT NULL DEFAULT 'active',
        last_login_at timestamptz,
        created_at {TS},
        CONSTRAINT uq_users_tenant_email UNIQUE (tenant_id, email))""",
    f"""CREATE TABLE agent_status (
        user_id uuid PRIMARY KEY REFERENCES users(id),
        {TENANT},
        status varchar(16) NOT NULL DEFAULT 'offline'
            CHECK (status IN ('online', 'away', 'on_hold', 'offline')),
        message varchar(200),
        updated_at {TS})""",
    f"""CREATE TABLE products (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        title_i18n jsonb NOT NULL,
        description_i18n jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        images text[] NOT NULL DEFAULT '{{}}',
        price_minor bigint NOT NULL CHECK (price_minor > 0),
        floor_price_minor bigint NOT NULL DEFAULT 0,
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        delivery_mode varchar(16) NOT NULL CHECK (delivery_mode IN ('auto_card', 'manual')),
        category varchar(32) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draft')),
        sold integer NOT NULL DEFAULT 0 CHECK (sold >= 0),
        created_by uuid REFERENCES users(id),
        created_at {TS},
        updated_at {TS},
        search_text text GENERATED ALWAYS AS (lower(
            coalesce(title_i18n->>'en', '') || ' ' || coalesce(title_i18n->>'zh-CN', '') || ' ' ||
            coalesce(description_i18n->>'en', '') || ' ' ||
            coalesce(description_i18n->>'zh-CN', ''))) STORED,
        search_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig,
            coalesce(title_i18n->>'en', '') || ' ' || coalesce(title_i18n->>'zh-CN', '') || ' ' ||
            coalesce(description_i18n->>'en', '') || ' ' ||
            coalesce(description_i18n->>'zh-CN', ''))) STORED,
        CONSTRAINT ck_products_floor CHECK (
            floor_price_minor >= 0 AND floor_price_minor <= price_minor))""",
    "CREATE INDEX ix_products_tenant_status_category ON products (tenant_id, status, category)",
    "CREATE INDEX ix_products_search_tsv ON products USING gin (search_tsv)",
    "CREATE INDEX ix_products_search_trgm ON products USING gin (search_text gin_trgm_ops)",
    f"""CREATE TABLE attachments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        owner_id uuid REFERENCES users(id),
        kind varchar(16) NOT NULL CHECK (kind IN ('image', 'voice')),
        purpose varchar(16) NOT NULL DEFAULT 'chat'
            CHECK (purpose IN ('chat', 'product', 'delivery')),
        is_public boolean NOT NULL DEFAULT false,
        mime varchar(64) NOT NULL,
        bytes integer NOT NULL CHECK (bytes > 0),
        width integer,
        height integer,
        duration_ms integer CHECK (duration_ms IS NULL OR duration_ms BETWEEN 1 AND 60000),
        storage_key varchar(64) NOT NULL UNIQUE,
        created_at {TS})""",
    f"""CREATE TABLE conversations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        buyer_id uuid NOT NULL REFERENCES users(id),
        product_id uuid REFERENCES products(id),
        assigned_agent_id uuid REFERENCES users(id),
        status varchar(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
        last_message_id bigint,
        buyer_last_read_id bigint NOT NULL DEFAULT 0,
        staff_last_read_id bigint NOT NULL DEFAULT 0,
        created_at {TS},
        updated_at {TS})""",
    """CREATE UNIQUE INDEX uq_conversations_open_buyer ON conversations (tenant_id, buyer_id)
        WHERE status = 'open'""",
    "CREATE INDEX ix_conversations_tenant_updated ON conversations (tenant_id, updated_at DESC)",
    f"""CREATE TABLE messages (
        id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        {TENANT},
        conversation_id uuid NOT NULL REFERENCES conversations(id),
        sender_id uuid REFERENCES users(id),
        sender_role varchar(16) NOT NULL CHECK (sender_role IN ('buyer', 'agent', 'system')),
        type varchar(16) NOT NULL CHECK (type IN ('text', 'image', 'voice', 'product_card',
            'quote_card', 'order_card', 'system')),
        text text CHECK (text IS NULL OR char_length(text) <= 2000),
        attachment_id uuid REFERENCES attachments(id),
        payload jsonb,
        client_msg_id varchar(64),
        created_at {TS},
        CONSTRAINT uq_messages_sender_client_msg UNIQUE (sender_id, client_msg_id))""",
    "CREATE INDEX ix_messages_conversation_id ON messages (conversation_id, id)",
    "CREATE INDEX ix_messages_attachment ON messages (attachment_id) "
    "WHERE attachment_id IS NOT NULL",
    f"""CREATE TABLE quotes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        conversation_id uuid NOT NULL REFERENCES conversations(id),
        product_id uuid REFERENCES products(id),
        created_by uuid NOT NULL REFERENCES users(id),
        message_id bigint,
        title varchar(200) NOT NULL,
        note text,
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        qty integer NOT NULL DEFAULT 1 CHECK (qty BETWEEN 1 AND 100),
        status varchar(16) NOT NULL DEFAULT 'offered'
            CHECK (status IN ('offered', 'accepted', 'withdrawn', 'expired')),
        expires_at timestamptz NOT NULL,
        version integer NOT NULL DEFAULT 1,
        signature varchar(64) NOT NULL,
        order_id uuid,
        created_at {TS},
        updated_at {TS})""",
    "CREATE INDEX ix_quotes_status_expires ON quotes (status, expires_at)",
    f"""CREATE TABLE orders (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        order_no varchar(16) NOT NULL UNIQUE,
        buyer_id uuid NOT NULL REFERENCES users(id),
        product_id uuid REFERENCES products(id),
        quote_id uuid UNIQUE REFERENCES quotes(id),
        conversation_id uuid REFERENCES conversations(id),
        title varchar(200) NOT NULL,
        qty integer NOT NULL DEFAULT 1 CHECK (qty BETWEEN 1 AND 100),
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        status varchar(16) NOT NULL DEFAULT 'pending_payment' CHECK (status IN (
            'pending_payment', 'paid', 'delivered', 'completed', 'cancelled', 'expired',
            'refunded', 'manual_review')),
        version integer NOT NULL DEFAULT 1,
        delivery_mode varchar(16) NOT NULL CHECK (delivery_mode IN ('auto_card', 'manual')),
        channel varchar(32),
        expires_at timestamptz,
        paid_at timestamptz,
        delivered_at timestamptz,
        completed_at timestamptz,
        manual_delivery_text text,
        manual_delivery_attachment_id uuid REFERENCES attachments(id),
        created_at {TS},
        updated_at {TS})""",
    "CREATE INDEX ix_orders_tenant_buyer_created ON orders (tenant_id, buyer_id, created_at DESC)",
    "CREATE INDEX ix_orders_tenant_status ON orders (tenant_id, status, created_at DESC)",
    "CREATE INDEX ix_orders_pending_expiry ON orders (expires_at) WHERE status = 'pending_payment'",
    """ALTER TABLE quotes ADD CONSTRAINT fk_quotes_order_id_orders
        FOREIGN KEY (order_id) REFERENCES orders(id)""",
    f"""CREATE TABLE order_events (
        id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        {TENANT},
        order_id uuid NOT NULL REFERENCES orders(id),
        from_status varchar(16),
        to_status varchar(16) NOT NULL,
        actor_id uuid REFERENCES users(id),
        actor_kind varchar(16) NOT NULL DEFAULT 'user',
        reason text,
        created_at {TS})""",
    "CREATE INDEX ix_order_events_order ON order_events (order_id, id)",
    f"""CREATE TABLE payments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        order_id uuid NOT NULL REFERENCES orders(id),
        channel varchar(32) NOT NULL,
        gateway_trade_no varchar(64) NOT NULL,
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'succeeded',
        raw jsonb NOT NULL,
        created_at {TS},
        CONSTRAINT uq_payments_channel_trade_no UNIQUE (channel, gateway_trade_no),
        CONSTRAINT uq_payments_order_id UNIQUE (order_id))""",
    f"""CREATE TABLE card_stock (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        product_id uuid NOT NULL REFERENCES products(id),
        blob bytea NOT NULL,
        fingerprint varchar(64) NOT NULL,
        kid varchar(64) NOT NULL,
        status varchar(16) NOT NULL DEFAULT 'available'
            CHECK (status IN ('available', 'reserved', 'sold', 'void')),
        order_id uuid REFERENCES orders(id),
        created_by uuid REFERENCES users(id),
        created_at {TS},
        CONSTRAINT uq_card_stock_tenant_fingerprint UNIQUE (tenant_id, fingerprint))""",
    "CREATE INDEX ix_card_stock_product_status ON card_stock (tenant_id, product_id, status)",
    "CREATE INDEX ix_card_stock_order ON card_stock (order_id) WHERE order_id IS NOT NULL",
    f"""CREATE TABLE deliveries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        order_id uuid NOT NULL REFERENCES orders(id),
        card_stock_id uuid NOT NULL UNIQUE REFERENCES card_stock(id),
        kind varchar(16) NOT NULL DEFAULT 'card',
        reveal_count integer NOT NULL DEFAULT 0,
        first_revealed_at timestamptz,
        created_at {TS})""",
    "CREATE INDEX ix_deliveries_order ON deliveries (order_id)",
    f"""CREATE TABLE ledger_entries (
        id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        {TENANT},
        txn_id uuid NOT NULL,
        account varchar(64) NOT NULL,
        amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
        currency varchar(3) NOT NULL,
        memo text,
        order_id uuid REFERENCES orders(id),
        created_at {TS})""",
    "CREATE INDEX ix_ledger_txn ON ledger_entries (txn_id)",
    "CREATE INDEX ix_ledger_tenant_created ON ledger_entries (tenant_id, created_at DESC, id)",
    """CREATE FUNCTION ledger_txn_balanced() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
    BEGIN
        IF (SELECT coalesce(sum(amount_minor), 0) FROM ledger_entries
            WHERE txn_id = NEW.txn_id) <> 0 THEN
            RAISE EXCEPTION 'ledger transaction does not balance' USING ERRCODE = '23514';
        END IF;
        RETURN NULL;
    END $$""",
    """CREATE CONSTRAINT TRIGGER ledger_balanced AFTER INSERT ON ledger_entries
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_txn_balanced()""",
    f"""CREATE TABLE outbox (
        id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        {TENANT},
        topic varchar(64) NOT NULL,
        key varchar(128) NOT NULL,
        payload jsonb NOT NULL,
        created_at {TS},
        available_at {TS},
        processed_at timestamptz,
        attempts integer NOT NULL DEFAULT 0,
        last_error varchar(200))""",
    "CREATE INDEX ix_outbox_pending ON outbox (available_at, id) WHERE processed_at IS NULL",
    f"""CREATE TABLE audit_log (
        id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        {TENANT},
        actor_id uuid REFERENCES users(id),
        actor_role varchar(16) NOT NULL DEFAULT 'system',
        action varchar(64) NOT NULL,
        target varchar(128),
        before jsonb,
        after jsonb,
        ip varchar(64),
        detail jsonb,
        severity varchar(16) NOT NULL DEFAULT 'info',
        created_at {TS})""",
    "CREATE INDEX ix_audit_tenant_created ON audit_log (tenant_id, created_at DESC, id)",
    f"""CREATE TABLE custom_domains (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        hostname varchar(253) NOT NULL UNIQUE CHECK (hostname = lower(hostname)),
        verify_token varchar(128) NOT NULL,
        verified_at timestamptz,
        tls_mode varchar(16) NOT NULL DEFAULT 'caddy' CHECK (tls_mode IN ('caddy', 'cf_saas')),
        status varchar(16) NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'active', 'disabled')),
        created_at {TS})""",
]

# Tenant tables protected by RLS. ``tenants`` and ``custom_domains`` are global routing
# tables (Host -> tenant resolution happens before a tenant is known); app_api may only
# SELECT them.
RLS_TABLES = (
    "users",
    "agent_status",
    "products",
    "attachments",
    "conversations",
    "messages",
    "quotes",
    "orders",
    "order_events",
    "payments",
    "card_stock",
    "deliveries",
    "ledger_entries",
    "outbox",
    "audit_log",
)
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

ROLES_SQL = """
DO $$
DECLARE
    r text;
    pw text;
BEGIN
    FOREACH r IN ARRAY ARRAY['app_api', 'app_delivery'] LOOP
        pw := current_setting('g24.pw_' || r, true);
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS '
                'NOINHERIT PASSWORD %L', r, pw);
        ELSE
            BEGIN
                EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', r, pw);
            EXCEPTION WHEN insufficient_privilege THEN
                RAISE NOTICE 'role % exists and is not manageable by this owner', r;
            END;
        END IF;
    END LOOP;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_api, app_delivery', current_database());
END $$
"""

GRANTS = [
    "GRANT USAGE ON SCHEMA public TO app_api, app_delivery",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_api, app_delivery",
    # ---- app_api: normal CRUD, append-only journals, NO SELECT on card_stock.blob ----
    "GRANT SELECT ON tenants, custom_domains TO app_api",
    "GRANT SELECT, INSERT, UPDATE ON users, agent_status, products, conversations, quotes, "
    "orders TO app_api",
    "GRANT SELECT, INSERT ON attachments, messages, order_events, payments, ledger_entries, "
    "outbox, audit_log TO app_api",
    "GRANT SELECT ON deliveries TO app_api",
    "GRANT INSERT ON card_stock TO app_api",
    "GRANT SELECT (id, tenant_id, product_id, fingerprint, kid, status, order_id, created_by, "
    "created_at) ON card_stock TO app_api",
    # ---- app_delivery: claims stock, delivers, reveals ----
    "GRANT SELECT ON tenants, products, conversations TO app_delivery",
    "GRANT UPDATE (last_message_id, updated_at) ON conversations TO app_delivery",
    "GRANT SELECT ON card_stock TO app_delivery",
    "GRANT UPDATE (status, order_id) ON card_stock TO app_delivery",
    "GRANT SELECT ON orders TO app_delivery",
    "GRANT UPDATE (status, delivered_at, version, updated_at) ON orders TO app_delivery",
    "GRANT SELECT, INSERT ON deliveries, order_events, messages, audit_log, ledger_entries "
    "TO app_delivery",
    "GRANT UPDATE (reveal_count, first_revealed_at) ON deliveries TO app_delivery",
    "GRANT SELECT ON outbox TO app_delivery",
    "GRANT UPDATE (processed_at, attempts, last_error, available_at) ON outbox TO app_delivery",
]


def _rls_statements() -> list[str]:
    stmts: list[str] = []
    for t in RLS_TABLES:
        stmts += [
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {t} USING ({TENANT_PREDICATE}) "
            f"WITH CHECK ({TENANT_PREDICATE})",
        ]
    # The delivery worker drains the outbox for every tenant, then pins the tenant per job.
    stmts.append(
        "CREATE POLICY delivery_drain ON outbox FOR ALL TO app_delivery USING (true) "
        "WITH CHECK (true)"
    )
    # The reveal endpoint receives an order id and must discover its tenant first.
    stmts.append("CREATE POLICY delivery_lookup ON orders FOR SELECT TO app_delivery USING (true)")
    return stmts


def upgrade() -> None:
    bind = op.get_bind()
    api_pw = os.environ.get("APP_DB_API_PASSWORD", "")
    delivery_pw = os.environ.get("APP_DB_DELIVERY_PASSWORD", "")
    if len(api_pw) < 12 or len(delivery_pw) < 12:
        raise RuntimeError("APP_DB_API_PASSWORD and APP_DB_DELIVERY_PASSWORD (>=12 chars) required")
    # Roles first: policies and grants reference them.
    bind.execute(
        sa.text(
            "SELECT set_config('g24.pw_app_api', :a, true), "
            "set_config('g24.pw_app_delivery', :d, true)"
        ),
        {"a": api_pw, "d": delivery_pw},
    )
    op.execute(ROLES_SQL)
    bind.execute(
        sa.text(
            "SELECT set_config('g24.pw_app_api', '', true), "
            "set_config('g24.pw_app_delivery', '', true)"
        )
    )
    for stmt in TABLES:
        op.execute(stmt)
    for stmt in _rls_statements():
        op.execute(stmt)
    for stmt in GRANTS:
        op.execute(stmt)


def downgrade() -> None:
    for t in (
        "custom_domains",
        "audit_log",
        "outbox",
        "ledger_entries",
        "deliveries",
        "card_stock",
        "payments",
        "order_events",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("ALTER TABLE IF EXISTS quotes DROP CONSTRAINT IF EXISTS fk_quotes_order_id_orders")
    for t in (
        "orders",
        "quotes",
        "messages",
        "conversations",
        "attachments",
        "products",
        "agent_status",
        "users",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ledger_txn_balanced()")
