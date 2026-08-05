from __future__ import annotations

import asyncio
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.models.ai import (
    AIConversation,
    AIMessage,
    AIMessageRole,
    AIMessageStatus,
)
from app.models.base import utc_now
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import onboard_owner


def ai_headers(token: str, idempotency_key: str) -> dict[str, str]:
    return {
        **bearer(token),
        "Idempotency-Key": idempotency_key,
    }


async def create_conversation(
    client: AsyncClient,
    *,
    token: str,
    title: str | None = None,
) -> dict:
    payload = {} if title is None else {"title": title}
    response = await client.post(
        "/api/v1/ai/conversations",
        headers=bearer(token),
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
async def test_conversation_and_pending_user_message_are_durable_and_idempotent(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-conversation@example.com",
        business_name="AI Conversation Tenant",
    )
    created = await create_conversation(
        client,
        token=token,
        title="  Quarterly performance  ",
    )
    assert created["business_id"] == onboarding["business"]["id"]
    assert created["created_by_user_id"] == onboarding["role_assignment"]["user_id"]
    assert created["title"] == "Quarterly performance"
    assert created["last_message_at"] is None

    raw_key = "ai-message-request-0001"
    submitted = await client.post(
        f"/api/v1/ai/conversations/{created['id']}/messages",
        headers=ai_headers(token, raw_key),
        json={"content": "  Explain the revenue trend.  "},
    )
    assert submitted.status_code == 201
    payload = submitted.json()
    assert payload["replayed"] is False
    assert payload["assistant_message"] is None
    assert payload["conversation"]["last_message_at"] is not None
    assert payload["user_message"]["role"] == "user"
    assert payload["user_message"]["status"] == "pending"
    assert payload["user_message"]["content"] == "Explain the revenue trend."
    assert payload["user_message"]["credit_ledger_entry_id"] is None
    assert payload["user_message"]["provider_model"] is None
    assert "idempotency_key_digest" not in payload["user_message"]

    replayed = await client.post(
        f"/api/v1/ai/conversations/{created['id']}/messages",
        headers=ai_headers(token, raw_key),
        json={"content": "Explain the revenue trend."},
    )
    assert replayed.status_code == 201
    assert replayed.json()["replayed"] is True
    assert replayed.json()["user_message"]["id"] == payload["user_message"]["id"]

    conversations = await client.get(
        "/api/v1/ai/conversations",
        headers=bearer(token),
    )
    assert conversations.status_code == 200
    assert conversations.json()["total"] == 1
    assert conversations.json()["items"][0]["id"] == created["id"]

    read = await client.get(
        f"/api/v1/ai/conversations/{created['id']}",
        headers=bearer(token),
    )
    assert read.status_code == 200
    assert read.json()["last_message_at"] == payload["conversation"]["last_message_at"]

    messages = await client.get(
        f"/api/v1/ai/conversations/{created['id']}/messages",
        headers=bearer(token),
    )
    assert messages.status_code == 200
    assert messages.json()["total"] == 1
    assert messages.json()["items"] == [payload["user_message"]]

    async with session_factory() as session:
        conversation = (await session.execute(select(AIConversation))).scalar_one()
        message = (await session.execute(select(AIMessage))).scalar_one()
        assert conversation.business_id == UUID(onboarding["business"]["id"])
        assert conversation.created_by_user_id == UUID(onboarding["role_assignment"]["user_id"])
        assert conversation.last_message_at is not None
        assert AIMessageRole(message.role) is AIMessageRole.USER
        assert AIMessageStatus(message.status) is AIMessageStatus.PENDING
        assert message.idempotency_key_digest == sha256(raw_key.encode()).hexdigest()
        assert message.credit_ledger_entry_id is None
        assert message.provider_response_id is None


@pytest.mark.integration
async def test_message_submission_validates_payload_and_detects_idempotency_conflicts(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-message-conflict@example.com",
        business_name="AI Message Conflict Tenant",
    )
    first = await create_conversation(client, token=token, title="First")
    second = await create_conversation(client, token=token, title="Second")
    key = "ai-message-conflict-0001"

    accepted = await client.post(
        f"/api/v1/ai/conversations/{first['id']}/messages",
        headers=ai_headers(token, key),
        json={"content": "Original content"},
    )
    assert accepted.status_code == 201

    changed_content = await client.post(
        f"/api/v1/ai/conversations/{first['id']}/messages",
        headers=ai_headers(token, key),
        json={"content": "Different content"},
    )
    changed_conversation = await client.post(
        f"/api/v1/ai/conversations/{second['id']}/messages",
        headers=ai_headers(token, key),
        json={"content": "Original content"},
    )
    assert changed_content.status_code == changed_conversation.status_code == 409
    assert (
        changed_content.json()
        == changed_conversation.json()
        == {"detail": "Idempotency-Key was already used for a different AI message."}
    )

    missing_key = await client.post(
        f"/api/v1/ai/conversations/{first['id']}/messages",
        headers=bearer(token),
        json={"content": "Missing key"},
    )
    blank = await client.post(
        f"/api/v1/ai/conversations/{first['id']}/messages",
        headers=ai_headers(token, "ai-message-blank-0001"),
        json={"content": "   "},
    )
    oversized = await client.post(
        f"/api/v1/ai/conversations/{first['id']}/messages",
        headers=ai_headers(token, "ai-message-large-0001"),
        json={"content": "x" * 12_001},
    )
    assert missing_key.status_code == blank.status_code == oversized.status_code == 422

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AIMessage)) == 1


