from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppEnvironment, Settings
from app.schemas.auth import RegisterRequest
from app.services.auth import AuthService, DuplicateEmailError


class NoopEmailSender:
    async def send_verification_otp(self, recipient: str, otp: str) -> None:
        raise AssertionError("No email should be sent after a failed registration flush")

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None:
        raise AssertionError("Password reset is not part of this test")


def make_settings() -> Settings:
    return Settings(
        app_env=AppEnvironment.TEST,
        database_url="postgresql+asyncpg://spike:password@localhost:5432/spike_test",
        redis_url="redis://localhost:6379/15",
        celery_broker_url="redis://localhost:6379/15",
        celery_result_backend="redis://localhost:6379/14",
        jwt_secret_key="test-only-secret-with-at-least-thirty-two-characters",
    )


@pytest.mark.asyncio
async def test_register_translates_flush_unique_violation_to_duplicate_email() -> None:
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT INTO users ...",
            {},
            RuntimeError("duplicate key value violates unique constraint"),
        )
    )
    session.rollback = AsyncMock()
    session.commit = AsyncMock()

    service = AuthService(
        session=session,
        settings=make_settings(),
        email_sender=NoopEmailSender(),
    )
    service._get_user_by_email = AsyncMock(return_value=None)  # type: ignore[method-assign]

    payload = RegisterRequest(
        full_name="Concurrent User",
        email="same@example.com",
        password="CorrectHorseBattery9",
    )

    with pytest.raises(DuplicateEmailError):
        await service.register(payload)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
