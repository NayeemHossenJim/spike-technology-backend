from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "database_url": "postgresql+asyncpg://user:password@db.internal:5432/spike",
        "redis_url": "rediss://redis.internal:6379/0",
        "celery_broker_url": "rediss://redis.internal:6379/0",
        "celery_result_backend": "rediss://redis.internal:6379/1",
        "jwt_secret_key": "a" * 64,
        "frontend_url": "https://app.example.com",
        "cors_origins": ["https://app.example.com"],
        "trusted_hosts": ["api.example.com"],
        "email_backend": "ses",
        "aws_region": "us-east-1",
        "ses_from_email": "noreply@example.com",
        "s3_uploads_enabled": True,
        "s3_upload_bucket": "spike-production-reports",
        "stripe_enabled": False,
        "gemini_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_production_configuration_is_accepted() -> None:
    settings = production_settings()
    assert settings.app_env.value == "production"
    assert settings.log_level == "INFO"


@pytest.mark.parametrize("value", ["TRACE", "VERBOSE", "", "not-a-level"])
def test_invalid_log_level_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        production_settings(log_level=value)


def test_log_level_is_normalized() -> None:
    assert production_settings(log_level="warning").log_level == "WARNING"


def test_production_frontend_requires_https() -> None:
    with pytest.raises(ValidationError, match="FRONTEND_URL"):
        production_settings(frontend_url="http://app.example.com")


def test_production_cors_requires_https() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        production_settings(cors_origins=["http://app.example.com"])


def test_production_cors_rejects_localhost() -> None:
    with pytest.raises(ValidationError, match="Localhost CORS"):
        production_settings(cors_origins=["https://localhost"])


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "testserver"])
def test_production_rejects_development_trusted_hosts(host: str) -> None:
    with pytest.raises(ValidationError, match="Development trusted hosts"):
        production_settings(trusted_hosts=[host])


def test_production_database_requires_postgresql() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(database_url="sqlite+aiosqlite:///production.db")


@pytest.mark.parametrize(
    ("setting_name", "value"),
    [
        ("redis_url", "redis://localhost:6379/0"),
        ("celery_broker_url", "redis://127.0.0.1:6379/0"),
        ("celery_result_backend", "redis://localhost:6379/1"),
    ],
)
def test_production_redis_services_reject_localhost(
    setting_name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match=setting_name.upper()):
        production_settings(**{setting_name: value})


def test_production_requires_s3_uploads() -> None:
    with pytest.raises(ValidationError, match="S3_UPLOADS_ENABLED"):
        production_settings(s3_uploads_enabled=False)
