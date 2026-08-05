from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.api.deps import enforce_ai_rate_limit_dependency
from app.core.config import get_settings
from app.models.ai import (
    AICreditAccount,
    AICreditLedgerEntry,
    AICreditLedgerStatus,
    AIMessage,
    AIMessageRole,
    AIMessageStatus,
)
from app.models.base import utc_now
from app.models.subscription import Subscription, SubscriptionEntitlement
from app.models.user import User
from app.services.ai_conversations import AIConversationService
from app.services.ai_credits import AICreditService
from app.services.ai_execution import AIExecutionService
from app.services.gemini_gateway import (
    GeminiBlockedError,
    GeminiTemporaryError,
)
from app.services.tenant import TenantScope
from tests.conftest import InMemoryEmailSender, InMemoryGeminiGateway
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
async def test_ai_execution_consumes_one_credit_and_replays_without_provider_call(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-execution@example.com",
        business_name="AI Execution Tenant",
    )
    caplog.set_level(logging.INFO, logger="app.services.ai_execution")
    conversation = await create_conversation(
        client,
        token=token,
        title="  Quarterly performance  ",
    )

    request_id = "ai-request-correlation-0001"
    response = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers={
            **ai_headers(token, "ai-execution-message-0001"),
            "X-Request-ID": request_id,
        },
        json={"content": "  Explain the revenue trend.  "},
    )
    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == request_id

    payload = response.json()
    assert payload["replayed"] is False
    assert payload["user_message"]["status"] == "completed"
    assert payload["user_message"]["content"] == "Explain the revenue trend."
    assert payload["assistant_message"]["status"] == "completed"
    assert payload["assistant_message"]["role"] == "assistant"
    assert payload["assistant_message"]["provider_model"] == "gemini-test-model"
    assert payload["assistant_message"]["response_token_count"] == 8
    assert payload["usage"]["consumed_count"] == 1
    assert payload["usage"]["reserved_count"] == 0
    assert payload["usage"]["remaining"] == 14
    assert len(gemini_gateway.calls) == 1
    completed_records = [
        record
        for record in caplog.records
        if record.name == "app.services.ai_execution"
        and record.getMessage() == "ai_request_completed"
    ]
    assert completed_records
    assert completed_records[-1].request_id == request_id
    assert completed_records[-1].business_id == onboarding["business"]["id"]
    assert "Explain the revenue trend." not in caplog.text

    replay = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-execution-message-0001"),
        json={"content": "Explain the revenue trend."},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["user_message"]["id"] == payload["user_message"]["id"]
    assert replay.json()["assistant_message"]["id"] == payload["assistant_message"]["id"]
    assert len(gemini_gateway.calls) == 1

    messages = await client.get(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=bearer(token),
    )
    assert messages.status_code == 200
    assert messages.json()["total"] == 2
    assert [item["role"] for item in messages.json()["items"]] == [
        "user",
        "assistant",
    ]

    usage = await client.get("/api/v1/ai/usage", headers=bearer(token))
    assert usage.status_code == 200
    assert usage.json()["total"] == 1
    assert usage.json()["current"]["consumed_count"] == 1
    assert usage.json()["items"][0]["status"] == "consumed"

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        ledger = (await session.execute(select(AICreditLedgerEntry))).scalar_one()
        stored_messages = (
            (await session.execute(select(AIMessage).order_by(AIMessage.created_at, AIMessage.id)))
            .scalars()
            .all()
        )
        assert account.business_id == UUID(onboarding["business"]["id"])
        assert account.reserved_count == 0
        assert account.consumed_count == 1
        assert AICreditLedgerStatus(ledger.status) is AICreditLedgerStatus.CONSUMED
        assert [AIMessageStatus(item.status) for item in stored_messages] == [
            AIMessageStatus.COMPLETED,
            AIMessageStatus.COMPLETED,
        ]
        assert stored_messages[0].processing_token is None
        assert stored_messages[0].processing_lease_expires_at is None


@pytest.mark.integration
async def test_provider_failure_releases_credit_and_failed_replay_is_deterministic(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-provider-failure@example.com",
        business_name="AI Provider Failure Tenant",
    )
    conversation = await create_conversation(client, token=token)
    gemini_gateway.failure = GeminiTemporaryError("temporary")

    first = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-provider-failure-0001"),
        json={"content": "Analyze this request."},
    )
    replay = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-provider-failure-0001"),
        json={"content": "Analyze this request."},
    )

    assert first.status_code == replay.status_code == 503
    assert first.json() == replay.json() == {"detail": "AI service is temporarily unavailable."}
    assert len(gemini_gateway.calls) == 1

    async with session_factory() as session:
        user_message = (await session.execute(select(AIMessage))).scalar_one()
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        ledger = (await session.execute(select(AICreditLedgerEntry))).scalar_one()
        assert AIMessageRole(user_message.role) is AIMessageRole.USER
        assert AIMessageStatus(user_message.status) is AIMessageStatus.FAILED
        assert user_message.error_code == "provider_temporary"
        assert user_message.credit_ledger_entry_id == ledger.id
        assert account.reserved_count == 0
        assert account.consumed_count == 0
        assert AICreditLedgerStatus(ledger.status) is AICreditLedgerStatus.RELEASED
        assert ledger.release_reason == "provider_temporary"


