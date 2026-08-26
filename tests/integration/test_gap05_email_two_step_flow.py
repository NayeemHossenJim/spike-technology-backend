from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.models.auth import (
    EmailTwoStepChallenge,
    EmailTwoStepPurpose,
    RefreshToken,
)
from app.models.user import User
from tests.conftest import InMemoryEmailSender

CURRENT_PASSWORD = "CorrectHorseBattery9"
NEW_PASSWORD = "DifferentHorseBattery8"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_verify_and_login(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    remember_me: bool = True,
) -> tuple[UUID, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Email Verification User",
            "email": email,
            "password": CURRENT_PASSWORD,
        },
    )
    assert registration.status_code == 201
    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "otp": email_sender.verification_otps[-1][1]},
    )
    assert verification.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": CURRENT_PASSWORD,
            "remember_me": remember_me,
        },
    )
    assert login.status_code == 200
    return UUID(registration.json()["id"]), login.json()["access_token"]


async def enable_email_two_step(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    access_token: str,
) -> UUID:
    requested = await client.post(
        "/api/v1/users/me/email-two-step/enable",
        headers=bearer(access_token),
        json={"email": email, "current_password": CURRENT_PASSWORD},
    )
    assert requested.status_code == 202
    challenge_id = UUID(requested.json()["challenge_id"])
    recipient, otp, purpose = email_sender.email_two_step_otps[-1]
    assert recipient == email.lower()
    assert purpose is EmailTwoStepPurpose.ENABLE
    confirmed = await client.post(
        "/api/v1/users/me/email-two-step/enable/confirm",
        headers=bearer(access_token),
        json={"challenge_id": str(challenge_id), "otp": otp},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["enabled"] is True
    assert confirmed.json()["reauthentication_required"] is True
    assert email_sender.email_two_step_changes[-1] == (email.lower(), True)
    return challenge_id


async def start_email_two_step_login(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    password: str = CURRENT_PASSWORD,
    remember_me: bool = True,
) -> tuple[UUID, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "remember_me": remember_me,
        },
    )
    assert response.status_code == 202
    assert "access_token" not in response.json()
    assert "refresh_token" not in response.json()
    assert response.json()["purpose"] == "login"
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None
    challenge_id = UUID(response.json()["challenge_id"])
    recipient, otp, purpose = email_sender.email_two_step_otps[-1]
    assert recipient == email.lower()
    assert purpose is EmailTwoStepPurpose.LOGIN
    return challenge_id, otp


