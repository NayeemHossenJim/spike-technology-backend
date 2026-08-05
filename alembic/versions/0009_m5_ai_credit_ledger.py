"""add atomic tenant-owned AI credit ledger

Revision ID: 0009_m5_ai_credit_ledger
Revises: 0008_m4_data_processing
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_m5_ai_credit_ledger"
down_revision: str | Sequence[str] | None = "0008_m4_data_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_credit_accounts",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_key", sa.String(length=64), nullable=False),
        sa.Column("period_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.Column("reserved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_accounts_entitlement_key",
        ),
        sa.CheckConstraint(
            "period_ends_at > period_started_at",
            name="ck_ai_credit_accounts_period",
        ),
        sa.CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_ai_credit_accounts_nonnegative_limit",
        ),
        sa.CheckConstraint(
            "reserved_count >= 0 AND consumed_count >= 0 "
            "AND (limit_value IS NULL OR reserved_count + consumed_count <= limit_value)",
            name="ck_ai_credit_accounts_balance",
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
            name="fk_ai_credit_accounts_subscription_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            "subscription_id",
            name="uq_ai_credit_accounts_id_business_subscription",
        ),
        sa.UniqueConstraint(
            "business_id",
            "subscription_id",
            "entitlement_key",
            "period_started_at",
            "period_ends_at",
            name="uq_ai_credit_accounts_subscription_period",
        ),
    )
    op.create_index(
        "ix_ai_credit_accounts_business_id",
        "ai_credit_accounts",
        ["business_id"],
    )
    op.create_index(
        "ix_ai_credit_accounts_subscription_id",
        "ai_credit_accounts",
        ["subscription_id"],
    )
    op.create_index(
        "ix_ai_credit_accounts_business_period",
        "ai_credit_accounts",
        ["business_id", "period_started_at", "period_ends_at"],
    )

    op.create_table(
        "ai_credit_ledger_entries",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_key", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_ledger_entries_entitlement_key",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'consumed', 'released')",
            name="ck_ai_credit_ledger_entries_status",
        ),
        sa.CheckConstraint(
            "quantity = 1",
            name="ck_ai_credit_ledger_entries_unit_quantity",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_digest) = 64",
            name="ck_ai_credit_ledger_entries_idempotency_digest",
        ),
        sa.CheckConstraint(
            "(status = 'reserved' "
            "AND consumed_at IS NULL "
            "AND released_at IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'consumed' "
            "AND consumed_at IS NOT NULL "
            "AND released_at IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'released' "
            "AND consumed_at IS NULL "
            "AND released_at IS NOT NULL "
            "AND release_reason IS NOT NULL "
            "AND char_length(trim(release_reason)) BETWEEN 1 AND 255)",
            name="ck_ai_credit_ledger_entries_status_fields",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL OR consumed_at >= reserved_at) "
            "AND (released_at IS NULL OR released_at >= reserved_at)",
            name="ck_ai_credit_ledger_entries_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "business_id", "subscription_id"],
            [
                "ai_credit_accounts.id",
                "ai_credit_accounts.business_id",
                "ai_credit_accounts.subscription_id",
            ],
            ondelete="CASCADE",
            name="fk_ai_credit_ledger_entries_account_business_subscription",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_credit_ledger_entries_id_business",
        ),
        sa.UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_credit_ledger_entries_business_idempotency",
        ),
    )
    op.create_index(
        "ix_ai_credit_ledger_entries_business_id",
        "ai_credit_ledger_entries",
        ["business_id"],
    )
    op.create_index(
        "ix_ai_credit_ledger_entries_account_id",
        "ai_credit_ledger_entries",
        ["account_id"],
    )
    op.create_index(
        "ix_ai_credit_ledger_entries_subscription_id",
        "ai_credit_ledger_entries",
        ["subscription_id"],
    )
    op.create_index(
        "ix_ai_credit_ledger_entries_account_status",
        "ai_credit_ledger_entries",
        ["account_id", "status"],
    )
    op.create_index(
        "ix_ai_credit_ledger_entries_business_status_created",
        "ai_credit_ledger_entries",
        ["business_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_credit_ledger_entries_business_status_created",
        table_name="ai_credit_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_ledger_entries_account_status",
        table_name="ai_credit_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_ledger_entries_subscription_id",
        table_name="ai_credit_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_ledger_entries_account_id",
        table_name="ai_credit_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_ledger_entries_business_id",
        table_name="ai_credit_ledger_entries",
    )
    op.drop_table("ai_credit_ledger_entries")

    op.drop_index(
        "ix_ai_credit_accounts_business_period",
        table_name="ai_credit_accounts",
    )
    op.drop_index(
        "ix_ai_credit_accounts_subscription_id",
        table_name="ai_credit_accounts",
    )
    op.drop_index(
        "ix_ai_credit_accounts_business_id",
        table_name="ai_credit_accounts",
    )
    op.drop_table("ai_credit_accounts")