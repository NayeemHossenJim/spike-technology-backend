from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.responses import Response

from app.core.config import AppEnvironment, Settings
from app.core.security import (
    TokenType,
    create_jwt_token,
    create_six_digit_otp,
    decode_jwt_token,
    hash_password,
    verify_password,
)
from app.core.session_cookie import REFRESH_COOKIE_NAME, set_refresh_cookie
from app.models.user import Industry, JobRole
from app.schemas.auth import EmailOTPRequest, LoginRequest, RegisterRequest


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


def test_signup_schema_strips_name_and_accepts_exact_figma_values() -> None:
    payload = RegisterRequest(
        full_name="  Test User  ",
        email="test@example.com",
        password="CorrectHorseBattery9",
        industry="Technology",
        job_role="Engineer / Developer",
    )
    assert payload.full_name == "Test User"
    assert payload.industry is Industry.TECHNOLOGY
    assert payload.job_role is JobRole.ENGINEER_DEVELOPER


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("industry", "Space Mining"),
        ("job_role", "Wizard"),
    ],
)
def test_signup_schema_rejects_values_not_present_in_figma(field: str, value: str) -> None:
    payload = {
        "full_name": "Test User",
        "email": "test@example.com",
        "password": "CorrectHorseBattery9",
        field: value,
    }
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(payload)


def test_signup_schema_rejects_whitespace_name_and_unknown_role_key() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(
            full_name="   ",
            email="test@example.com",
            password="CorrectHorseBattery9",
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RegisterRequest.model_validate(
            {
                "full_name": "Test User",
                "email": "test@example.com",
                "password": "CorrectHorseBattery9",
                "role": "Engineer / Developer",
            }
        )


def test_figma_dropdown_values_are_locked() -> None:
    assert [industry.value for industry in Industry] == [
        "General",
        "Finance & Banking",
        "Investment Management",
        "Insurance",
        "Accounting",
        "Agriculture",
        "Media & Entertainment",
        "Healthcare",
        "Transportation",
        "Manufacturing",
        "Construction",
        "Consultant",
        "Technology",
        "Government",
        "Marketing",
        "Other",
    ]
    assert [role.value for role in JobRole] == [
        "Executive / C-Suite",
        "Accountant",
        "Bookkeeper",
        "Controller",
        "Analyst",
        "Product Manager",
        "Engineer / Developer",
        "Marketer",
        "Manufacturer",
        "CFO",
        "Sales & RevOps",
        "Consultant",
        "Operations",
        "Financial Advisor",
        "Support",
    ]


def test_login_remember_me_defaults_to_session_cookie_mode() -> None:
    assert LoginRequest(email="test@example.com", password="password").remember_me is False


def test_persistent_refresh_cookie_has_approved_security_attributes() -> None:
    response = Response()
    set_refresh_cookie(
        response,
        refresh_token="opaque-token",
        remember_me=True,
        settings=make_settings(),
    )
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{REFRESH_COOKIE_NAME}=opaque-token")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "max-age=2592000" in cookie


def test_production_refresh_cookie_is_secure() -> None:
    settings = Settings(
        app_env=AppEnvironment.PRODUCTION,
        database_url="postgresql+asyncpg://spike:password@postgres:5432/spike",
        redis_url="redis://redis:6379/0",
        celery_broker_url="redis://redis:6379/0",
        celery_result_backend="redis://redis:6379/1",
        jwt_secret_key="this-is-a-long-enough-production-test-secret-value",
        email_backend="ses",
        aws_region="us-east-1",
        ses_from_email="no-reply@example.com",
    )
    response = Response()
    set_refresh_cookie(
        response,
        refresh_token="opaque-token",
        remember_me=False,
        settings=settings,
    )
    assert "secure" in response.headers["set-cookie"].lower()
