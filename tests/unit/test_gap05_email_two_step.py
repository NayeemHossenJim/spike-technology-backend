from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.deps import enforce_auth_rate_limit_dependency, get_auth_service
from app.core.config import AppEnvironment, Settings
from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.main import create_app
from app.models.auth import EmailTwoStepChallenge, EmailTwoStepPurpose
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    EmailTwoStepChallengeResponse,
    LoginRequest,
    ResendEmailTwoStepLoginRequest,
    VerifyEmailTwoStepLoginRequest,
)
from app.schemas.user import (
    UserEmailTwoStepConfirmRequest,
    UserEmailTwoStepEnableRequest,
    UserEmailTwoStepStatusResponse,
)
from app.services.auth import SessionTokens
from app.services.email_two_step import mask_email


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://spike:password@localhost:5432/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
    }
    values.update(overrides)
    return Settings(**values)


def test_figma_email_two_step_contract_is_strict_and_masked() -> None:
    enable = UserEmailTwoStepEnableRequest.model_validate(
        {
            "email": "USER@example.com",
            "current_password": "CorrectHorseBattery9",
        }
    )
    assert str(enable.email) == "USER@example.com"
    assert mask_email("user@example.com") == "u**r@example.com"
    assert mask_email("a@example.com") == "a*@example.com"
    assert mask_email("not-an-email") == "***"

    challenge_id = uuid4()
    confirmed = UserEmailTwoStepConfirmRequest.model_validate(
        {"challenge_id": challenge_id, "otp": "012345"}
    )
    assert confirmed.challenge_id == challenge_id
    assert confirmed.otp == "012345"

    with pytest.raises(ValidationError):
        UserEmailTwoStepEnableRequest.model_validate(
            {
                "email": "user@example.com",
                "current_password": "CorrectHorseBattery9",
                "enabled": True,
            }
        )
    with pytest.raises(ValidationError):
        UserEmailTwoStepConfirmRequest.model_validate(
            {"challenge_id": challenge_id, "otp": "12345"}
        )


def test_email_two_step_challenge_model_binds_security_state() -> None:
    now = datetime.now(UTC)
    challenge = EmailTwoStepChallenge(
        user_id=uuid4(),
        purpose=EmailTwoStepPurpose.LOGIN,
        otp_hash="hashed",
        expires_at=now + timedelta(minutes=10),
        session_version=7,
        remember_me=True,
    )
    assert challenge.purpose is EmailTwoStepPurpose.LOGIN
    assert challenge.attempt_count == 0
    assert challenge.session_version == 7
    assert challenge.remember_me is True
    assert challenge.used_at is None
    challenge_constraints = {
        constraint.name for constraint in EmailTwoStepChallenge.__table__.constraints
    }
    assert {
        "ck_email_two_step_challenges_purpose",
        "ck_email_two_step_challenges_attempt_count",
        "ck_email_two_step_challenges_session_version",
    } <= challenge_constraints
    assert "ck_users_email_two_step_state" in {
        constraint.name for constraint in User.__table__.constraints
    }


@pytest.mark.parametrize("minutes", [0, 11, -1])
def test_email_two_step_expiry_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError, match="EMAIL_TWO_STEP_EXPIRE_MINUTES"):
        make_settings(email_two_step_expire_minutes=minutes)


def test_email_two_step_public_status_does_not_claim_strong_mfa() -> None:
    status = UserEmailTwoStepStatusResponse(
        enabled=True,
        enabled_at=datetime.now(UTC),
        masked_destination="u**r@example.com",
    )
    payload = status.model_dump(mode="json")
    assert payload["verification_method"] == "email_code"
    assert "mfa" not in payload
    assert "two_factor" not in payload


class ChallengeAuthService:
    def __init__(self) -> None:
        self.challenge_id = uuid4()

    async def login(self, _payload: LoginRequest) -> EmailTwoStepChallengeResponse:
        return EmailTwoStepChallengeResponse(
            challenge_id=self.challenge_id,
            purpose=EmailTwoStepPurpose.LOGIN,
            masked_destination="u**r@example.com",
            expires_in=600,
        )

    async def verify_email_two_step_login(
        self,
        payload: VerifyEmailTwoStepLoginRequest,
    ) -> SessionTokens:
        assert payload.challenge_id == self.challenge_id
        assert payload.otp == "012345"
        return SessionTokens(
            access=AccessTokenResponse(
                access_token="access-after-email-code",
                access_token_expires_in=900,
            ),
            refresh_token="refresh-after-email-code",
            remember_me=True,
        )

    async def resend_email_two_step_login(
        self,
        payload: ResendEmailTwoStepLoginRequest,
    ) -> EmailTwoStepChallengeResponse:
        assert payload.challenge_id == self.challenge_id
        return EmailTwoStepChallengeResponse(
            challenge_id=self.challenge_id,
            purpose=EmailTwoStepPurpose.LOGIN,
            masked_destination="u**r@example.com",
            expires_in=500,
        )


async def no_rate_limit() -> None:
    return None


@pytest.mark.asyncio
async def test_login_challenge_issues_no_session_until_code_is_verified() -> None:
    app = create_app()
    service = ChallengeAuthService()
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[enforce_auth_rate_limit_dependency] = no_rate_limit

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "user@example.com",
                "password": "CorrectHorseBattery9",
                "remember_me": True,
            },
        )
        assert login.status_code == 202
        assert login.json() == {
            "verification_required": True,
            "challenge_id": str(service.challenge_id),
            "purpose": "login",
            "delivery_method": "email",
            "masked_destination": "u**r@example.com",
            "expires_in": 600,
        }
        assert "access_token" not in login.json()
        assert "set-cookie" not in login.headers
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None

        resent = await client.post(
            "/api/v1/auth/email-two-step/resend",
            json={"challenge_id": str(service.challenge_id)},
        )
        assert resent.status_code == 202
        assert resent.json()["challenge_id"] == str(service.challenge_id)
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None

        verified = await client.post(
            "/api/v1/auth/email-two-step/verify",
            json={
                "challenge_id": str(service.challenge_id),
                "otp": "012345",
            },
        )
        assert verified.status_code == 200
        assert verified.json()["access_token"] == "access-after-email-code"
        assert client.cookies.get(REFRESH_COOKIE_NAME) == "refresh-after-email-code"
        assert "httponly" in verified.headers["set-cookie"].lower()


def test_gap05_openapi_contract_exposes_each_backend_state() -> None:
    schema = create_app().openapi()
    expected_paths = {
        "/api/v1/auth/email-two-step/verify",
        "/api/v1/auth/email-two-step/resend",
        "/api/v1/users/me/email-two-step",
        "/api/v1/users/me/email-two-step/enable",
        "/api/v1/users/me/email-two-step/enable/confirm",
        "/api/v1/users/me/email-two-step/disable",
        "/api/v1/users/me/email-two-step/disable/confirm",
        "/api/v1/users/me/email-two-step/resend",
    }
    assert expected_paths <= set(schema["paths"])
    assert {"200", "202", "422"} <= set(schema["paths"]["/api/v1/auth/login"]["post"]["responses"])

    challenge_schema = schema["components"]["schemas"]["EmailTwoStepChallengeResponse"]
    assert "access_token" not in challenge_schema["properties"]
    assert "refresh_token" not in challenge_schema["properties"]
    assert challenge_schema["properties"]["delivery_method"]["const"] == "email"
