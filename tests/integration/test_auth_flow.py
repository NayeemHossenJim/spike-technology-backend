from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import InMemoryEmailSender


@pytest.mark.integration
async def test_register_verify_login_refresh_logout(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
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
    assert len(email_sender.verification_messages) == 1

    unverified_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "CorrectHorseBattery9"},
    )
    assert unverified_login.status_code == 403

    verification_token = email_sender.verification_messages[0][1]
    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={"token": verification_token},
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
async def test_password_reset_revokes_existing_refresh_tokens(
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
    verification_token = email_sender.verification_messages[0][1]
    await client.post("/api/v1/auth/verify-email", json={"token": verification_token})

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "CorrectHorseBattery9"},
    )
    refresh_token = login.json()["refresh_token"]

    forgot = await client.post("/api/v1/auth/forgot-password", json={"email": "reset@example.com"})
    assert forgot.status_code == 202
    reset_token = email_sender.reset_messages[0][1]

    reset = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": "NewCorrectHorseBattery9"},
    )
    assert reset.status_code == 200

    previous_session = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert previous_session.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "reset@example.com", "password": "NewCorrectHorseBattery9"},
    )
    assert new_login.status_code == 200
