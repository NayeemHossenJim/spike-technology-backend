from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import AppEnvironment, Settings
from app.services.email import ConsoleEmailSender, SesEmailSender


def make_settings() -> Settings:
    return Settings(
        app_env=AppEnvironment.DEVELOPMENT,
        database_url="postgresql+asyncpg://spike:password@localhost:5432/spike",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/0",
        celery_result_backend="redis://localhost:6379/1",
        jwt_secret_key="test-only-secret-with-at-least-thirty-two-characters",
        frontend_url="http://localhost:3000",
    )


@pytest.mark.asyncio
async def test_console_sender_logs_verification_otp(caplog: pytest.LogCaptureFixture) -> None:
    sender = ConsoleEmailSender(make_settings())

    with caplog.at_level(logging.INFO, logger="app.services.email"):
        await sender.send_verification_otp("test@example.com", "012345")

    assert "Development signup-verification OTP for test@example.com: 012345" in caplog.text


@pytest.mark.asyncio
async def test_ses_sender_builds_verification_email_without_network_access() -> None:
    settings = make_settings()
    settings.aws_region = "us-east-1"
    settings.ses_from_email = "no-reply@example.com"
    mock_client = MagicMock()

    with patch("app.services.email.boto3.client", return_value=mock_client):
        sender = SesEmailSender(settings)
        await sender.send_verification_otp("recipient@example.com", "012345")

    mock_client.send_email.assert_called_once()
    payload = mock_client.send_email.call_args.kwargs
    assert payload["FromEmailAddress"] == "no-reply@example.com"
    assert payload["Destination"] == {"ToAddresses": ["recipient@example.com"]}
    assert "012345" in payload["Content"]["Simple"]["Body"]["Text"]["Data"]
