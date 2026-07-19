from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.security import utc_now
from app.models.auth import EmailVerificationOTP
from tests.conftest import InMemoryEmailSender


def assert_six_digit_otp(value: str) -> None:
    assert len(value) == 6
    assert value.isdigit()


async def make_verification_otp_resendable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        result = await session.execute(select(EmailVerificationOTP))
        otp_record = result.scalar_one()
        otp_record.created_at = utc_now() - timedelta(seconds=61)
        await session.commit()


async def get_verification_otps(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[EmailVerificationOTP]:
    async with session_factory() as session:
        result = await session.execute(
            select(EmailVerificationOTP).order_by(EmailVerificationOTP.created_at)
        )
        return list(result.scalars().all())


@pytest.mark.integration
async def test_register_resend_verify_login_refresh_logout(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Test User",
            "email": "test@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["is_verified"] is False
    assert len(email_sender.verification_otps) == 1
    first_otp = email_sender.verification_otps[0][1]
    assert_six_digit_otp(first_otp)

    unverified_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "CorrectHorseBattery9"},
    )
    assert unverified_login.status_code == 403

    cooldown_resend = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "test@example.com"}
    )
    assert cooldown_resend.status_code == 202
    assert len(email_sender.verification_otps) == 1

    await make_verification_otp_resendable(session_factory)
    resend = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "test@example.com"}
    )
    assert resend.status_code == 202
    assert len(email_sender.verification_otps) == 2
    verification_otp = email_sender.verification_otps[-1][1]
    assert_six_digit_otp(verification_otp)
    otp_records = await get_verification_otps(session_factory)
    assert otp_records[0].used_at is not None
    assert otp_records[1].used_at is None

    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": "test@example.com", "otp": verification_otp},
    )
    assert verification.status_code == 200

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "CorrectHorseBattery9"},
    )
    assert login.status_code == 200
    token_pair = login.json()

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token_pair['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "test@example.com"

    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token_pair["refresh_token"]},
    )
    assert refreshed.status_code == 200
    rotated_pair = refreshed.json()

    reused_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token_pair["refresh_token"]},
    )
    assert reused_refresh.status_code == 401

    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": rotated_pair["refresh_token"]},
    )
    assert logout.status_code == 202

    revoked_refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated_pair["refresh_token"]},
    )
    assert revoked_refresh.status_code == 401


@pytest.mark.integration
async def test_signup_otp_locks_after_five_wrong_attempts(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Lock Test",
            "email": "lock@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    otp = email_sender.verification_otps[0][1]
    wrong_otp = "000000" if otp != "000000" else "000001"

    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/verify-email",
            json={"email": "lock@example.com", "otp": wrong_otp},
        )
        assert response.status_code == 400

    locked_otp = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": "lock@example.com", "otp": otp},
    )
    assert locked_otp.status_code == 400


@pytest.mark.integration
async def test_expired_signup_otp_is_rejected(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Expired OTP",
            "email": "expired@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    otp = email_sender.verification_otps[0][1]
    async with session_factory() as session:
        result = await session.execute(select(EmailVerificationOTP))
        otp_record = result.scalar_one()
        otp_record.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    expired = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": "expired@example.com", "otp": otp},
    )
    assert expired.status_code == 400


@pytest.mark.integration
async def test_password_reset_uses_verified_otp_and_revokes_refresh_tokens(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Reset User",
            "email": "reset@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    verification_otp = email_sender.verification_otps[0][1]
    await client.post(
        "/api/v1/auth/verify-email",
        json={"email": "reset@example.com", "otp": verification_otp},
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "CorrectHorseBattery9"},
    )
    refresh_token = login.json()["refresh_token"]

    forgot = await client.post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"})
    assert forgot.status_code == 202
    assert len(email_sender.password_reset_otps) == 1
    reset_otp = email_sender.password_reset_otps[0][1]
    assert_six_digit_otp(reset_otp)

    cooldown_resend = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "reset@example.com"}
    )
    assert cooldown_resend.status_code == 202
    assert len(email_sender.password_reset_otps) == 1

    reset_without_verification = await client.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "reset@example.com",
            "otp": reset_otp,
            "new_password": "NewCorrectHorseBattery9",
        },
    )
    assert reset_without_verification.status_code == 400

    verification = await client.post(
        "/api/v1/auth/verify-password-reset-otp",
        json={"email": "reset@example.com", "otp": reset_otp},
    )
    assert verification.status_code == 200

    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "reset@example.com",
            "otp": reset_otp,
            "new_password": "NewCorrectHorseBattery9",
        },
    )
    assert reset.status_code == 200

    reused_otp = await client.post(
        "/api/v1/auth/reset-password",
        json={
            "email": "reset@example.com",
            "otp": reset_otp,
            "new_password": "AnotherCorrectPassword9",
        },
    )
    assert reused_otp.status_code == 400

    previous_session = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert previous_session.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "NewCorrectHorseBattery9"},
    )
    assert new_login.status_code == 200
