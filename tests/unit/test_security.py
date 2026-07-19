from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import AppEnvironment, Settings
from app.core.security import (
    TokenType,
    create_jwt_token,
    create_six_digit_otp,
    decode_jwt_token,
    hash_password,
    verify_password,
)
from app.schemas.auth import EmailOTPRequest


def make_settings() -> Settings:
    return Settings(
        app_env=AppEnvironment.TEST,
        database_url="postgresql+asyncpg://spike:password@localhost:5432/spike_test",
        redis_url="redis://localhost:6379/15",
        celery_broker_url="redis://localhost:6379/15",
        celery_result_backend="redis://localhost:6379/14",
        jwt_secret_key="test-only-secret-with-at-least-thirty-two-characters",
    )


def test_password_hash_round_trip() -> None:
    password = "CorrectHorseBattery9"
    assert verify_password(password, hash_password(password))
    assert not verify_password("not-the-password", hash_password(password))


def test_access_token_round_trip() -> None:
    settings = make_settings()
    user_id = uuid4()
    issued = create_jwt_token(
        user_id=user_id,
        token_type=TokenType.ACCESS,
        settings=settings,
        expires_delta=timedelta(minutes=15),
    )
    decoded = decode_jwt_token(issued.raw_token, settings)
    assert decoded.user_id == user_id
    assert decoded.token_id == issued.token_id
    assert decoded.token_type is TokenType.ACCESS


def test_production_rejects_console_email_backend() -> None:
    with pytest.raises(ValueError, match="EMAIL_BACKEND"):
        Settings(
            app_env=AppEnvironment.PRODUCTION,
            database_url="postgresql+asyncpg://spike:password@localhost:5432/spike",
            redis_url="redis://localhost:6379/0",
            celery_broker_url="redis://localhost:6379/0",
            celery_result_backend="redis://localhost:6379/1",
            jwt_secret_key="this-is-a-long-enough-production-test-secret-value",
            email_backend="console",
        )


def test_six_digit_otp_preserves_leading_zeroes() -> None:
    with patch("app.core.security.secrets.randbelow", return_value=42):
        assert create_six_digit_otp() == "000042"


@pytest.mark.parametrize("otp", ["12345", "1234567", "12a456"])
def test_otp_schema_requires_exactly_six_numeric_digits(otp: str) -> None:
    with pytest.raises(ValidationError):
        EmailOTPRequest(email="test@example.com", otp=otp)


def test_otp_schema_accepts_leading_zeroes() -> None:
    payload = EmailOTPRequest(email="test@example.com", otp="012345")
    assert payload.otp == "012345"
