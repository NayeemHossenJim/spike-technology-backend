from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from google.genai import errors, types
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.services.gemini_gateway import (
    GeminiBlockedError,
    GeminiConfigurationError,
    GeminiGateway,
    GeminiGeneration,
    GeminiIncompleteResponseError,
    GeminiInvalidResponseError,
    GeminiRequestRejectedError,
    GeminiRequestValidationError,
    GeminiTemporaryError,
    GoogleGeminiGateway,
)


def gemini_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://spike:spike@localhost/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
        "email_backend": "console",
        "stripe_enabled": False,
        "gemini_enabled": True,
        "gemini_api_key": SecretStr("test-only-gemini-key"),
        "gemini_model": "gemini-3.6-flash",
        "gemini_request_timeout_seconds": 30,
        "gemini_max_output_tokens": 2048,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeModels:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    async def generate_content(
        self,
        *,
        model: str,
        contents: object,
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        self.calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        assert isinstance(self.outcome, types.GenerateContentResponse)
        return self.outcome


class FakeAsyncClient:
    def __init__(self, outcome: object) -> None:
        self.models = FakeModels(outcome)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, outcome: object) -> None:
        self.aio = FakeAsyncClient(outcome)
        self.closed = False

    def close(self) -> None:
        self.closed = True


@dataclass
class DeterministicGeminiGateway:
    responses: list[GeminiGeneration]
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    closed: bool = False

    async def generate(
        self,
        *,
        prompt: str,
        system_instruction: str | None = None,
    ) -> GeminiGeneration:
        self.calls.append((prompt, system_instruction))
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def successful_response(text: str = "A useful financial answer.") -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        response_id="response-unit-001",
        model_version="gemini-3.6-flash-2026-07",
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=text)],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=25,
            candidates_token_count=12,
            thoughts_token_count=4,
            total_token_count=41,
        ),
    )


def test_gemini_configuration_is_optional_but_fails_closed_when_enabled() -> None:
    disabled = gemini_settings(
        gemini_enabled=False,
        gemini_api_key=None,
    )
    assert disabled.gemini_enabled is False

    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        gemini_settings(gemini_api_key=None)

    with pytest.raises(ValidationError, match="GEMINI_API_KEY"):
        gemini_settings(gemini_api_key=SecretStr("   "))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"gemini_model": ""}, "GEMINI_MODEL"),
        ({"gemini_model": "invalid model"}, "GEMINI_MODEL"),
        ({"gemini_request_timeout_seconds": 4}, "between 5 and 300"),
        ({"gemini_request_timeout_seconds": 301}, "between 5 and 300"),
        ({"gemini_max_output_tokens": 127}, "between 128 and 65536"),
        ({"gemini_max_output_tokens": 65537}, "between 128 and 65536"),
    ],
)
def test_gemini_configuration_bounds_are_validated(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        gemini_settings(**overrides)


def test_gateway_fails_closed_without_configuration() -> None:
    gateway = GoogleGeminiGateway(
        gemini_settings(
            gemini_enabled=False,
            gemini_api_key=None,
        )
    )

    with pytest.raises(GeminiConfigurationError, match="not configured"):
        _ = gateway.client


@pytest.mark.asyncio
async def test_generate_maps_a_complete_text_response_and_usage() -> None:
    client = FakeClient(successful_response("  First line.\nSecond line.  "))
    gateway = GoogleGeminiGateway(gemini_settings(), client=client)

    generated = await gateway.generate(
        prompt="  Analyze this report.  ",
        system_instruction="  Be concise and accurate.  ",
    )

    assert generated == GeminiGeneration(
        text="First line.\nSecond line.",
        provider_response_id="response-unit-001",
        model_version="gemini-3.6-flash-2026-07",
        finish_reason="STOP",
        prompt_token_count=25,
        response_token_count=12,
        thoughts_token_count=4,
        total_token_count=41,
    )

    assert client.aio.models.calls
    call = client.aio.models.calls[0]
    assert call["model"] == "gemini-3.6-flash"
    assert call["contents"] == "Analyze this report."

    config = call["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.system_instruction == "Be concise and accurate."
    assert config.max_output_tokens == 2048
    assert config.response_mime_type == "text/plain"
    assert config.temperature is None
    assert config.top_p is None
    assert config.top_k is None
    assert config.candidate_count is None


@pytest.mark.asyncio
async def test_empty_prompt_is_rejected_before_provider_call() -> None:
    client = FakeClient(successful_response())
    gateway = GoogleGeminiGateway(gemini_settings(), client=client)

    with pytest.raises(GeminiRequestValidationError, match="cannot be empty"):
        await gateway.generate(prompt="   ")

    assert client.aio.models.calls == []


@pytest.mark.asyncio
async def test_prompt_feedback_block_is_classified() -> None:
    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY,
        )
    )
    gateway = GoogleGeminiGateway(gemini_settings(), client=FakeClient(response))

    with pytest.raises(GeminiBlockedError) as captured:
        await gateway.generate(prompt="blocked prompt")

    assert captured.value.reason == "SAFETY"


