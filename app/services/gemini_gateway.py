from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai import errors, types

from app.core.config import Settings, get_settings

GEMINI_RETRYABLE_CLIENT_CODES = frozenset({408, 409, 425, 429})
GEMINI_CONFIGURATION_CLIENT_CODES = frozenset({401, 403})
GEMINI_BLOCKED_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)


class GeminiGatewayError(Exception):
    """Base error for Gemini gateway failures."""


class GeminiConfigurationError(GeminiGatewayError):
    """Gemini is disabled, missing credentials, or rejected operator configuration."""


class GeminiRequestValidationError(GeminiGatewayError, ValueError):
    """The application attempted to send an invalid generation request."""


class GeminiTemporaryError(GeminiGatewayError):
    """The provider request may succeed if retried later."""


class GeminiRequestRejectedError(GeminiGatewayError):
    """Gemini definitively rejected the request."""


class GeminiBlockedError(GeminiGatewayError):
    """Gemini blocked the prompt or generated candidate."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("Gemini blocked the request or response.")


class GeminiIncompleteResponseError(GeminiGatewayError):
    """Gemini stopped before producing a complete response."""

    def __init__(self, finish_reason: str) -> None:
        self.finish_reason = finish_reason
        super().__init__("Gemini did not produce a complete response.")


class GeminiInvalidResponseError(GeminiGatewayError):
    """Gemini returned a response that cannot be safely used."""


@dataclass(frozen=True, slots=True)
class GeminiGeneration:
    text: str
    provider_response_id: str | None
    model_version: str
    finish_reason: str
    prompt_token_count: int | None
    response_token_count: int | None
    thoughts_token_count: int | None
    total_token_count: int | None


@runtime_checkable
class GeminiGateway(Protocol):
    async def generate(
        self,
        *,
        prompt: str,
        system_instruction: str | None = None,
    ) -> GeminiGeneration: ...

    async def aclose(self) -> None: ...


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    raw_value = getattr(value, "value", value)
    return str(raw_value)


def _response_text(candidate: types.Candidate) -> str:
    content = candidate.content
    if content is None or not content.parts:
        return ""
    return "".join(part.text or "" for part in content.parts).strip()


def _token_count(usage: object | None, *names: str) -> int | None:
    if usage is None:
        return None
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int):
            return value
    return None


class GoogleGeminiGateway:
    """Narrow async adapter around the Google Gen AI SDK generateContent API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.settings.gemini_enabled or self.settings.gemini_api_key is None:
            raise GeminiConfigurationError("Gemini AI is not configured.")

        api_key = self.settings.gemini_api_key.get_secret_value().strip()
        if not api_key:
            raise GeminiConfigurationError("Gemini AI is not configured.")

        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=self.settings.gemini_request_timeout_seconds * 1000,
            ),
        )
        return self._client

    @staticmethod
    def _validate_prompt(prompt: str) -> str:
        normalized = prompt.strip()
        if not normalized:
            raise GeminiRequestValidationError("Gemini prompt cannot be empty.")
        return normalized

    @staticmethod
    def _normalize_system_instruction(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _parse_response(self, response: types.GenerateContentResponse) -> GeminiGeneration:
        prompt_feedback = response.prompt_feedback
        block_reason = (
            _enum_value(prompt_feedback.block_reason) if prompt_feedback is not None else None
        )
        if block_reason and block_reason != "BLOCKED_REASON_UNSPECIFIED":
            raise GeminiBlockedError(block_reason)

        candidates = response.candidates or []
        if not candidates:
            raise GeminiInvalidResponseError("Gemini returned no response candidate.")

        candidate = candidates[0]
        finish_reason = _enum_value(candidate.finish_reason)
        if finish_reason is None or finish_reason == "FINISH_REASON_UNSPECIFIED":
            raise GeminiInvalidResponseError("Gemini returned no valid finish reason.")
        if finish_reason in GEMINI_BLOCKED_FINISH_REASONS:
            raise GeminiBlockedError(finish_reason)
        if finish_reason != "STOP":
            raise GeminiIncompleteResponseError(finish_reason)

        text = _response_text(candidate)
        if not text:
            raise GeminiInvalidResponseError("Gemini returned an empty text response.")

        usage = response.usage_metadata
        return GeminiGeneration(
            text=text,
            provider_response_id=response.response_id,
            model_version=response.model_version or self.settings.gemini_model,
            finish_reason=finish_reason,
            prompt_token_count=_token_count(usage, "prompt_token_count"),
            response_token_count=_token_count(
                usage,
                "response_token_count",
                "candidates_token_count",
            ),
            thoughts_token_count=_token_count(usage, "thoughts_token_count"),
            total_token_count=_token_count(usage, "total_token_count"),
        )

    async def generate(
        self,
        *,
        prompt: str,
        system_instruction: str | None = None,
    ) -> GeminiGeneration:
        normalized_prompt = self._validate_prompt(prompt)
        normalized_instruction = self._normalize_system_instruction(system_instruction)
        client = self.client

        config = types.GenerateContentConfig(
            system_instruction=normalized_instruction,
            max_output_tokens=self.settings.gemini_max_output_tokens,
            response_mime_type="text/plain",
        )

        try:
            async with asyncio.timeout(self.settings.gemini_request_timeout_seconds):
                response = await client.aio.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=normalized_prompt,
                    config=config,
                )
        except TimeoutError as exc:
            raise GeminiTemporaryError("Gemini request timed out.") from exc
        except errors.ServerError as exc:
            raise GeminiTemporaryError("Gemini is temporarily unavailable.") from exc
        except errors.ClientError as exc:
            if exc.code in GEMINI_CONFIGURATION_CLIENT_CODES:
                raise GeminiConfigurationError(
                    "Gemini rejected the configured credentials or permissions."
                ) from exc
            if exc.code in GEMINI_RETRYABLE_CLIENT_CODES:
                raise GeminiTemporaryError("Gemini is temporarily unavailable.") from exc
            raise GeminiRequestRejectedError("Gemini rejected the generation request.") from exc
        except errors.APIError as exc:
            raise GeminiTemporaryError("Gemini could not complete the request.") from exc
        except errors.UnknownApiResponseError as exc:
            raise GeminiInvalidResponseError("Gemini returned an unreadable response.") from exc
        except OSError as exc:
            raise GeminiTemporaryError("Gemini could not be reached.") from exc

        return self._parse_response(response)

    async def aclose(self) -> None:
        if self._client is None:
            return

        client = self._client
        self._client = None
        try:
            await client.aio.aclose()
        finally:
            client.close()


@lru_cache
def get_gemini_gateway() -> GeminiGateway:
    return GoogleGeminiGateway(get_settings())


__all__ = [
    "GEMINI_BLOCKED_FINISH_REASONS",
    "GEMINI_CONFIGURATION_CLIENT_CODES",
    "GEMINI_RETRYABLE_CLIENT_CODES",
    "GeminiBlockedError",
    "GeminiConfigurationError",
    "GeminiGateway",
    "GeminiGatewayError",
    "GeminiGeneration",
    "GeminiIncompleteResponseError",
    "GeminiInvalidResponseError",
    "GeminiRequestRejectedError",
    "GeminiRequestValidationError",
    "GeminiTemporaryError",
    "GoogleGeminiGateway",
    "get_gemini_gateway",
]
