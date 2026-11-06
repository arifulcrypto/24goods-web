"""Commerce v4: cart checkouts (one payment for several orders), payment-channel
surcharge, buyer order input, buyer/staff cancel, rich product descriptions
(docs/review-v4.md §9).

Revision ID: 0003_commerce_v4
Revises: 0002_storefront_v3
Create Date: 2026-11-06

Hand-written SQL like 0001/0002. Runs as the schema owner.
"""

from __future__ import annotations

from alembic import op

revision = "0003_commerce_v4"
down_revision = "0002_storefront_v3"
branch_labels = None
depends_on = None

TS = "timestamptz NOT NULL DEFAULT now()"
TENANT = "tenant_id uuid NOT NULL REFERENCES tenants(id)"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

SCHEMA = [
    # ---- checkouts: a cart paid in ONE gateway transaction (out_trade_no = checkout_no) ----
    f"""CREATE TABLE checkouts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        {TENANT},
        checkout_no varchar(16) NOT NULL UNIQUE,
        buyer_id uuid NOT NULL REFERENCES users(id),
        amount_minor bigint NOT NULL CHECK (amount_minor > 0),
        currency varchar(3) NOT NULL DEFAULT 'CNY',
        status varchar(16) NOT NULL DEFAULT 'pending_payment' CHECK (status IN (
            'pending_payment', 'paid', 'cancelled', 'expired', 'manual_review')),
        channel varchar(32),
        expires_at timestamptz,
        paid_at timestamptz,
        created_at {TS},
        updated_at {TS})""",
    "CREATE INDEX ix_checkouts_tenant_buyer ON checkouts (tenant_id, buyer_id, created_at DESC)",
    "CREATE INDEX ix_checkouts_pending_expiry ON checkouts (expires_at) "
    "WHERE status = 'pending_payment'",
    # ---- orders ----
    "ALTER TABLE orders ADD COLUMN checkout_id uuid REFERENCES checkouts(id)",
    "CREATE INDEX ix_orders_checkout ON orders (checkout_id) WHERE checkout_id IS NOT NULL",
    # amount_minor (what the gateway charges) = subtotal_minor + fee_minor
    "ALTER TABLE orders ADD COLUMN subtotal_minor bigint",
    "ALTER TABLE orders ADD COLUMN fee_minor bigint NOT NULL DEFAULT 0 CHECK (fee_minor >= 0)",
    "ALTER TABLE orders ADD COLUMN fee_permille integer NOT NULL DEFAULT 0 "
    "CHECK (fee_permille BETWEEN 0 AND 200)",
    "ALTER TABLE orders ADD COLUMN buyer_input text CHECK (length(buyer_input) <= 4000)",
    "ALTER TABLE orders ADD COLUMN cancel_reason varchar(200)",
    # ---- payments: exactly one of order_id / checkout_id ----
    "ALTER TABLE payments ALTER COLUMN order_id DROP NOT NULL",
    "ALTER TABLE payments ADD COLUMN checkout_id uuid REFERENCES checkouts(id)",
    "ALTER TABLE payments ADD CONSTRAINT uq_payments_checkout_id UNIQUE (checkout_id)",
    "ALTER TABLE payments ADD CONSTRAINT ck_payments_target "
    "CHECK (num_nonnulls(order_id, checkout_id) = 1)",
    # ---- products ----
    # buyer_input: {enabled, required, multiline, max_len, title_i18n, prompt_i18n,
    #               placeholder_i18n}  (validated in the API, see review-v4 §4)
    "ALTER TABLE products ADD COLUMN buyer_input jsonb",
    # description_i18n is now markdown; this is the nh3-sanitized render cache
    "ALTER TABLE products ADD COLUMN description_html_i18n jsonb NOT NULL DEFAULT '{}'::jsonb",
]

BACKFILL = [
    "UPDATE orders SET subtotal_minor = amount_minor WHERE subtotal_minor IS NULL",
]

POST = [
    "ALTER TABLE orders ALTER COLUMN subtotal_minor SET NOT NULL",
    "ALTER TABLE orders ADD CONSTRAINT ck_orders_amount_split "
    "CHECK (amount_minor = subtotal_minor + fee_minor)",
    # Writers that predate v4 (and raw-SQL fixtures) omit subtotal: derive it.
    """CREATE FUNCTION orders_subtotal_default() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.subtotal_minor IS NULL THEN
            NEW.subtotal_minor := NEW.amount_minor - COALESCE(NEW.fee_minor, 0);
        END IF;
        RETURN NEW;
    END $$""",
    "CREATE TRIGGER orders_subtotal_default BEFORE INSERT ON orders "
    "FOR EACH ROW EXECUTE FUNCTION orders_subtotal_default()",
]

NEW_RLS_TABLES = ("checkouts",)

GRANTS = [
    "GRANT SELECT, INSERT, UPDATE ON checkouts TO app_api",
    "GRANT SELECT ON checkouts TO app_delivery",
]


def upgrade() -> None:
    for stmt in SCHEMA:
        op.execute(stmt)
    for stmt in BACKFILL:
        op.execute(stmt)
    for stmt in POST:
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
    # Never delete money records. payments is FORCE-RLS, so a plain COUNT here would be
    # filtered; SET NOT NULL checks every row regardless and fails if a checkout payment
    # (order_id IS NULL) exists. DDL is transactional: the failure rolls everything back.
    try:
        op.execute("ALTER TABLE payments ALTER COLUMN order_id SET NOT NULL")
    except Exception as exc:
        raise RuntimeError(
            "refusing to downgrade 0003: checkout payments exist (payments.order_id IS NULL); "
            "reconcile or move them first"
        ) from exc
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS description_html_i18n")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS buyer_input")
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS ck_payments_target")
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS uq_payments_checkout_id")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS checkout_id")
    op.execute("DROP TRIGGER IF EXISTS orders_subtotal_default ON orders")
    op.execute("DROP FUNCTION IF EXISTS orders_subtotal_default()")
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS ck_orders_amount_split")
    for col in (
        "cancel_reason",
        "buyer_input",
        "fee_permille",
        "fee_minor",
        "subtotal_minor",
        "checkout_id",
    ):
        op.execute(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col}")
    op.execute("DROP TABLE IF EXISTS checkouts CASCADE")
