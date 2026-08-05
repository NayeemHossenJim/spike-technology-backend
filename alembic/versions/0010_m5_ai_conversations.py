"""add tenant-owned AI conversations and messages

Revision ID: 0010_m5_ai_conversations
Revises: 0009_m5_ai_credit_ledger
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010_m5_ai_conversations"
down_revision: str | Sequence[str] | None = "0009_m5_ai_credit_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "title IS NULL OR char_length(trim(title)) BETWEEN 1 AND 160",
            name="ck_ai_conversations_title",
        ),
        sa.CheckConstraint(
            "last_message_at IS NULL OR last_message_at >= created_at",
            name="ck_ai_conversations_last_message",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_ai_conversations_business_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_conversations_id_business",
        ),
    )
    op.create_index(
        "ix_ai_conversations_business_id",
        "ai_conversations",
        ["business_id"],
    )
    op.create_index(
        "ix_ai_conversations_created_by_user_id",
        "ai_conversations",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_ai_conversations_business_updated",
        "ai_conversations",
        ["business_id", "updated_at", "id"],
    )

    op.create_table(
        "ai_messages",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reply_to_message_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=True),
        sa.Column("credit_ledger_entry_id", sa.Uuid(), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
        sa.Column("provider_model", sa.String(length=128), nullable=True),
        sa.Column("provider_finish_reason", sa.String(length=64), nullable=True),
        sa.Column("prompt_token_count", sa.Integer(), nullable=True),
        sa.Column("response_token_count", sa.Integer(), nullable=True),
        sa.Column("thoughts_token_count", sa.Integer(), nullable=True),
        sa.Column("total_token_count", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_ai_messages_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_ai_messages_status",
        ),
        sa.CheckConstraint(
            "char_length(trim(content)) BETWEEN 1 AND 12000",
            name="ck_ai_messages_content",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest IS NULL OR char_length(idempotency_key_digest) = 64",
            name="ck_ai_messages_idempotency_digest",
        ),
        sa.CheckConstraint(
            "(role = 'user' "
            "AND created_by_user_id IS NOT NULL "
            "AND reply_to_message_id IS NULL "
            "AND idempotency_key_digest IS NOT NULL) OR "
            "(role = 'assistant' "
            "AND created_by_user_id IS NULL "
            "AND reply_to_message_id IS NOT NULL "
            "AND idempotency_key_digest IS NULL)",
            name="ck_ai_messages_role_fields",
        ),
        sa.CheckConstraint(
            "(role = 'user' "
            "AND provider_response_id IS NULL "
            "AND provider_model IS NULL "
            "AND provider_finish_reason IS NULL "
            "AND prompt_token_count IS NULL "
            "AND response_token_count IS NULL "
            "AND thoughts_token_count IS NULL "
            "AND total_token_count IS NULL) OR "
            "(role = 'assistant' "
            "AND status = 'completed' "
            "AND credit_ledger_entry_id IS NOT NULL "
            "AND provider_model IS NOT NULL "
            "AND provider_finish_reason IS NOT NULL "
            "AND completed_at IS NOT NULL "
            "AND error_code IS NULL)",
            name="ck_ai_messages_provider_fields",
        ),
        sa.CheckConstraint(
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
            ")) OR role = 'assistant'",
            name="ck_ai_messages_status_fields",
        ),
        sa.CheckConstraint(
            "(prompt_token_count IS NULL OR prompt_token_count >= 0) "
            "AND (response_token_count IS NULL OR response_token_count >= 0) "
            "AND (thoughts_token_count IS NULL OR thoughts_token_count >= 0) "
            "AND (total_token_count IS NULL OR total_token_count >= 0)",
            name="ck_ai_messages_token_counts",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_ai_messages_completed_at",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "business_id"],
            ["ai_conversations.id", "ai_conversations.business_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_conversation_business",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_business_creator",
        ),
        sa.ForeignKeyConstraint(
            ["credit_ledger_entry_id", "business_id"],
            ["ai_credit_ledger_entries.id", "ai_credit_ledger_entries.business_id"],
            name="fk_ai_messages_credit_ledger_business",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id", "business_id", "conversation_id"],
            ["ai_messages.id", "ai_messages.business_id", "ai_messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_reply_conversation_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            "conversation_id",
            name="uq_ai_messages_id_business_conversation",
        ),
        sa.UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_messages_business_idempotency",
        ),
        sa.UniqueConstraint(
            "business_id",
            "credit_ledger_entry_id",
            "role",
            name="uq_ai_messages_business_ledger_role",
        ),
        sa.UniqueConstraint(
            "business_id",
            "conversation_id",
            "reply_to_message_id",
            name="uq_ai_messages_business_conversation_reply",
        ),
    )
    op.create_index(
        "ix_ai_messages_business_id",
        "ai_messages",
        ["business_id"],
    )
    op.create_index(
        "ix_ai_messages_conversation_id",
        "ai_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_ai_messages_created_by_user_id",
        "ai_messages",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_ai_messages_credit_ledger_entry_id",
        "ai_messages",
        ["credit_ledger_entry_id"],
    )
    op.create_index(
        "ix_ai_messages_business_conversation_created",
        "ai_messages",
        ["business_id", "conversation_id", "created_at", "id"],
    )
    op.create_index(
        "ix_ai_messages_business_status_created",
        "ai_messages",
        ["business_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_messages_business_status_created",
        table_name="ai_messages",
    )
    op.drop_index(
        "ix_ai_messages_business_conversation_created",
        table_name="ai_messages",
    )
    op.drop_index(
        "ix_ai_messages_credit_ledger_entry_id",
        table_name="ai_messages",
    )
    op.drop_index(
        "ix_ai_messages_created_by_user_id",
        table_name="ai_messages",
    )
    op.drop_index(
        "ix_ai_messages_conversation_id",
        table_name="ai_messages",
    )
    op.drop_index(
        "ix_ai_messages_business_id",
        table_name="ai_messages",
    )
    op.drop_table("ai_messages")

    op.drop_index(
        "ix_ai_conversations_business_updated",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_created_by_user_id",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_business_id",
        table_name="ai_conversations",
    )
    op.drop_table("ai_conversations")