@pytest.mark.asyncio
async def test_candidate_safety_finish_is_classified() -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.PROHIBITED_CONTENT,
            )
        ]
    )
    gateway = GoogleGeminiGateway(gemini_settings(), client=FakeClient(response))

    with pytest.raises(GeminiBlockedError) as captured:
        await gateway.generate(prompt="provider blocked output")

    assert captured.value.reason == "PROHIBITED_CONTENT"


@pytest.mark.asyncio
async def test_max_tokens_is_not_counted_as_a_complete_response() -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Truncated response")],
                ),
                finish_reason=types.FinishReason.MAX_TOKENS,
            )
        ]
    )
    gateway = GoogleGeminiGateway(gemini_settings(), client=FakeClient(response))

    with pytest.raises(GeminiIncompleteResponseError) as captured:
        await gateway.generate(prompt="produce a full response")

    assert captured.value.finish_reason == "MAX_TOKENS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_error"),
    [
        (
            errors.ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "message": "quota exhausted",
                        "status": "RESOURCE_EXHAUSTED",
                    }
                },
            ),
            GeminiTemporaryError,
        ),
        (
            errors.ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "message": "unavailable",
                        "status": "UNAVAILABLE",
                    }
                },
            ),
            GeminiTemporaryError,
        ),
        (
            errors.ClientError(
                401,
                {
                    "error": {
                        "code": 401,
                        "message": "invalid credentials",
                        "status": "UNAUTHENTICATED",
                    }
                },
            ),
            GeminiConfigurationError,
        ),
        (
            errors.ClientError(
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "invalid request",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            ),
            GeminiRequestRejectedError,
        ),
        (TimeoutError(), GeminiTemporaryError),
        (OSError("network unavailable"), GeminiTemporaryError),
    ],
)
async def test_provider_failures_are_translated(
    outcome: BaseException,
    expected_error: type[Exception],
) -> None:
    gateway = GoogleGeminiGateway(gemini_settings(), client=FakeClient(outcome))

    with pytest.raises(expected_error):
        await gateway.generate(prompt="provider failure")


@pytest.mark.asyncio
async def test_missing_or_empty_candidate_is_invalid() -> None:
    no_candidate = GoogleGeminiGateway(
        gemini_settings(),
        client=FakeClient(types.GenerateContentResponse()),
    )
    with pytest.raises(GeminiInvalidResponseError, match="no response candidate"):
        await no_candidate.generate(prompt="missing candidate")

    empty_candidate = GoogleGeminiGateway(
        gemini_settings(),
        client=FakeClient(
            types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(role="model", parts=[]),
                        finish_reason=types.FinishReason.STOP,
                    )
                ]
            )
        ),
    )
    with pytest.raises(GeminiInvalidResponseError, match="empty text"):
        await empty_candidate.generate(prompt="empty candidate")


@pytest.mark.asyncio
async def test_gateway_closes_sync_and_async_sdk_clients() -> None:
    client = FakeClient(successful_response())
    gateway = GoogleGeminiGateway(gemini_settings(), client=client)

    await gateway.aclose()

    assert client.aio.closed is True
    assert client.closed is True
    assert gateway._client is None


@pytest.mark.asyncio
async def test_deterministic_test_double_satisfies_gateway_protocol() -> None:
    expected = GeminiGeneration(
        text="deterministic",
        provider_response_id=None,
        model_version="test-model",
        finish_reason="STOP",
        prompt_token_count=None,
        response_token_count=None,
        thoughts_token_count=None,
        total_token_count=None,
    )
    gateway = DeterministicGeminiGateway([expected])

    assert isinstance(gateway, GeminiGateway)
    assert (
        await gateway.generate(
            prompt="question",
            system_instruction="instruction",
        )
        == expected
    )
    assert gateway.calls == [("question", "instruction")]

    await gateway.aclose()
    assert gateway.closed is True
