from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.aws import build_aws_client_config
from app.core.config import Settings, get_settings
from app.db.session import create_database_engine, get_session
from app.main import create_app
from app.services.email import SesEmailSender
from app.services.redis import create_redis_client, get_redis
from app.services.s3_storage import Boto3S3UploadGateway


def infrastructure_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://spike:password@localhost:5432/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
        "email_backend": "console",
        "stripe_enabled": False,
        "s3_uploads_enabled": False,
        "aws_region": "us-east-1",
        "ses_from_email": "no-reply@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("db_pool_timeout_seconds", 0, "DB_POOL_TIMEOUT_SECONDS"),
        ("db_connect_timeout_seconds", 0, "DB_CONNECT_TIMEOUT_SECONDS"),
        (
            "redis_socket_connect_timeout_seconds",
            0,
            "REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS",
        ),
        ("redis_socket_timeout_seconds", 0, "REDIS_SOCKET_TIMEOUT_SECONDS"),
        ("health_check_timeout_seconds", 0, "HEALTH_CHECK_TIMEOUT_SECONDS"),
        ("aws_connect_timeout_seconds", 0, "AWS_CONNECT_TIMEOUT_SECONDS"),
        ("aws_read_timeout_seconds", 0, "AWS_READ_TIMEOUT_SECONDS"),
        ("aws_max_attempts", 0, "AWS_MAX_ATTEMPTS"),
    ],
)
def test_infrastructure_timeout_configuration_rejects_invalid_values(
    name: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        infrastructure_settings(**{name: value})


def test_database_engine_uses_bounded_pool_and_connect_timeouts() -> None:
    settings = infrastructure_settings(
        db_pool_size=7,
        db_max_overflow=9,
        db_pool_timeout_seconds=12,
        db_connect_timeout_seconds=8,
    )

    with patch("app.db.session.create_async_engine") as factory:
        create_database_engine(settings)

    factory.assert_called_once_with(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=7,
        max_overflow=9,
        pool_timeout=12,
        connect_args={"timeout": 8},
    )


def test_redis_client_uses_bounded_socket_timeouts() -> None:
    settings = infrastructure_settings(
        redis_socket_connect_timeout_seconds=4,
        redis_socket_timeout_seconds=6,
    )

    with patch("app.services.redis.from_url") as factory:
        create_redis_client(settings)

    factory.assert_called_once_with(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=4,
        socket_timeout=6,
        health_check_interval=30,
    )


def test_aws_client_policy_sets_timeouts_retries_and_signature() -> None:
    settings = infrastructure_settings(
        aws_connect_timeout_seconds=4,
        aws_read_timeout_seconds=25,
        aws_max_attempts=5,
    )

    config = build_aws_client_config(settings, signature_version="s3v4")

    assert config.connect_timeout == 4
    assert config.read_timeout == 25
    assert config.signature_version == "s3v4"
    assert config.retries["mode"] == "standard"
    assert config.retries["max_attempts"] == 5


def test_s3_gateway_uses_shared_aws_client_policy() -> None:
    settings = infrastructure_settings(
        s3_uploads_enabled=True,
        s3_upload_bucket="spike-private-test-uploads",
    )

    with patch("app.services.s3_storage.boto3.client") as factory:
        gateway = Boto3S3UploadGateway(settings)
        _ = gateway.client

    kwargs = factory.call_args.kwargs
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"].connect_timeout == settings.aws_connect_timeout_seconds
    assert kwargs["config"].read_timeout == settings.aws_read_timeout_seconds


def test_ses_sender_uses_shared_aws_client_policy() -> None:
    settings = infrastructure_settings(email_backend="ses")

    with patch("app.services.email.boto3.client") as factory:
        SesEmailSender(settings)

    kwargs = factory.call_args.kwargs
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["config"].connect_timeout == settings.aws_connect_timeout_seconds
    assert kwargs["config"].read_timeout == settings.aws_read_timeout_seconds


class SlowSession:
    async def execute(self, _statement) -> None:
        await asyncio.sleep(2)


class HealthyRedis:
    async def ping(self) -> bool:
        return True


async def override_slow_session():
    yield SlowSession()


@pytest.mark.asyncio
async def test_readiness_returns_503_when_dependency_exceeds_timeout() -> None:
    app = create_app()
    app.dependency_overrides[get_session] = override_slow_session
    app.dependency_overrides[get_redis] = lambda: HealthyRedis()
    app.dependency_overrides[get_settings] = lambda: infrastructure_settings(
        health_check_timeout_seconds=1
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "A required service is unavailable."}