@pytest.mark.integration
async def test_provider_safety_block_maps_to_422_and_releases_credit(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-provider-block@example.com",
        business_name="AI Provider Block Tenant",
    )
    conversation = await create_conversation(client, token=token)
    gemini_gateway.failure = GeminiBlockedError("SAFETY")

    response = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-provider-block-0001"),
        json={"content": "Provider-blocked request"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "The AI provider blocked this request."}

    async with session_factory() as session:
        ledger = (await session.execute(select(AICreditLedgerEntry))).scalar_one()
        assert AICreditLedgerStatus(ledger.status) is AICreditLedgerStatus.RELEASED
        assert ledger.release_reason == "provider_blocked"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_error"),
    [
        ("expired", 402, "subscription_inactive"),
        ("limit", 429, "credit_limit_reached"),
    ],
)
async def test_pre_reservation_failures_create_no_ledger_and_skip_provider(
    mode: str,
    expected_status: int,
    expected_error: str,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email=f"ai-pre-reservation-{mode}@example.com",
        business_name=f"AI Pre Reservation {mode}",
    )
    conversation = await create_conversation(client, token=token)
    subscription_id = UUID(onboarding["subscription"]["id"])

    async with session_factory() as session:
        if mode == "expired":
            subscription = await session.get(Subscription, subscription_id)
            assert subscription is not None
            subscription.trial_ends_at = utc_now() - timedelta(seconds=1)
        else:
            entitlement = (
                await session.execute(
                    select(SubscriptionEntitlement).where(
                        SubscriptionEntitlement.subscription_id == subscription_id
                    )
                )
            ).scalar_one()
            entitlement.limit_value = 0
        await session.commit()

    response = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, f"ai-pre-reservation-{mode}-0001"),
        json={"content": "This request cannot reserve credit."},
    )
    assert response.status_code == expected_status
    assert gemini_gateway.calls == []

    async with session_factory() as session:
        message = (await session.execute(select(AIMessage))).scalar_one()
        assert AIMessageStatus(message.status) is AIMessageStatus.FAILED
        assert message.error_code == expected_error
        assert message.credit_ledger_entry_id is None
        assert await session.scalar(select(func.count()).select_from(AICreditLedgerEntry)) == 0


@pytest.mark.integration
async def test_concurrent_idempotent_requests_make_one_provider_call(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-concurrent-execution@example.com",
        business_name="AI Concurrent Execution Tenant",
    )
    conversation = await create_conversation(client, token=token)
    gemini_gateway.block = True

    async def submit():
        return await client.post(
            f"/api/v1/ai/conversations/{conversation['id']}/messages",
            headers=ai_headers(token, "ai-concurrent-execution-0001"),
            json={"content": "Generate exactly one response."},
        )

    first_task = asyncio.create_task(submit())
    await asyncio.wait_for(gemini_gateway.started.wait(), timeout=5)
    second = await submit()

    assert second.status_code == 202
    assert second.json()["assistant_message"] is None
    assert second.json()["user_message"]["status"] == "pending"
    assert second.json()["usage"]["reserved_count"] == 1

    gemini_gateway.release.set()
    first = await asyncio.wait_for(first_task, timeout=5)
    assert first.status_code == 201

    replay = await submit()
    assert replay.status_code == 200
    assert replay.json()["assistant_message"]["id"] == first.json()["assistant_message"]["id"]
    assert len(gemini_gateway.calls) == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AIMessage)) == 2
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        assert account.reserved_count == 0
        assert account.consumed_count == 1


@pytest.mark.integration
async def test_usage_history_is_paginated_and_tenant_isolated(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
) -> None:
    first_token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-usage-a@example.com",
        business_name="AI Usage A",
    )
    second_token, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-usage-b@example.com",
        business_name="AI Usage B",
    )
    conversation = await create_conversation(client, token=first_token)

    success = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(first_token, "ai-usage-success-0001"),
        json={"content": "Successful usage"},
    )
    assert success.status_code == 201

    gemini_gateway.failure = GeminiTemporaryError("temporary")
    failure = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(first_token, "ai-usage-failure-0001"),
        json={"content": "Released usage"},
    )
    assert failure.status_code == 503

    first_page = await client.get(
        "/api/v1/ai/usage?limit=1&offset=0",
        headers=bearer(first_token),
    )
    second_page = await client.get(
        "/api/v1/ai/usage?limit=1&offset=1",
        headers=bearer(first_token),
    )
    other_tenant = await client.get(
        "/api/v1/ai/usage",
        headers=bearer(second_token),
    )

    assert first_page.status_code == second_page.status_code == other_tenant.status_code == 200
    assert first_page.json()["total"] == 2
    assert second_page.json()["total"] == 2
    assert {
        first_page.json()["items"][0]["status"],
        second_page.json()["items"][0]["status"],
    } == {"consumed", "released"}
    assert first_page.json()["current"]["consumed_count"] == 1
    assert first_page.json()["current"]["reserved_count"] == 0
    assert first_page.json()["current"]["remaining"] == 14
    assert other_tenant.json()["total"] == 0
    assert other_tenant.json()["current"]["remaining"] == 15


