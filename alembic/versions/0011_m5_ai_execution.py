"""add recoverable AI execution leases

Revision ID: 0011_m5_ai_execution
Revises: 0010_m5_ai_conversations
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_m5_ai_execution"
down_revision: str | Sequence[str] | None = "0010_m5_ai_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLE_FIELDS = (
    "(role = 'user' "
    "AND created_by_user_id IS NOT NULL "
    "AND reply_to_message_id IS NULL "
    "AND idempotency_key_digest IS NOT NULL) OR "
    "(role = 'assistant' "
    "AND created_by_user_id IS NULL "
    "AND reply_to_message_id IS NOT NULL "
    "AND idempotency_key_digest IS NULL "
    "AND processing_token IS NULL "
    "AND processing_lease_expires_at IS NULL)"
)

STATUS_FIELDS = (
    "(role = 'user' AND ("
    "(status = 'pending' "
    "AND completed_at IS NULL "
    "AND error_code IS NULL "
    "AND ("
    "(credit_ledger_entry_id IS NULL "
    "AND processing_token IS NULL "
    "AND processing_lease_expires_at IS NULL) OR "
    "(credit_ledger_entry_id IS NOT NULL "
    "AND processing_token IS NOT NULL "
    "AND processing_lease_expires_at IS NOT NULL)"
    ")) OR "
    "(status = 'completed' "
    "AND credit_ledger_entry_id IS NOT NULL "
    "AND completed_at IS NOT NULL "
    "AND error_code IS NULL "
    "AND processing_token IS NULL "
    "AND processing_lease_expires_at IS NULL) OR "
    "(status = 'failed' "
    "AND completed_at IS NOT NULL "
    "AND error_code IS NOT NULL "
    "AND processing_token IS NULL "
    "AND processing_lease_expires_at IS NULL "
    "AND char_length(trim(error_code)) BETWEEN 1 AND 64)"
    ")) OR role = 'assistant'"
)

OLD_ROLE_FIELDS = (
    "(role = 'user' "
    "AND created_by_user_id IS NOT NULL "
    "AND reply_to_message_id IS NULL "
    "AND idempotency_key_digest IS NOT NULL) OR "
    "(role = 'assistant' "
    "AND created_by_user_id IS NULL "
    "AND reply_to_message_id IS NOT NULL "
    "AND idempotency_key_digest IS NULL)"
)

OLD_STATUS_FIELDS = (
    "(role = 'user' AND ("
    "(status = 'pending' AND completed_at IS NULL AND error_code IS NULL) OR "
    "(status = 'completed' "
    "AND credit_ledger_entry_id IS NOT NULL "
    "AND completed_at IS NOT NULL "
    "AND error_code IS NULL) OR "
    "(status = 'failed' "
    "AND completed_at IS NOT NULL "
    "AND error_code IS NOT NULL "
    "AND char_length(trim(error_code)) BETWEEN 1 AND 64)"
    ")) OR role = 'assistant'"
)


def upgrade() -> None:
    op.add_column(
        "ai_messages",
        sa.Column("processing_token", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "ai_messages",
        sa.Column(
            "processing_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.drop_constraint(
        "ck_ai_messages_role_fields",
        "ai_messages",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_messages_status_fields",
        "ai_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_messages_role_fields",
        "ai_messages",
        ROLE_FIELDS,
    )
    op.create_check_constraint(
        "ck_ai_messages_status_fields",
        "ai_messages",
        STATUS_FIELDS,
    )
    op.create_check_constraint(
        "ck_ai_messages_processing_lease",
        "ai_messages",
        "processing_lease_expires_at IS NULL OR processing_lease_expires_at >= created_at",
    )
    op.create_index(
        "ix_ai_messages_business_pending_lease",
        "ai_messages",
        ["business_id", "status", "processing_lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_messages_business_pending_lease",
        table_name="ai_messages",
    )
    op.drop_constraint(
        "ck_ai_messages_processing_lease",
        "ai_messages",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_messages_status_fields",
        "ai_messages",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_messages_role_fields",
        "ai_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ai_messages_role_fields",
        "ai_messages",
        OLD_ROLE_FIELDS,
    )
    op.create_check_constraint(
        "ck_ai_messages_status_fields",
        "ai_messages",
        OLD_STATUS_FIELDS,
    )

    op.drop_column("ai_messages", "processing_lease_expires_at")
    op.drop_column("ai_messages", "processing_token")
