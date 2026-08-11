"""add immutable admin AI credit adjustment ledger

Revision ID: 0015_m7_ai_credit_adjustments
Revises: 0014_m7_account_lifecycle
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_m7_ai_credit_adjustments"
down_revision: str | Sequence[str] | None = "0014_m7_account_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ADJUSTMENT_REASONS = (
    "support_credit",
    "service_recovery",
    "billing_correction",
    "administrative_correction",
    "fraud_reversal",
)


def upgrade() -> None:
    op.create_table(
        "ai_credit_adjustment_ledger_entries",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("entitlement_key", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("base_limit_value", sa.Integer(), nullable=False),
        sa.Column("effective_limit_before", sa.Integer(), nullable=False),
        sa.Column("effective_limit_after", sa.Integer(), nullable=False),
        sa.Column("reserved_count", sa.Integer(), nullable=False),
        sa.Column("consumed_count", sa.Integer(), nullable=False),
        sa.Column(
            "adjusted_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_adjustments_entitlement_key",
        ),
        sa.CheckConstraint(
            "delta <> 0 AND delta BETWEEN -1000 AND 1000",
            name="ck_ai_credit_adjustments_delta",
        ),
        sa.CheckConstraint(
            "reason_code IN ("
            "'support_credit', "
            "'service_recovery', "
            "'billing_correction', "
            "'administrative_correction', "
            "'fraud_reversal'"
            ")",
            name="ck_ai_credit_adjustments_reason",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key_digest) = 64",
            name="ck_ai_credit_adjustments_idempotency_digest",
        ),
        sa.CheckConstraint(
            "base_limit_value >= 0 AND effective_limit_before >= 0 AND effective_limit_after >= 0",
            name="ck_ai_credit_adjustments_nonnegative_limits",
        ),
        sa.CheckConstraint(
            "effective_limit_after = effective_limit_before + delta",
            name="ck_ai_credit_adjustments_limit_math",
        ),
        sa.CheckConstraint(
            "reserved_count >= 0 AND consumed_count >= 0 "
            "AND effective_limit_before >= reserved_count + consumed_count "
            "AND effective_limit_after >= reserved_count + consumed_count",
            name="ck_ai_credit_adjustments_usage_floor",
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
            ondelete="RESTRICT",
            name="fk_ai_credit_adjustments_account_business_subscription",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_credit_adjustments_business_idempotency",
        ),
    )

    op.create_index(
        "ix_ai_credit_adjustment_ledger_entries_business_id",
        "ai_credit_adjustment_ledger_entries",
        ["business_id"],
    )
    op.create_index(
        "ix_ai_credit_adjustment_ledger_entries_account_id",
        "ai_credit_adjustment_ledger_entries",
        ["account_id"],
    )
    op.create_index(
        "ix_ai_credit_adjustment_ledger_entries_subscription_id",
        "ai_credit_adjustment_ledger_entries",
        ["subscription_id"],
    )
    op.create_index(
        "ix_ai_credit_adjustments_account_adjusted",
        "ai_credit_adjustment_ledger_entries",
        ["account_id", "adjusted_at", "id"],
    )
    op.create_index(
        "ix_ai_credit_adjustments_business_adjusted",
        "ai_credit_adjustment_ledger_entries",
        ["business_id", "adjusted_at", "id"],
    )
    op.create_index(
        "ix_ai_credit_adjustments_actor_adjusted",
        "ai_credit_adjustment_ledger_entries",
        ["actor_user_id", "adjusted_at", "id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_ai_credit_adjustment_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'AI credit adjustment ledger entries are immutable'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_ai_credit_adjustments_immutable
        BEFORE UPDATE OR DELETE
        ON ai_credit_adjustment_ledger_entries
        FOR EACH ROW
        EXECUTE FUNCTION prevent_ai_credit_adjustment_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_ai_credit_adjustments_immutable
        ON ai_credit_adjustment_ledger_entries;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS prevent_ai_credit_adjustment_mutation();
        """
    )

    op.drop_index(
        "ix_ai_credit_adjustments_actor_adjusted",
        table_name="ai_credit_adjustment_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_adjustments_business_adjusted",
        table_name="ai_credit_adjustment_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_adjustments_account_adjusted",
        table_name="ai_credit_adjustment_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_adjustment_ledger_entries_subscription_id",
        table_name="ai_credit_adjustment_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_adjustment_ledger_entries_account_id",
        table_name="ai_credit_adjustment_ledger_entries",
    )
    op.drop_index(
        "ix_ai_credit_adjustment_ledger_entries_business_id",
        table_name="ai_credit_adjustment_ledger_entries",
    )

    op.drop_table("ai_credit_adjustment_ledger_entries")
