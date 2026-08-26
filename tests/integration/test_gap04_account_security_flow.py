from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.security import verify_password
from app.core.session_cookie import REFRESH_COOKIE_NAME
from app.models.auth import EmailChangeOTP, RefreshToken
from app.models.billing import BillingCheckoutSession, BillingCheckoutStatus
from app.models.business import RoleAssignment
from app.models.subscription import Plan, Subscription, SubscriptionStatus
from app.models.user import User, UserRole
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
            "full_name": "Account Security User",
            "email": email,
            "password": CURRENT_PASSWORD,
            "industry": "Technology",
            "job_role": "Engineer / Developer",
        },
    )
    assert registration.status_code == 201

    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={
            "email": email,
            "otp": email_sender.verification_otps[-1][1],
        },
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


@pytest.mark.integration
async def test_verified_email_change_revokes_every_existing_session(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, access_token = await register_verify_and_login(
        client,
        email_sender,
        email="old-address@example.com",
    )
    assert client.cookies.get(REFRESH_COOKIE_NAME)

    wrong_password = await client.post(
        "/api/v1/users/me/email-change",
        headers=bearer(access_token),
        json={
            "old_email": "old-address@example.com",
            "new_email": "new-address@example.com",
            "current_password": "IncorrectPassword9",
        },
    )
    assert wrong_password.status_code == 401
    assert email_sender.email_change_otps == []

    requested = await client.post(
        "/api/v1/users/me/email-change",
        headers=bearer(access_token),
        json={
            "old_email": "OLD-ADDRESS@example.com",
            "new_email": "NEW-ADDRESS@example.com",
            "current_password": CURRENT_PASSWORD,
        },
    )
    assert requested.status_code == 202
    assert email_sender.email_change_otps[-1][0] == "new-address@example.com"
    otp = email_sender.email_change_otps[-1][1]

    wrong_otp = "000000" if otp != "000000" else "000001"
    rejected = await client.post(
        "/api/v1/users/me/email-change/confirm",
        headers=bearer(access_token),
        json={"new_email": "new-address@example.com", "otp": wrong_otp},
    )
    assert rejected.status_code == 400

    confirmed = await client.post(
        "/api/v1/users/me/email-change/confirm",
        headers=bearer(access_token),
        json={"new_email": "new-address@example.com", "otp": otp},
    )
    assert confirmed.status_code == 200
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    invalidated_access = await client.get(
        "/api/v1/users/me",
        headers=bearer(access_token),
    )
    assert invalidated_access.status_code == 401

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "old-address@example.com", "password": CURRENT_PASSWORD},
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "new-address@example.com", "password": CURRENT_PASSWORD},
    )
    assert new_login.status_code == 200

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.email == "new-address@example.com"
        assert user.auth_session_version == 1
        otp_records = (
            await session.execute(select(EmailChangeOTP).where(EmailChangeOTP.user_id == user_id))
        ).scalars()
        assert all(record.used_at is not None for record in otp_records)
        refresh_tokens = list(
            (
                await session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
            ).scalars()
        )
        assert len(refresh_tokens) == 2
        assert sum(token.revoked_at is not None for token in refresh_tokens) == 1
        assert sum(token.revoked_at is None for token in refresh_tokens) == 1


@pytest.mark.integration
async def test_password_change_requires_reauthentication_and_invalidates_access(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, access_token = await register_verify_and_login(
        client,
        email_sender,
        email="password-change@example.com",
    )

    rejected = await client.put(
        "/api/v1/users/me/password",
        headers=bearer(access_token),
        json={
            "current_password": "IncorrectPassword9",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert rejected.status_code == 401

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
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    invalidated_access = await client.get(
        "/api/v1/users/me",
        headers=bearer(access_token),
    )
    assert invalidated_access.status_code == 401

    old_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "password-change@example.com", "password": CURRENT_PASSWORD},
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "password-change@example.com", "password": NEW_PASSWORD},
    )
    assert new_login.status_code == 200

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.auth_session_version == 1
        assert verify_password(NEW_PASSWORD, user.password_hash)


