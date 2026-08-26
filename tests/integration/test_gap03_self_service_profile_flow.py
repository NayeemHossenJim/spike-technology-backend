from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.business import Business
from app.models.user import User, UserRole
from tests.conftest import InMemoryEmailSender

PASSWORD = "CorrectHorseBattery9"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_verify_and_login(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    full_name: str,
) -> tuple[UUID, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": full_name,
            "email": email,
            "password": PASSWORD,
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
            "password": PASSWORD,
        },
    )
    assert login.status_code == 200

    return UUID(registration.json()["id"]), login.json()["access_token"]


@pytest.mark.integration
async def test_profile_update_persists_figma_fields_and_synchronizes_business_industry(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, token = await register_verify_and_login(
        client,
        email_sender,
        email="gap03-profile@example.com",
        full_name="Original Profile",
    )

    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "Profile Tenant"},
    )
    assert onboarding.status_code == 201
    business_id = UUID(onboarding.json()["business"]["id"])
    assert onboarding.json()["business"]["industry"] == "Technology"

    update = await client.patch(
        "/api/v1/users/me",
        headers=bearer(token),
        json={
            "full_name": "  Brendoly Cooper  ",
            "username": "  B. Cooper  ",
            "phone_number": "  (207) 555-0119  ",
            "industry": "Accounting",
            "job_role": "Accountant",
            "street_address": "  2972 Westheimer Rd.  ",
            "city": "  Santa Ana  ",
            "state": "  Illinois  ",
            "postal_code": "  85486  ",
            "bio": "  Finance operations lead.  ",
        },
    )
    assert update.status_code == 200
    assert update.json() == {
        "id": str(user_id),
        "email": "gap03-profile@example.com",
        "full_name": "Brendoly Cooper",
        "username": "B. Cooper",
        "phone_number": "(207) 555-0119",
        "role": "user",
        "industry": "Accounting",
        "job_role": "Accountant",
        "street_address": "2972 Westheimer Rd.",
        "city": "Santa Ana",
        "state": "Illinois",
        "postal_code": "85486",
        "bio": "Finance operations lead.",
        "is_active": True,
        "is_verified": True,
        "email_two_step_enabled": False,
    }

    me = await client.get(
        "/api/v1/users/me",
        headers=bearer(token),
    )
    assert me.status_code == 200
    assert me.json() == update.json()

    tenant = await client.get(
        "/api/v1/businesses/me",
        headers=bearer(token),
    )
    assert tenant.status_code == 200
    assert tenant.json()["business"]["industry"] == "Accounting"

    async with session_factory() as session:
        persisted_user = await session.get(User, user_id)
        persisted_business = await session.get(Business, business_id)
        assert persisted_user is not None
        assert persisted_business is not None
        assert persisted_user.username == "B. Cooper"
        assert persisted_user.bio == "Finance operations lead."
        assert persisted_business.industry == "Accounting"

    clear = await client.patch(
        "/api/v1/users/me",
        headers=bearer(token),
        json={
            "phone_number": None,
            "industry": None,
            "street_address": None,
            "city": None,
            "state": None,
            "postal_code": None,
            "bio": None,
        },
    )
    assert clear.status_code == 200
    assert clear.json()["username"] == "B. Cooper"
    assert clear.json()["job_role"] == "Accountant"
    assert clear.json()["phone_number"] is None
    assert clear.json()["industry"] is None
    assert clear.json()["bio"] is None

    tenant_after_clear = await client.get(
        "/api/v1/businesses/me",
        headers=bearer(token),
    )
    assert tenant_after_clear.status_code == 200
    assert tenant_after_clear.json()["business"]["industry"] is None


@pytest.mark.integration
async def test_profile_contract_rejects_sensitive_fields_and_works_without_a_tenant(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    unauthenticated = await client.patch(
        "/api/v1/users/me",
        json={"full_name": "No Session"},
    )
    assert unauthenticated.status_code == 401

    user_id, token = await register_verify_and_login(
        client,
        email_sender,
        email="gap03-operator@example.com",
        full_name="Platform Operator",
    )

    async with session_factory() as session:
        operator = await session.get(User, user_id)
        assert operator is not None
        operator.role = UserRole.CUSTOMER_SERVICE
        await session.commit()

    allowed = await client.patch(
        "/api/v1/users/me",
        headers=bearer(token),
        json={
            "full_name": "Customer Service Operator",
            "username": "Support Agent",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["role"] == "customer_service"
    assert allowed.json()["full_name"] == "Customer Service Operator"
    assert allowed.json()["username"] == "Support Agent"

    for invalid_payload in (
        {},
        {"email": "replacement@example.com"},
        {"role": "super_admin"},
        {"is_active": False},
        {"full_name": None},
        {"phone_number": "123"},
    ):
        rejected = await client.patch(
            "/api/v1/users/me",
            headers=bearer(token),
            json=invalid_payload,
        )
        assert rejected.status_code == 422

    async with session_factory() as session:
        persisted = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert persisted.email == "gap03-operator@example.com"
        assert persisted.role == UserRole.CUSTOMER_SERVICE
        assert persisted.is_active is True
