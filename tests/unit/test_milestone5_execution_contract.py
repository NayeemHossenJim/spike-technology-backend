from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import Settings
from app.models.ai import AIMessage, AIMessageRole, AIMessageStatus
from app.models.base import utc_now
from app.services.ai_execution import (
    AI_PROMPT_MAX_CHARACTERS,
    AIExecutionErrorCode,
    _bounded_prompt,
)
from app.services.rate_limit import enforce_ai_rate_limit


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://spike:spike@localhost/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
        "email_backend": "console",
        "stripe_enabled": False,
        "gemini_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_ai_execution_error_codes_are_stable_and_non_sensitive() -> None:
    assert [item.value for item in AIExecutionErrorCode] == [
        "subscription_required",
        "subscription_inactive",
        "entitlement_denied",
        "credit_limit_reached",
        "provider_configuration",
        "provider_temporary",
        "provider_rejected",
        "provider_blocked",
        "provider_incomplete",
        "provider_invalid",
        "response_too_large",
        "persistence_failure",
        "execution_integrity",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ai_rate_limit_requests": 0}, "AI_RATE_LIMIT_REQUESTS"),
        ({"ai_rate_limit_window_seconds": 0}, "AI_RATE_LIMIT_WINDOW_SECONDS"),
        ({"ai_rate_limit_window_seconds": 3601}, "AI_RATE_LIMIT_WINDOW_SECONDS"),
    ],
)
def test_ai_rate_limit_configuration_is_bounded(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**overrides)


def test_bounded_prompt_keeps_recent_context_without_exceeding_limit() -> None:
    now = utc_now()
    conversation_id = uuid4()
    business_id = uuid4()
    messages = [
        AIMessage(
            business_id=business_id,
            conversation_id=conversation_id,
            role=AIMessageRole.USER if index % 2 == 0 else AIMessageRole.ASSISTANT,
            status=AIMessageStatus.COMPLETED,
            content=f"message-{index}-" + ("x" * 4000),
            created_by_user_id=uuid4() if index % 2 == 0 else None,
            reply_to_message_id=None if index % 2 == 0 else uuid4(),
            idempotency_key_digest=("a" * 64) if index % 2 == 0 else None,
            credit_ledger_entry_id=uuid4(),
            completed_at=now + timedelta(seconds=index),
            created_at=now + timedelta(seconds=index),
            updated_at=now + timedelta(seconds=index),
        )
        for index in range(12)
    ]

    prompt = _bounded_prompt(messages)

    assert len(prompt) <= AI_PROMPT_MAX_CHARACTERS
    assert "message-11-" in prompt
    assert "message-0-" not in prompt
    assert "User:" in prompt
    assert "Assistant:" in prompt


@pytest.mark.asyncio
async def test_ai_rate_limit_is_scoped_and_returns_retry_after() -> None:
    redis = FakeRedis()
    configured = settings(
        ai_rate_limit_requests=2,
        ai_rate_limit_window_seconds=45,
    )
    business_id = uuid4()
    user_id = uuid4()

    await enforce_ai_rate_limit(
        redis=redis,
        settings=configured,
        business_id=business_id,
        user_id=user_id,
    )
    await enforce_ai_rate_limit(
        redis=redis,
        settings=configured,
        business_id=business_id,
        user_id=user_id,
    )

    with pytest.raises(HTTPException) as captured:
        await enforce_ai_rate_limit(
            redis=redis,
            settings=configured,
            business_id=business_id,
            user_id=user_id,
        )

    assert captured.value.status_code == 429
    assert captured.value.headers == {"Retry-After": "45"}
    assert len(redis.values) == 1
    assert next(iter(redis.expirations.values())) == 45

    await enforce_ai_rate_limit(
        redis=redis,
        settings=configured,
        business_id=uuid4(),
        user_id=user_id,
    )
    assert len(redis.values) == 2
