"""add Stripe billing, webhook, checkout, and invoice synchronization

Revision ID: 0005_m2_stripe_billing
Revises: 0004_m1_foundation
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0005_m2_stripe_billing"
down_revision: str | Sequence[str] | None = "0004_m1_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_event_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_event_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("last_stripe_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "billing_checkout_sessions",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(length=255), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'open', 'complete', 'expired')",
            name="ck_billing_checkout_status",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_billing_checkout_subscription_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_billing_checkout_business_idempotency",
        ),
        sa.UniqueConstraint("stripe_checkout_session_id"),
    )
    op.create_index(
        "ix_billing_checkout_sessions_business_id",
        "billing_checkout_sessions",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_checkout_sessions_plan_id",
        "billing_checkout_sessions",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_checkout_sessions_subscription_id",
        "billing_checkout_sessions",
        ["subscription_id"],
        unique=False,
    )
    op.create_index(
        "uq_billing_checkout_one_open_per_business",
        "billing_checkout_sessions",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'open')"),
    )

    op.create_table(
        "billing_invoices",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(length=255), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=False),
        sa.Column("invoice_number", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("amount_due_cents", sa.Integer(), nullable=False),
        sa.Column("amount_paid_cents", sa.Integer(), nullable=False),
        sa.Column("amount_remaining_cents", sa.Integer(), nullable=False),
        sa.Column("stripe_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hosted_invoice_url", sa.Text(), nullable=True),
        sa.Column("invoice_pdf_url", sa.Text(), nullable=True),
        sa.Column(
            "last_stripe_event_created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("last_stripe_event_id", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'open', 'paid', 'uncollectible', 'void')",
            name="ck_billing_invoices_status",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3",
            name="ck_billing_invoices_currency_length",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_billing_invoices_subscription_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_invoice_id"),
    )
    op.create_index(
        "ix_billing_invoices_business_id",
        "billing_invoices",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_invoices_stripe_created_at",
        "billing_invoices",
        ["stripe_created_at"],
        unique=False,
    )
    op.create_index(
        "ix_billing_invoices_stripe_customer_id",
        "billing_invoices",
        ["stripe_customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_billing_invoices_subscription_id",
        "billing_invoices",
        ["subscription_id"],
        unique=False,
    )

    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result IN ('processed', 'ignored')",
            name="ck_stripe_webhook_events_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )
    op.create_index(
        "ix_stripe_webhook_events_event_type",
        "stripe_webhook_events",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stripe_webhook_events_event_type",
        table_name="stripe_webhook_events",
    )
    op.drop_table("stripe_webhook_events")

    op.drop_index("ix_billing_invoices_subscription_id", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_stripe_customer_id", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_stripe_created_at", table_name="billing_invoices")
    op.drop_index("ix_billing_invoices_business_id", table_name="billing_invoices")
    op.drop_table("billing_invoices")

    op.drop_index(
        "uq_billing_checkout_one_open_per_business",
        table_name="billing_checkout_sessions",
    )
    op.drop_index(
        "ix_billing_checkout_sessions_subscription_id",
        table_name="billing_checkout_sessions",
    )
    op.drop_index(
        "ix_billing_checkout_sessions_plan_id",
        table_name="billing_checkout_sessions",
    )
    op.drop_index(
        "ix_billing_checkout_sessions_business_id",
        table_name="billing_checkout_sessions",
    )
    op.drop_table("billing_checkout_sessions")

    op.drop_column("subscriptions", "last_stripe_synced_at")
    op.drop_column("subscriptions", "last_stripe_event_id")
    op.drop_column("subscriptions", "last_stripe_event_created_at")
    op.drop_column("subscriptions", "stripe_price_id")
