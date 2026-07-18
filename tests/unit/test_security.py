from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.config import AppEnvironment, Settings
from app.core.security import (
    TokenType,
    create_jwt_token,
    decode_jwt_token,
    hash_password,
    verify_password,
)


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
