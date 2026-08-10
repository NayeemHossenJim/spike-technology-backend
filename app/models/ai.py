from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey
from app.models.subscription import EntitlementKey

AI_CONVERSATION_TITLE_MAX_LENGTH = 160
AI_MESSAGE_CONTENT_MAX_LENGTH = 12_000


class AICreditLedgerStatus(StrEnum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class AICreditAdjustmentReason(StrEnum):
    SUPPORT_CREDIT = "support_credit"
    SERVICE_RECOVERY = "service_recovery"
    BILLING_CORRECTION = "billing_correction"
    ADMINISTRATIVE_CORRECTION = "administrative_correction"
    FRAUD_REVERSAL = "fraud_reversal"


class AIMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AIMessageStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class AICreditAccount(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_credit_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_ai_credit_accounts_subscription_business",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            "subscription_id",
            name="uq_ai_credit_accounts_id_business_subscription",
        ),
        UniqueConstraint(
            "business_id",
            "subscription_id",
            "entitlement_key",
            "period_started_at",
            "period_ends_at",
            name="uq_ai_credit_accounts_subscription_period",
        ),
        CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_accounts_entitlement_key",
        ),
        CheckConstraint(
            "period_ends_at > period_started_at",
            name="ck_ai_credit_accounts_period",
        ),
        CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_ai_credit_accounts_nonnegative_limit",
        ),
        CheckConstraint(
            "reserved_count >= 0 AND consumed_count >= 0 "
            "AND (limit_value IS NULL OR reserved_count + consumed_count <= limit_value)",
            name="ck_ai_credit_accounts_balance",
        ),
        Index(
            "ix_ai_credit_accounts_business_period",
            "business_id",
            "period_started_at",
            "period_ends_at",
        ),
    )

    subscription_id: UUID = Field(nullable=False, index=True)
    entitlement_key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    period_started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    period_ends_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    limit_value: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    reserved_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )
    consumed_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )


class AICreditLedgerEntry(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_credit_ledger_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "business_id", "subscription_id"],
            [
                "ai_credit_accounts.id",
                "ai_credit_accounts.business_id",
                "ai_credit_accounts.subscription_id",
            ],
            ondelete="CASCADE",
            name="fk_ai_credit_ledger_entries_account_business_subscription",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_credit_ledger_entries_id_business",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_credit_ledger_entries_business_idempotency",
        ),
        CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_ledger_entries_entitlement_key",
        ),
        CheckConstraint(
            "status IN ('reserved', 'consumed', 'released')",
            name="ck_ai_credit_ledger_entries_status",
        ),
        CheckConstraint(
            "quantity = 1",
            name="ck_ai_credit_ledger_entries_unit_quantity",
        ),
        CheckConstraint(
            "char_length(idempotency_key_digest) = 64",
            name="ck_ai_credit_ledger_entries_idempotency_digest",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "(consumed_at IS NULL OR consumed_at >= reserved_at) "
            "AND (released_at IS NULL OR released_at >= reserved_at)",
            name="ck_ai_credit_ledger_entries_timestamps",
        ),
        Index(
            "ix_ai_credit_ledger_entries_account_status",
            "account_id",
            "status",
        ),
        Index(
            "ix_ai_credit_ledger_entries_business_status_created",
            "business_id",
            "status",
            "created_at",
        ),
    )

    account_id: UUID = Field(nullable=False, index=True)
    subscription_id: UUID = Field(nullable=False, index=True)
    entitlement_key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    idempotency_key_digest: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    status: AICreditLedgerStatus = Field(
        default=AICreditLedgerStatus.RESERVED,
        sa_column=Column(
            String(32),
            nullable=False,
            default=AICreditLedgerStatus.RESERVED,
        ),
    )
    quantity: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1),
    )
    reserved_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    consumed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    released_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    release_reason: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )


class AICreditAdjustmentLedgerEntry(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_credit_adjustment_ledger_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "business_id", "subscription_id"],
            [
                "ai_credit_accounts.id",
                "ai_credit_accounts.business_id",
                "ai_credit_accounts.subscription_id",
            ],
            ondelete="RESTRICT",
            name="fk_ai_credit_adjustments_account_business_subscription",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_credit_adjustments_business_idempotency",
        ),
        CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_adjustments_entitlement_key",
        ),
        CheckConstraint(
            "delta <> 0 AND delta BETWEEN -1000 AND 1000",
            name="ck_ai_credit_adjustments_delta",
        ),
        CheckConstraint(
            "reason_code IN ("
            "'support_credit', "
            "'service_recovery', "
            "'billing_correction', "
            "'administrative_correction', "
            "'fraud_reversal'"
            ")",
            name="ck_ai_credit_adjustments_reason",
        ),
        CheckConstraint(
            "char_length(idempotency_key_digest) = 64",
            name="ck_ai_credit_adjustments_idempotency_digest",
        ),
        CheckConstraint(
            "base_limit_value >= 0 AND effective_limit_before >= 0 AND effective_limit_after >= 0",
            name="ck_ai_credit_adjustments_nonnegative_limits",
        ),
        CheckConstraint(
            "effective_limit_after = effective_limit_before + delta",
            name="ck_ai_credit_adjustments_limit_math",
        ),
        CheckConstraint(
            "reserved_count >= 0 AND consumed_count >= 0 "
            "AND effective_limit_before >= reserved_count + consumed_count "
            "AND effective_limit_after >= reserved_count + consumed_count",
            name="ck_ai_credit_adjustments_usage_floor",
        ),
        Index(
            "ix_ai_credit_adjustments_account_adjusted",
            "account_id",
            "adjusted_at",
            "id",
        ),
        Index(
            "ix_ai_credit_adjustments_business_adjusted",
            "business_id",
            "adjusted_at",
            "id",
        ),
        Index(
            "ix_ai_credit_adjustments_actor_adjusted",
            "actor_user_id",
            "adjusted_at",
            "id",
        ),
    )

    account_id: UUID = Field(nullable=False, index=True)
    subscription_id: UUID = Field(nullable=False, index=True)
    entitlement_key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    actor_user_id: UUID = Field(nullable=False)
    idempotency_key_digest: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    delta: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    reason_code: AICreditAdjustmentReason = Field(
        sa_column=Column(String(64), nullable=False),
    )
    request_id: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    base_limit_value: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    effective_limit_before: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    effective_limit_after: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    reserved_count: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    consumed_count: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    adjusted_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AIConversation(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_ai_conversations_business_creator",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_conversations_id_business",
        ),
        CheckConstraint(
            "title IS NULL OR char_length(trim(title)) BETWEEN 1 AND 160",
            name="ck_ai_conversations_title",
        ),
        CheckConstraint(
            "last_message_at IS NULL OR last_message_at >= created_at",
            name="ck_ai_conversations_last_message",
        ),
        Index(
            "ix_ai_conversations_business_updated",
            "business_id",
            "updated_at",
            "id",
        ),
    )

    created_by_user_id: UUID = Field(nullable=False, index=True)
    title: str | None = Field(
        default=None,
        sa_column=Column(String(AI_CONVERSATION_TITLE_MAX_LENGTH), nullable=True),
    )
    last_message_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class AIMessage(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "business_id"],
            ["ai_conversations.id", "ai_conversations.business_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_conversation_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_business_creator",
        ),
        ForeignKeyConstraint(
            ["credit_ledger_entry_id", "business_id"],
            ["ai_credit_ledger_entries.id", "ai_credit_ledger_entries.business_id"],
            name="fk_ai_messages_credit_ledger_business",
        ),
        ForeignKeyConstraint(
            ["reply_to_message_id", "business_id", "conversation_id"],
            ["ai_messages.id", "ai_messages.business_id", "ai_messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_ai_messages_reply_conversation_business",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            "conversation_id",
            name="uq_ai_messages_id_business_conversation",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_messages_business_idempotency",
        ),
        UniqueConstraint(
            "business_id",
            "credit_ledger_entry_id",
            "role",
            name="uq_ai_messages_business_ledger_role",
        ),
        UniqueConstraint(
            "business_id",
            "conversation_id",
            "reply_to_message_id",
            name="uq_ai_messages_business_conversation_reply",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_ai_messages_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_ai_messages_status",
        ),
        CheckConstraint(
            f"char_length(trim(content)) BETWEEN 1 AND {AI_MESSAGE_CONTENT_MAX_LENGTH}",
            name="ck_ai_messages_content",
        ),
        CheckConstraint(
            "idempotency_key_digest IS NULL OR char_length(idempotency_key_digest) = 64",
            name="ck_ai_messages_idempotency_digest",
        ),
        CheckConstraint(
            "(role = 'user' "
            "AND created_by_user_id IS NOT NULL "
            "AND reply_to_message_id IS NULL "
            "AND idempotency_key_digest IS NOT NULL) OR "
            "(role = 'assistant' "
            "AND created_by_user_id IS NULL "
            "AND reply_to_message_id IS NOT NULL "
            "AND idempotency_key_digest IS NULL "
            "AND processing_token IS NULL "
            "AND processing_lease_expires_at IS NULL)",
            name="ck_ai_messages_role_fields",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
            ")) OR role = 'assistant'",
            name="ck_ai_messages_status_fields",
        ),
        CheckConstraint(
            "processing_lease_expires_at IS NULL OR processing_lease_expires_at >= created_at",
            name="ck_ai_messages_processing_lease",
        ),
        CheckConstraint(
            "(prompt_token_count IS NULL OR prompt_token_count >= 0) "
            "AND (response_token_count IS NULL OR response_token_count >= 0) "
            "AND (thoughts_token_count IS NULL OR thoughts_token_count >= 0) "
            "AND (total_token_count IS NULL OR total_token_count >= 0)",
            name="ck_ai_messages_token_counts",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= created_at",
            name="ck_ai_messages_completed_at",
        ),
        Index(
            "ix_ai_messages_business_conversation_created",
            "business_id",
            "conversation_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_ai_messages_business_status_created",
            "business_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_ai_messages_business_pending_lease",
            "business_id",
            "status",
            "processing_lease_expires_at",
        ),
    )

    conversation_id: UUID = Field(nullable=False, index=True)
    role: AIMessageRole = Field(
        sa_column=Column(String(32), nullable=False),
    )
    status: AIMessageStatus = Field(
        default=AIMessageStatus.PENDING,
        sa_column=Column(
            String(32),
            nullable=False,
            default=AIMessageStatus.PENDING,
        ),
    )
    content: str = Field(
        sa_column=Column(Text, nullable=False),
    )
    created_by_user_id: UUID | None = Field(
        default=None,
        nullable=True,
        index=True,
    )
    reply_to_message_id: UUID | None = Field(
        default=None,
        nullable=True,
    )
    idempotency_key_digest: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    credit_ledger_entry_id: UUID | None = Field(
        default=None,
        nullable=True,
        index=True,
    )
    processing_token: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid(), nullable=True),
    )
    processing_lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    provider_response_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    provider_model: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    provider_finish_reason: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    prompt_token_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    response_token_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    thoughts_token_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    total_token_count: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