@pytest.mark.integration
async def test_email_two_step_enable_and_login_gate_issue_no_early_session(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    email = "two-step@example.com"
    user_id, access_token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    assert client.cookies.get(REFRESH_COOKIE_NAME)

    initial_status = await client.get(
        "/api/v1/users/me/email-two-step",
        headers=bearer(access_token),
    )
    assert initial_status.status_code == 200
    assert initial_status.json() == {
        "enabled": False,
        "enabled_at": None,
        "verification_method": "email_code",
        "masked_destination": "t******p@example.com",
        "reauthentication_required": False,
    }

    wrong_password = await client.post(
        "/api/v1/users/me/email-two-step/enable",
        headers=bearer(access_token),
        json={"email": email, "current_password": "IncorrectPassword9"},
    )
    assert wrong_password.status_code == 401
    wrong_email = await client.post(
        "/api/v1/users/me/email-two-step/enable",
        headers=bearer(access_token),
        json={"email": "other@example.com", "current_password": CURRENT_PASSWORD},
    )
    assert wrong_email.status_code == 409
    assert email_sender.email_two_step_otps == []

    requested = await client.post(
        "/api/v1/users/me/email-two-step/enable",
        headers=bearer(access_token),
        json={"email": "TWO-STEP@example.com", "current_password": CURRENT_PASSWORD},
    )
    assert requested.status_code == 202
    assert requested.json()["purpose"] == "enable"
    assert requested.json()["masked_destination"] == "t******p@example.com"
    assert "otp" not in requested.json()
    enable_challenge_id = UUID(requested.json()["challenge_id"])
    enable_otp = email_sender.email_two_step_otps[-1][1]

    cooldown_resend = await client.post(
        "/api/v1/users/me/email-two-step/resend",
        headers=bearer(access_token),
        json={"challenge_id": str(enable_challenge_id)},
    )
    assert cooldown_resend.status_code == 202
    assert UUID(cooldown_resend.json()["challenge_id"]) == enable_challenge_id
    assert len(email_sender.email_two_step_otps) == 1

    wrong_otp = "000000" if enable_otp != "000000" else "000001"
    rejected = await client.post(
        "/api/v1/users/me/email-two-step/enable/confirm",
        headers=bearer(access_token),
        json={"challenge_id": str(enable_challenge_id), "otp": wrong_otp},
    )
    assert rejected.status_code == 400

    confirmed = await client.post(
        "/api/v1/users/me/email-two-step/enable/confirm",
        headers=bearer(access_token),
        json={"challenge_id": str(enable_challenge_id), "otp": enable_otp},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["enabled"] is True
    assert confirmed.json()["enabled_at"] is not None
    assert confirmed.json()["reauthentication_required"] is True
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    invalidated_access = await client.get(
        "/api/v1/users/me",
        headers=bearer(access_token),
    )
    assert invalidated_access.status_code == 401

    login_challenge_id, login_otp = await start_email_two_step_login(
        client,
        email_sender,
        email=email,
    )
    wrong_login_otp = "000000" if login_otp != "000000" else "000001"
    wrong_login = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(login_challenge_id), "otp": wrong_login_otp},
    )
    assert wrong_login.status_code == 400
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    verified_login = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(login_challenge_id), "otp": login_otp},
    )
    assert verified_login.status_code == 200
    assert verified_login.json()["access_token"]
    assert client.cookies.get(REFRESH_COOKIE_NAME)

    replayed = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(login_challenge_id), "otp": login_otp},
    )
    assert replayed.status_code == 400

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.email_two_step_enabled is True
        assert user.email_two_step_enabled_at is not None
        assert user.auth_session_version == 1
        assert user.last_login_at is not None
        challenges = list(
            (
                await session.execute(
                    select(EmailTwoStepChallenge).where(EmailTwoStepChallenge.user_id == user_id)
                )
            ).scalars()
        )
        assert len(challenges) == 2
        assert all(challenge.used_at is not None for challenge in challenges)
        refresh_tokens = list(
            (
                await session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
            ).scalars()
        )
        assert len(refresh_tokens) == 2
        assert sum(token.revoked_at is not None for token in refresh_tokens) == 1
        assert sum(token.revoked_at is None for token in refresh_tokens) == 1


@pytest.mark.integration
async def test_email_two_step_resend_rotates_expired_cooldown_challenge(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    email = "resend-two-step@example.com"
    _, access_token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    await enable_email_two_step(
        client,
        email_sender,
        email=email,
        access_token=access_token,
    )
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    old_challenge_id, old_otp = await start_email_two_step_login(
        client,
        email_sender,
        email=email,
    )
    async with session_factory() as session:
        challenge = await session.get(EmailTwoStepChallenge, old_challenge_id)
        assert challenge is not None
        challenge.created_at -= timedelta(seconds=61)
        await session.commit()

    resent = await client.post(
        "/api/v1/auth/email-two-step/resend",
        json={"challenge_id": str(old_challenge_id)},
    )
    assert resent.status_code == 202
    new_challenge_id = UUID(resent.json()["challenge_id"])
    assert new_challenge_id != old_challenge_id
    new_otp = email_sender.email_two_step_otps[-1][1]
    assert len(email_sender.email_two_step_otps) == 3

    old_rejected = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(old_challenge_id), "otp": old_otp},
    )
    assert old_rejected.status_code == 400
    new_verified = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(new_challenge_id), "otp": new_otp},
    )
    assert new_verified.status_code == 200

    async with session_factory() as session:
        old_challenge = await session.get(EmailTwoStepChallenge, old_challenge_id)
        new_challenge = await session.get(EmailTwoStepChallenge, new_challenge_id)
        assert old_challenge is not None and old_challenge.used_at is not None
        assert new_challenge is not None and new_challenge.used_at is not None


