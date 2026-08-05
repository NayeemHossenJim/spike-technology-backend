from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.main import create_app
from app.models.ai import (
    AIConversation,
    AIMessage,
    AIMessageRole,
    AIMessageStatus,
)
from app.schemas.ai import AIConversationCreate, AIMessageCreate, AIMessageRead


def _named(table, constraint_type):
    return {
        item.name
        for item in table.constraints
        if isinstance(item, constraint_type) and item.name is not None
    }


def _index_names(table) -> set[str]:
    return {item.name for item in table.indexes if isinstance(item, Index)}


def test_ai_conversation_message_enums_are_stable() -> None:
    assert [item.value for item in AIMessageRole] == ["user", "assistant"]
    assert [item.value for item in AIMessageStatus] == [
        "pending",
        "completed",
        "failed",
    ]


def test_ai_conversation_schema_contract_is_registered() -> None:
    table = AIConversation.__table__

    assert table.name == "ai_conversations"
    assert {
        "business_id",
        "id",
        "created_at",
        "updated_at",
        "created_by_user_id",
        "title",
        "last_message_at",
    } == set(table.columns.keys())
    assert {
        "fk_ai_conversations_business_creator",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_ai_conversations_id_business",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_ai_conversations_title",
        "ck_ai_conversations_last_message",
    } <= _named(table, CheckConstraint)
    assert {
        "ix_ai_conversations_business_id",
        "ix_ai_conversations_created_by_user_id",
        "ix_ai_conversations_business_updated",
    } <= _index_names(table)


def test_ai_message_schema_contract_is_registered() -> None:
    table = AIMessage.__table__

    assert table.name == "ai_messages"
    assert {
        "business_id",
        "id",
        "created_at",
        "updated_at",
        "conversation_id",
        "role",
        "status",
        "content",
        "created_by_user_id",
        "reply_to_message_id",
        "idempotency_key_digest",
        "credit_ledger_entry_id",
        "processing_token",
        "processing_lease_expires_at",
        "provider_response_id",
        "provider_model",
        "provider_finish_reason",
        "prompt_token_count",
        "response_token_count",
        "thoughts_token_count",
        "total_token_count",
        "completed_at",
        "error_code",
    } == set(table.columns.keys())
    assert {
        "fk_ai_messages_conversation_business",
        "fk_ai_messages_business_creator",
        "fk_ai_messages_credit_ledger_business",
        "fk_ai_messages_reply_conversation_business",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_ai_messages_id_business_conversation",
        "uq_ai_messages_business_idempotency",
        "uq_ai_messages_business_ledger_role",
        "uq_ai_messages_business_conversation_reply",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_ai_messages_role",
        "ck_ai_messages_status",
        "ck_ai_messages_content",
        "ck_ai_messages_idempotency_digest",
        "ck_ai_messages_role_fields",
        "ck_ai_messages_provider_fields",
        "ck_ai_messages_status_fields",
        "ck_ai_messages_processing_lease",
        "ck_ai_messages_token_counts",
        "ck_ai_messages_completed_at",
    } <= _named(table, CheckConstraint)
    assert {
        "ix_ai_messages_business_id",
        "ix_ai_messages_conversation_id",
        "ix_ai_messages_created_by_user_id",
        "ix_ai_messages_credit_ledger_entry_id",
        "ix_ai_messages_business_conversation_created",
        "ix_ai_messages_business_status_created",
        "ix_ai_messages_business_pending_lease",
    } <= _index_names(table)


def test_ai_conversation_and_message_payloads_normalize_and_forbid_extras() -> None:
    assert AIConversationCreate(title="  Quarterly analysis  ").title == "Quarterly analysis"
    assert AIConversationCreate(title="   ").title is None
    assert AIMessageCreate(content="  Explain the revenue trend.  ").content == (
        "Explain the revenue trend."
    )

    with pytest.raises(ValidationError):
        AIMessageCreate(content="   ")
    with pytest.raises(ValidationError):
        AIMessageCreate(content="x" * 12_001)
    with pytest.raises(ValidationError):
        AIConversationCreate(title="Valid", unexpected=True)


def test_ai_message_read_contract_never_exposes_internal_execution_fields() -> None:
    assert "idempotency_key_digest" not in AIMessageRead.model_fields
    assert "processing_token" not in AIMessageRead.model_fields
    assert "processing_lease_expires_at" not in AIMessageRead.model_fields


def test_stage5_openapi_contract_exposes_usage_and_execution_responses() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert "/api/v1/ai/conversations" in paths
    assert "/api/v1/ai/conversations/{conversation_id}" in paths
    assert "/api/v1/ai/conversations/{conversation_id}/messages" in paths
    assert "/api/v1/ai/usage" in paths

    submit = paths["/api/v1/ai/conversations/{conversation_id}/messages"]["post"]
    headers = {
        parameter["name"]: parameter
        for parameter in submit["parameters"]
        if parameter["in"] == "header"
    }
    assert headers["Idempotency-Key"]["required"] is True
    assert {"200", "201", "202", "402", "403", "409", "429", "502", "503"} <= set(
        submit["responses"]
    )


def test_stage4_migration_contract_is_linear_and_complete() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (project_root / "alembic" / "versions" / "0010_m5_ai_conversations.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0010_m5_ai_conversations"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0009_m5_ai_credit_ledger"' in migration
    assert '"ai_conversations"' in migration
    assert '"ai_messages"' in migration
    assert "fk_ai_messages_conversation_business" in migration
    assert "fk_ai_messages_credit_ledger_business" in migration
    assert "uq_ai_messages_business_idempotency" in migration
    assert "ck_ai_messages_provider_fields" in migration


def test_stage5_execution_migration_adds_recoverable_leases() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (project_root / "alembic" / "versions" / "0011_m5_ai_execution.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0011_m5_ai_execution"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0010_m5_ai_conversations"' in migration
    assert '"processing_token"' in migration
    assert '"processing_lease_expires_at"' in migration
    assert "ck_ai_messages_processing_lease" in migration
    assert "ix_ai_messages_business_pending_lease" in migration
