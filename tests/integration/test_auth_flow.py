from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import timedelta

import pytest
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient
from redis.asyncio import from_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.config import get_settings
from app.core.security import utc_now
from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.models.auth import EmailVerificationOTP, PasswordResetOTP, RefreshToken
from app.models.user import JobRole, User
from app.services.rate_limit import enforce_auth_rate_limit
from app.services.redis import close_redis
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


async def register_and_verify(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Test User",
            "email": email,
            "password": "CorrectHorseBattery9",
            "industry": "Technology",
            "job_role": "Engineer / Developer",
        },
    )
    assert registration.status_code == 201
    otp = email_sender.verification_otps[-1][1]
    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "otp": otp},
    )
    assert verification.status_code == 200


@pytest.mark.integration
async def test_register_resend_verify_login_refresh_logout(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Test User",
            "email": "test@example.com",
            "password": "CorrectHorseBattery9",
            "industry": "Technology",
            "job_role": "Engineer / Developer",
        },
    )
    assert registration.status_code == 201
    assert registration.json()["is_verified"] is False
    assert registration.json()["industry"] == "Technology"
    assert registration.json()["job_role"] == "Engineer / Developer"
    assert len(email_sender.verification_otps) == 1
    first_otp = email_sender.verification_otps[0][1]
    assert_six_digit_otp(first_otp)

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "test@example.com"))
        user = result.scalar_one()
        assert user.job_role == JobRole.ENGINEER_DEVELOPER
        assert user.terms_accepted_at is not None
        assert user.terms_version == "v1"

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
        json={
            "email": "test@example.com",
            "password": "CorrectHorseBattery9",
            "remember_me": False,
        },
    )
    assert login.status_code == 200
    access = login.json()
    assert "refresh_token" not in access
    session_cookie = login.headers["set-cookie"].lower()
    assert "httponly" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "max-age" not in session_cookie
    first_refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert first_refresh_token

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "test@example.com"

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert "refresh_token" not in refreshed.json()
    rotated_refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert rotated_refresh_token
    assert rotated_refresh_token != first_refresh_token

    replay_transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=replay_transport,
        base_url="http://testserver",
    ) as replay_client:
        reused_refresh = await replay_client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}={first_refresh_token}"},
        )
        assert reused_refresh.status_code == 401
        assert "max-age=0" in reused_refresh.headers["set-cookie"].lower()

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 202
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as replay_client:
        revoked_refresh = await replay_client.post(
            "/api/v1/auth/refresh",
            headers={"Cookie": f"{REFRESH_COOKIE_NAME}={rotated_refresh_token}"},
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
    assert login.status_code == 200
    refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert refresh_token

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

    previous_session = await client.post("/api/v1/auth/refresh")
    assert previous_session.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "NewCorrectHorseBattery9"},
    )
    assert new_login.status_code == 200


@pytest.mark.integration
async def test_remember_me_persists_and_survives_refresh_rotation(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await register_and_verify(client, email_sender, email="remember@example.com")

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "remember@example.com",
            "password": "CorrectHorseBattery9",
            "remember_me": True,
        },
    )
    assert login.status_code == 200
    cookie_header = login.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "max-age=2592000" in cookie_header

    first_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert first_token
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert "max-age=2592000" in refreshed.headers["set-cookie"].lower()
    assert client.cookies.get(REFRESH_COOKIE_NAME) != first_token

    async with session_factory() as session:
        result = await session.execute(
            select(RefreshToken).order_by(RefreshToken.created_at.desc()).limit(1)
        )
        assert result.scalar_one().is_persistent is True


@pytest.mark.integration
async def test_concurrent_refresh_allows_only_one_rotation(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
) -> None:
    await register_and_verify(client, email_sender, email="rotation@example.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "rotation@example.com",
            "password": "CorrectHorseBattery9",
            "remember_me": True,
        },
    )
    assert login.status_code == 200
    refresh_token = client.cookies.get(REFRESH_COOKIE_NAME)
    assert refresh_token

    async def rotate_once() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as concurrent_client:
            response = await concurrent_client.post(
                "/api/v1/auth/refresh",
                headers={"Cookie": f"{REFRESH_COOKIE_NAME}={refresh_token}"},
            )
            return response.status_code

    statuses = await asyncio.gather(rotate_once(), rotate_once())
    assert sorted(statuses) == [200, 401]


