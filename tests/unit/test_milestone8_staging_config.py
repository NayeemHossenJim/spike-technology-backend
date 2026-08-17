from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppEnvironment, Settings
from app.main import create_app


def staging_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_env": AppEnvironment.STAGING,
        "database_url": (
            "postgresql+asyncpg://spike:password@staging-postgres.internal:5432/spike"
        ),
        "redis_url": "rediss://staging-redis.internal:6379/0",
        "celery_broker_url": ("rediss://staging-redis.internal:6379/0"),
        "celery_result_backend": ("rediss://staging-redis.internal:6379/1"),
        "metrics_bearer_token": "m" * 48,
        "jwt_secret_key": "j" * 48,
        "frontend_url": "https://staging-app.example.com",
        "cors_origins": [
            "https://staging-app.example.com",
        ],
        "trusted_hosts": [
            "staging-api.example.com",
        ],
        "email_backend": "ses",
        "aws_region": "us-east-1",
        "ses_from_email": "staging@example.com",
        "s3_uploads_enabled": True,
        "s3_upload_bucket": "spike-private-staging",
        "stripe_enabled": True,
        "stripe_secret_key": "sk_test_stage_contract",
        "stripe_webhook_secret": "whsec_stage_contract",
        "stripe_premium_monthly_price_id": ("price_stage_premium"),
        "stripe_pro_monthly_price_id": ("price_stage_pro"),
        "stripe_checkout_success_url": (
            "https://staging-app.example.com/billing/success?session_id={CHECKOUT_SESSION_ID}"
        ),
        "stripe_checkout_cancel_url": ("https://staging-app.example.com/billing/cancel"),
        "gemini_enabled": True,
        "gemini_api_key": "staging-gemini-key",
    }

    values.update(overrides)

    return values


def build_staging(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        **staging_values(**overrides),
    )


def test_staging_environment_is_supported() -> None:
    settings = build_staging()

    assert settings.app_env is AppEnvironment.STAGING


def test_staging_rejects_localhost_database() -> None:
    with pytest.raises(
        ValidationError,
        match="DATABASE_URL cannot use localhost",
    ):
        build_staging(
            database_url=("postgresql+asyncpg://spike:password@localhost:5432/spike"),
        )


def test_staging_rejects_localhost_redis() -> None:
    with pytest.raises(
        ValidationError,
        match="REDIS_URL cannot use localhost",
    ):
        build_staging(
            redis_url="redis://localhost:6379/0",
        )


def test_staging_requires_https_frontend() -> None:
    with pytest.raises(
        ValidationError,
        match="FRONTEND_URL must use HTTPS",
    ):
        build_staging(
            frontend_url=("http://staging-app.example.com"),
        )


def test_staging_requires_https_stripe_redirect() -> None:
    with pytest.raises(
        ValidationError,
        match=("STRIPE_CHECKOUT_CANCEL_URL must use HTTPS"),
    ):
        build_staging(
            stripe_checkout_cancel_url=("http://staging-app.example.com/billing/cancel"),
        )


def test_staging_requires_private_upload_support() -> None:
    with pytest.raises(
        ValidationError,
        match="S3_UPLOADS_ENABLED must be true",
    ):
        build_staging(
            s3_uploads_enabled=False,
        )


def test_staging_requires_ses() -> None:
    with pytest.raises(
        ValidationError,
        match="EMAIL_BACKEND must be 'ses'",
    ):
        build_staging(
            email_backend="console",
        )


def test_staging_requires_stripe() -> None:
    with pytest.raises(
        ValidationError,
        match="STRIPE_ENABLED must be true",
    ):
        build_staging(
            stripe_enabled=False,
        )


def test_staging_requires_gemini() -> None:
    with pytest.raises(
        ValidationError,
        match="GEMINI_ENABLED must be true",
    ):
        build_staging(
            gemini_enabled=False,
        )


def test_staging_requires_stripe_test_key() -> None:
    with pytest.raises(
        ValidationError,
        match=("Non-production Stripe billing requires an sk_test_ secret key"),
    ):
        build_staging(
            stripe_secret_key=("sk_live_not_allowed_in_staging"),
        )


def test_production_still_requires_live_stripe_key() -> None:
    with pytest.raises(
        ValidationError,
        match=("Production Stripe billing requires an sk_live_ secret key"),
    ):
        Settings(
            _env_file=None,
            **staging_values(
                app_env=AppEnvironment.PRODUCTION,
                stripe_secret_key=("sk_test_not_allowed_in_production"),
            ),
        )


def test_openapi_contract_path_is_stable() -> None:
    app = create_app()

    assert app.openapi_url == "/api/v1/openapi.json"
    assert app.docs_url == "/docs"
    assert app.redoc_url is None
