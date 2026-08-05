from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import (
    AI_CONVERSATION_TITLE_MAX_LENGTH,
    AI_MESSAGE_CONTENT_MAX_LENGTH,
    AICreditLedgerStatus,
    AIMessageRole,
    AIMessageStatus,
)


class AIConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=AI_CONVERSATION_TITLE_MAX_LENGTH,
    )

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class AIConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    created_by_user_id: UUID
    title: str | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AIConversationPageRead(BaseModel):
    items: list[AIConversationRead]
    total: int
    limit: int
    offset: int


class AIMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        min_length=1,
        max_length=AI_MESSAGE_CONTENT_MAX_LENGTH,
    )

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AIMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    conversation_id: UUID
    role: AIMessageRole
    status: AIMessageStatus
    content: str
    created_by_user_id: UUID | None
    reply_to_message_id: UUID | None
    credit_ledger_entry_id: UUID | None
    provider_response_id: str | None
    provider_model: str | None
    provider_finish_reason: str | None
    prompt_token_count: int | None
    response_token_count: int | None
    thoughts_token_count: int | None
    total_token_count: int | None
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class AIMessagePageRead(BaseModel):
    items: list[AIMessageRead]
    total: int
    limit: int
    offset: int


class AICreditUsageSummaryRead(BaseModel):
    account_id: UUID | None
    subscription_id: UUID
    business_id: UUID
    limit_value: int | None
    reserved_count: int
    consumed_count: int
    remaining: int | None
    period_started_at: datetime
    period_ends_at: datetime


class AICreditLedgerEntryRead(BaseModel):
    id: UUID
    account_id: UUID
    subscription_id: UUID
    status: AICreditLedgerStatus
    quantity: int
    reserved_at: datetime
    consumed_at: datetime | None
    released_at: datetime | None
    release_reason: str | None


class AIUsageHistoryRead(BaseModel):
    current: AICreditUsageSummaryRead
    items: list[AICreditLedgerEntryRead]
    total: int
    limit: int
    offset: int


class AIMessageSubmissionRead(BaseModel):
    conversation: AIConversationRead
    user_message: AIMessageRead
    assistant_message: AIMessageRead | None = None
    usage: AICreditUsageSummaryRead | None = None
    replayed: bool