@pytest.mark.integration
async def test_email_two_step_disable_reauthenticates_and_revokes_sessions(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    email = "disable-two-step@example.com"
    user_id, initial_access = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    await enable_email_two_step(
        client,
        email_sender,
        email=email,
        access_token=initial_access,
    )
    login_challenge_id, login_otp = await start_email_two_step_login(
        client,
        email_sender,
        email=email,
    )
    signed_in = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(login_challenge_id), "otp": login_otp},
    )
    assert signed_in.status_code == 200
    access_token = signed_in.json()["access_token"]

    wrong_password = await client.post(
        "/api/v1/users/me/email-two-step/disable",
        headers=bearer(access_token),
        json={"current_password": "IncorrectPassword9"},
    )
    assert wrong_password.status_code == 401

    requested = await client.post(
        "/api/v1/users/me/email-two-step/disable",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD},
    )
    assert requested.status_code == 202
    assert requested.json()["purpose"] == "disable"
    disable_challenge_id = UUID(requested.json()["challenge_id"])
    _, disable_otp, purpose = email_sender.email_two_step_otps[-1]
    assert purpose is EmailTwoStepPurpose.DISABLE

    disabled = await client.post(
        "/api/v1/users/me/email-two-step/disable/confirm",
        headers=bearer(access_token),
        json={"challenge_id": str(disable_challenge_id), "otp": disable_otp},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["enabled_at"] is None
    assert disabled.json()["reauthentication_required"] is True
    assert email_sender.email_two_step_changes[-1] == (email, False)
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    invalidated_access = await client.get(
        "/api/v1/users/me",
        headers=bearer(access_token),
    )
    assert invalidated_access.status_code == 401
    direct_login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": CURRENT_PASSWORD},
    )
    assert direct_login.status_code == 200
    assert direct_login.json()["access_token"]

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.email_two_step_enabled is False
        assert user.email_two_step_enabled_at is None
        assert user.auth_session_version == 2


@pytest.mark.integration
async def test_password_change_invalidates_pending_email_two_step_login(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
) -> None:
    email = "stale-two-step@example.com"
    _, initial_access = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    await enable_email_two_step(
        client,
        email_sender,
        email=email,
        access_token=initial_access,
    )

    first_challenge_id, first_otp = await start_email_two_step_login(
        client,
        email_sender,
        email=email,
    )
    verified = await client.post(
        "/api/v1/auth/email-two-step/verify",
        json={"challenge_id": str(first_challenge_id), "otp": first_otp},
    )
    assert verified.status_code == 200
    access_token = verified.json()["access_token"]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as pending_client:
        pending_id, pending_otp = await start_email_two_step_login(
            pending_client,
            email_sender,
            email=email,
        )

        changed = await client.put(
            "/api/v1/users/me/password",
            headers=bearer(access_token),
            json={
                "current_password": CURRENT_PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        assert changed.status_code == 200

        stale = await pending_client.post(
            "/api/v1/auth/email-two-step/verify",
            json={"challenge_id": str(pending_id), "otp": pending_otp},
        )
        assert stale.status_code == 400

        old_password = await pending_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": CURRENT_PASSWORD},
        )
        assert old_password.status_code == 401
        new_password = await pending_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": NEW_PASSWORD},
        )
        assert new_password.status_code == 202
        assert "access_token" not in new_password.json()


@pytest.mark.integration
async def test_authenticated_email_two_step_challenge_cannot_cross_accounts(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
) -> None:
    owner_email = "challenge-owner@example.com"
    _, owner_access = await register_verify_and_login(
        client,
        email_sender,
        email=owner_email,
    )
    requested = await client.post(
        "/api/v1/users/me/email-two-step/enable",
        headers=bearer(owner_access),
        json={"email": owner_email, "current_password": CURRENT_PASSWORD},
    )
    assert requested.status_code == 202
    owner_challenge_id = requested.json()["challenge_id"]
    owner_otp = email_sender.email_two_step_otps[-1][1]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as other_client:
        _, other_access = await register_verify_and_login(
            other_client,
            email_sender,
            email="other-challenge-user@example.com",
        )
        cross_account = await other_client.post(
            "/api/v1/users/me/email-two-step/enable/confirm",
            headers=bearer(other_access),
            json={"challenge_id": owner_challenge_id, "otp": owner_otp},
        )
        assert cross_account.status_code == 400

    owner_confirmed = await client.post(
        "/api/v1/users/me/email-two-step/enable/confirm",
        headers=bearer(owner_access),
        json={"challenge_id": owner_challenge_id, "otp": owner_otp},
    )
    assert owner_confirmed.status_code == 200
    assert owner_confirmed.json()["enabled"] is True