@pytest.mark.integration
async def test_real_redis_enforces_tenant_user_ai_rate_limit(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email=f"ai-rate-limit-{uuid4()}@example.com",
        business_name="AI Rate Limit Tenant",
    )
    conversation = await create_conversation(client, token=token)
    settings = get_settings()
    original_requests = settings.ai_rate_limit_requests
    original_window = settings.ai_rate_limit_window_seconds
    app.dependency_overrides.pop(enforce_ai_rate_limit_dependency, None)
    settings.ai_rate_limit_requests = 2
    settings.ai_rate_limit_window_seconds = 60

    try:
        responses = [
            await client.post(
                f"/api/v1/ai/conversations/{conversation['id']}/messages",
                headers=ai_headers(token, f"ai-rate-limit-{index:04d}"),
                json={"content": f"Rate-limited request {index}"},
            )
            for index in range(3)
        ]
    finally:
        settings.ai_rate_limit_requests = original_requests
        settings.ai_rate_limit_window_seconds = original_window

    assert [item.status_code for item in responses] == [201, 201, 429]
    assert responses[-1].headers["Retry-After"] == "60"
    assert responses[-1].json() == {"detail": "Too many AI requests. Please try again later."}

    replay = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, "ai-rate-limit-0000"),
        json={"content": "Rate-limited request 0"},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True


@pytest.mark.integration
async def test_provider_call_runs_after_database_transaction_is_closed(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-transaction-boundary@example.com",
        business_name="AI Transaction Boundary Tenant",
    )
    conversation = await create_conversation(client, token=token)
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    business_id = UUID(onboarding["business"]["id"])

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None

        class TransactionProbeGateway:
            calls = 0

            async def generate(self, *, prompt: str, system_instruction: str | None = None):
                self.calls += 1
                assert prompt
                assert system_instruction
                assert session.in_transaction() is False
                return await InMemoryGeminiGateway().generate(
                    prompt=prompt,
                    system_instruction=system_instruction,
                )

            async def aclose(self) -> None:
                return None

        gateway = TransactionProbeGateway()
        result = await AIExecutionService(
            session,
            gateway=gateway,
            settings=get_settings(),
        ).execute(
            TenantScope(business_id),
            user=user,
            conversation_id=UUID(conversation["id"]),
            content="Verify the provider transaction boundary.",
            idempotency_key="ai-transaction-boundary-0001",
            request_id="transaction-boundary-test",
        )

        assert result.assistant_message is not None
        assert gateway.calls == 1


@pytest.mark.integration
async def test_expired_execution_lease_is_reclaimed_without_an_extra_credit(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="ai-stale-lease@example.com",
        business_name="AI Stale Lease Tenant",
    )
    conversation = await create_conversation(client, token=token)
    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    scope = TenantScope(business_id)
    key = "ai-stale-lease-0001"

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        submission = await AIConversationService(session).submit_user_message(
            scope,
            user=user,
            conversation_id=UUID(conversation["id"]),
            content="Recover this expired execution lease.",
            idempotency_key=key,
        )
        reservation = await AICreditService(session).reserve(
            scope,
            idempotency_key=key,
        )
        message = await session.get(AIMessage, submission.user_message.id)
        assert message is not None
        message.credit_ledger_entry_id = reservation.entry_id
        message.processing_token = uuid4()
        message.processing_lease_expires_at = message.created_at
        await session.commit()

    recovered = await client.post(
        f"/api/v1/ai/conversations/{conversation['id']}/messages",
        headers=ai_headers(token, key),
        json={"content": "Recover this expired execution lease."},
    )

    assert recovered.status_code == 200
    assert recovered.json()["replayed"] is True
    assert recovered.json()["assistant_message"] is not None
    assert len(gemini_gateway.calls) == 1

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        ledger = (await session.execute(select(AICreditLedgerEntry))).scalar_one()
        assert account.reserved_count == 0
        assert account.consumed_count == 1
        assert AICreditLedgerStatus(ledger.status) is AICreditLedgerStatus.CONSUMED
