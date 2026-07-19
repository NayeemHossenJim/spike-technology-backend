from __future__ import annotations

import logging

import pytest

from app.core.config import AppEnvironment, Settings
from app.services.email import ConsoleEmailSender


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