@pytest.mark.integration
async def test_conversation_and_message_lists_are_paginated_in_stable_order(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-pagination@example.com",
        business_name="AI Pagination Tenant",
    )
    conversations = [
        await create_conversation(client, token=token, title=f"Conversation {index}")
        for index in range(3)
    ]

    base = utc_now()
    async with session_factory() as session:
        for index, payload in enumerate(conversations):
            conversation = await session.get(AIConversation, UUID(payload["id"]))
            assert conversation is not None
            conversation.updated_at = base + timedelta(seconds=index)
        await session.commit()

    page = await client.get(
        "/api/v1/ai/conversations?limit=2&offset=1",
        headers=bearer(token),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert page.json()["limit"] == 2
    assert page.json()["offset"] == 1
    assert [item["id"] for item in page.json()["items"]] == [
        conversations[1]["id"],
        conversations[0]["id"],
    ]

    message_ids: list[str] = []
    target_id = conversations[2]["id"]
    for index in range(3):
        response = await client.post(
            f"/api/v1/ai/conversations/{target_id}/messages",
            headers=ai_headers(token, f"ai-pagination-message-{index:04d}"),
            json={"content": f"Message {index}"},
        )
        assert response.status_code == 201
        message_ids.append(response.json()["user_message"]["id"])

    message_base = utc_now() + timedelta(seconds=10)
    async with session_factory() as session:
        for index, message_id in enumerate(message_ids):
            message = await session.get(AIMessage, UUID(message_id))
            assert message is not None
            message.created_at = message_base + timedelta(seconds=index)
            message.updated_at = message.created_at
        conversation = await session.get(AIConversation, UUID(target_id))
        assert conversation is not None
        conversation.last_message_at = message_base + timedelta(seconds=2)
        conversation.updated_at = conversation.last_message_at
        await session.commit()

    message_page = await client.get(
        f"/api/v1/ai/conversations/{target_id}/messages?limit=2&offset=1",
        headers=bearer(token),
    )
    assert message_page.status_code == 200
    assert message_page.json()["total"] == 3
    assert [item["id"] for item in message_page.json()["items"]] == message_ids[1:]


@pytest.mark.integration
async def test_conversations_and_messages_are_tenant_isolated_at_api_and_database(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
) -> None:
    first_token, first_onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-tenant-a@example.com",
        business_name="AI Tenant A",
    )
    second_token, second_onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-tenant-b@example.com",
        business_name="AI Tenant B",
    )
    conversation = await create_conversation(
        client,
        token=first_token,
        title="Tenant A conversation",
    )
    message = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(first_token, "ai-tenant-message-0001"),
        json={"content": "Tenant A private prompt"},
    )
    assert message.status_code == 201

    unknown_id = uuid4()
    cross_read = await client.get(
        f"/api/v1/ai/conversations/{conversation['id']}",
        headers=bearer(second_token),
    )
    unknown_read = await client.get(
        f"/api/v1/ai/conversations/{unknown_id}",
        headers=bearer(second_token),
    )
    cross_messages = await client.get(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=bearer(second_token),
    )
    cross_submit = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(second_token, "ai-cross-tenant-message-0001"),
        json={"content": "Cross-tenant attempt"},
    )
    assert cross_read.status_code == unknown_read.status_code == 404
    assert cross_read.json() == unknown_read.json() == {"detail": "AI conversation not found."}
    assert cross_messages.status_code == cross_submit.status_code == 404

    now = utc_now()
    first_business_id = UUID(first_onboarding["business"]["id"])
    second_business_id = UUID(second_onboarding["business"]["id"])
    second_user_id = UUID(second_onboarding["role_assignment"]["user_id"])

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(AIConversation).values(
                    id=uuid4(),
                    business_id=first_business_id,
                    created_by_user_id=second_user_id,
                    title="Invalid creator",
                    last_message_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(AIMessage).values(
                    id=uuid4(),
                    business_id=second_business_id,
                    conversation_id=UUID(conversation["id"]),
                    role=AIMessageRole.USER.value,
                    status=AIMessageStatus.PENDING.value,
                    content="Invalid conversation tenant",
                    created_by_user_id=second_user_id,
                    reply_to_message_id=None,
                    idempotency_key_digest=sha256(b"invalid-cross-tenant-message").hexdigest(),
                    credit_ledger_entry_id=None,
                    provider_response_id=None,
                    provider_model=None,
                    provider_finish_reason=None,
                    prompt_token_count=None,
                    response_token_count=None,
                    thoughts_token_count=None,
                    total_token_count=None,
                    completed_at=None,
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )


@pytest.mark.integration
async def test_database_rejects_assistant_messages_without_auditable_provider_linkage(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-assistant-contract@example.com",
        business_name="AI Assistant Contract Tenant",
    )
    conversation = await create_conversation(client, token=token)
    submitted = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-assistant-contract-0001"),
        json={"content": "Create the pending request"},
    )
    assert submitted.status_code == 201

    now = utc_now()
    business_id = UUID(onboarding["business"]["id"])
    user_message_id = UUID(submitted.json()["user_message"]["id"])

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(AIMessage).values(
                    id=uuid4(),
                    business_id=business_id,
                    conversation_id=UUID(conversation["id"]),
                    role=AIMessageRole.ASSISTANT.value,
                    status=AIMessageStatus.COMPLETED.value,
                    content="Unaudited assistant response",
                    created_by_user_id=None,
                    reply_to_message_id=user_message_id,
                    idempotency_key_digest=None,
                    credit_ledger_entry_id=None,
                    provider_response_id=None,
                    provider_model=None,
                    provider_finish_reason=None,
                    prompt_token_count=None,
                    response_token_count=None,
                    thoughts_token_count=None,
                    total_token_count=None,
                    completed_at=now,
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )


@pytest.mark.integration
async def test_concurrent_idempotent_message_submission_creates_exactly_one_row(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-concurrent-idempotency@example.com",
        business_name="AI Concurrent Idempotency Tenant",
    )
    conversation = await create_conversation(
        client,
        token=token,
        title="Concurrent idempotency",
    )
    key = "ai-concurrent-message-0001"

    async def submit():
        return await client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/messages",
            headers=ai_headers(token, key),
            json={"content": "Analyze the same request exactly once."},
        )

    first, second = await asyncio.gather(submit(), submit())

    assert first.status_code == second.status_code == 201

    payloads = [first.json(), second.json()]
    assert payloads[0]["user_message"]["id"] == payloads[1]["user_message"]["id"]
    assert sorted(payload["replayed"] for payload in payloads) == [False, True]

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AIMessage)) == 1


@pytest.mark.integration
async def test_pre_reservation_failure_can_be_recorded_without_credit_ledger_entry(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-pre-reservation-failure@example.com",
        business_name="AI Pre-Reservation Failure Tenant",
    )
    conversation = await create_conversation(
        client,
        token=token,
        title="Reservation failure",
    )
    submitted = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-pre-reservation-failure-0001"),
        json={"content": "This request will fail before credit reservation."},
    )
    assert submitted.status_code == 201

    message_id = UUID(submitted.json()["user_message"]["id"])

    async with session_factory() as session:
        message = await session.get(AIMessage, message_id)
        assert message is not None
        assert message.credit_ledger_entry_id is None

        message.status = AIMessageStatus.FAILED
        message.completed_at = utc_now()
        message.error_code = "credit_reservation_denied"
        await session.commit()

    async with session_factory() as session:
        failed = await session.get(AIMessage, message_id)
        assert failed is not None
        assert AIMessageStatus(failed.status) is AIMessageStatus.FAILED
        assert failed.credit_ledger_entry_id is None
        assert failed.completed_at is not None
        assert failed.error_code == "credit_reservation_denied"