@pytest.mark.integration
async def test_concurrent_resend_creates_one_replacement_otp(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Concurrent User",
            "email": "concurrent@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    assert registration.status_code == 201
    await make_verification_otp_resendable(session_factory)

    async def resend_once() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as concurrent_client:
            response = await concurrent_client.post(
                "/api/v1/auth/resend-verification",
                json={"email": "concurrent@example.com"},
            )
            return response.status_code

    statuses = await asyncio.gather(resend_once(), resend_once())
    assert statuses == [202, 202]
    assert len(email_sender.verification_otps) == 2

    records = await get_verification_otps(session_factory)
    assert sum(record.used_at is None for record in records) == 1


@pytest.mark.integration
async def test_verification_and_resend_are_serialized_without_deadlock(
    client: AsyncClient,
    app,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Race Test",
            "email": "verify-race@example.com",
            "password": "CorrectHorseBattery9",
        },
    )
    assert registration.status_code == 201
    original_otp = email_sender.verification_otps[-1][1]
    await make_verification_otp_resendable(session_factory)

    async def verify_once() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as concurrent_client:
            response = await concurrent_client.post(
                "/api/v1/auth/verify-email",
                json={"email": "verify-race@example.com", "otp": original_otp},
            )
            return response.status_code

    async def resend_once() -> int:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as concurrent_client:
            response = await concurrent_client.post(
                "/api/v1/auth/resend-verification",
                json={"email": "verify-race@example.com"},
            )
            return response.status_code

    verification_status, resend_status = await asyncio.gather(verify_once(), resend_once())
    assert resend_status == 202
    assert verification_status in {200, 400}

    if verification_status == 200:
        assert len(email_sender.verification_otps) == 1
    else:
        assert len(email_sender.verification_otps) == 2
        replacement_otp = email_sender.verification_otps[-1][1]
        replacement_verification = await client.post(
            "/api/v1/auth/verify-email",
            json={"email": "verify-race@example.com", "otp": replacement_otp},
        )
        assert replacement_verification.status_code == 200


@pytest.mark.integration
async def test_password_reset_otp_locks_after_five_wrong_attempts(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    await register_and_verify(client, email_sender, email="reset-lock@example.com")
    await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "reset-lock@example.com"},
    )
    otp = email_sender.password_reset_otps[-1][1]
    wrong_otp = "000000" if otp != "000000" else "000001"

    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/verify-password-reset-otp",
            json={"email": "reset-lock@example.com", "otp": wrong_otp},
        )
        assert response.status_code == 400

    locked = await client.post(
        "/api/v1/auth/verify-password-reset-otp",
        json={"email": "reset-lock@example.com", "otp": otp},
    )
    assert locked.status_code == 400


@pytest.mark.integration
async def test_expired_password_reset_otp_is_rejected(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await register_and_verify(client, email_sender, email="reset-expired@example.com")
    await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "reset-expired@example.com"},
    )
    otp = email_sender.password_reset_otps[-1][1]
    async with session_factory() as session:
        result = await session.execute(select(PasswordResetOTP))
        record = result.scalar_one()
        record.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    expired = await client.post(
        "/api/v1/auth/verify-password-reset-otp",
        json={"email": "reset-expired@example.com", "otp": otp},
    )
    assert expired.status_code == 400


@pytest.mark.integration
async def test_real_postgres_and_redis_readiness(client: AsyncClient) -> None:
    try:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        await close_redis()


@pytest.mark.integration
async def test_real_redis_enforces_auth_rate_limit(client: AsyncClient) -> None:
    redis = from_url(os.environ["REDIS_URL"], decode_responses=True)
    settings = get_settings().model_copy(
        update={
            "auth_rate_limit_requests": 2,
            "auth_rate_limit_window_seconds": 60,
        }
    )
    client_ip = "198.51.100.77"
    fingerprint = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()
    key = f"spike:rate-limit:auth:{fingerprint}"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": (client_ip, 12345),
        }
    )
    try:
        await redis.delete(key)
        await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)
        await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)
        with pytest.raises(HTTPException) as exc_info:
            await enforce_auth_rate_limit(request=request, redis=redis, settings=settings)
        assert exc_info.value.status_code == 429
    finally:
        await redis.delete(key)
        await redis.aclose()