@pytest.mark.integration
async def test_account_deletion_anonymizes_identity_and_ends_local_trial(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    email = "delete-account@example.com"
    user_id, access_token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    onboarded = await client.post(
        "/api/v1/businesses",
        headers=bearer(access_token),
        json={"name": "Retained Audit Tenant"},
    )
    assert onboarded.status_code == 201
    subscription_id = UUID(onboarded.json()["subscription"]["id"])

    invalid_confirmation = await client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD, "confirmation": "delete"},
    )
    assert invalid_confirmation.status_code == 422

    deleted = await client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD, "confirmation": "DELETE"},
    )
    assert deleted.status_code == 200
    assert client.cookies.get(REFRESH_COOKIE_NAME) is None

    rejected_access = await client.get(
        "/api/v1/users/me",
        headers=bearer(access_token),
    )
    assert rejected_access.status_code == 401

    async with session_factory() as session:
        user = await session.get(User, user_id)
        subscription = await session.get(Subscription, subscription_id)
        assignment = (
            await session.execute(select(RoleAssignment).where(RoleAssignment.user_id == user_id))
        ).scalar_one()
        assert user is not None
        assert user.deleted_at is not None
        assert user.email.startswith(f"deleted-{user_id.hex}-")
        assert user.email.endswith("@deleted.invalid")
        assert user.full_name == "Deleted User"
        assert user.username is None
        assert user.is_active is False
        assert user.is_verified is False
        assert user.auth_session_version == 1
        assert assignment.is_active is False
        assert subscription is not None
        assert subscription.status == SubscriptionStatus.CANCELED
        assert subscription.canceled_at is not None
        assert subscription.ended_at is not None

    replacement_registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Replacement Account",
            "email": email,
            "password": CURRENT_PASSWORD,
        },
    )
    assert replacement_registration.status_code == 201
    assert UUID(replacement_registration.json()["id"]) != user_id


@pytest.mark.integration
async def test_account_deletion_blocks_stripe_subscription_and_last_super_admin(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, access_token = await register_verify_and_login(
        client,
        email_sender,
        email="paid-account@example.com",
    )
    onboarded = await client.post(
        "/api/v1/businesses",
        headers=bearer(access_token),
        json={"name": "Paid Tenant"},
    )
    assert onboarded.status_code == 201
    subscription_id = UUID(onboarded.json()["subscription"]["id"])

    async with session_factory() as session:
        subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None
        subscription.stripe_subscription_id = "sub_gap04_active"
        await session.commit()

    billing_blocked = await client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD, "confirmation": "DELETE"},
    )
    assert billing_blocked.status_code == 409

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is True
        assert user.deleted_at is None

        subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None
        subscription.stripe_subscription_id = None
        plan = (await session.execute(select(Plan).order_by(Plan.sort_order).limit(1))).scalar_one()
        session.add(
            BillingCheckoutSession(
                business_id=subscription.business_id,
                subscription_id=subscription.id,
                plan_id=plan.id,
                status=BillingCheckoutStatus.COMPLETE,
                idempotency_key_digest="a" * 64,
                stripe_subscription_id="sub_gap04_in_flight",
            )
        )
        await session.commit()

    checkout_sync_blocked = await client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD, "confirmation": "DELETE"},
    )
    assert checkout_sync_blocked.status_code == 409

    async with session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.role = UserRole.SUPER_ADMIN
        subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None
        subscription.status = SubscriptionStatus.CANCELED
        await session.commit()

    admin_blocked = await client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=bearer(access_token),
        json={"current_password": CURRENT_PASSWORD, "confirmation": "DELETE"},
    )
    assert admin_blocked.status_code == 409
    assert "last active super administrator" in admin_blocked.json()["detail"]
