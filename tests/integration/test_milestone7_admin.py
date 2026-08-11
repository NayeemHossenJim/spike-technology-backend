from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.user import User, UserRole
from tests.conftest import InMemoryEmailSender

PASSWORD = "CorrectHorseBattery9"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_and_verify(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    full_name: str,
) -> None:
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


async def login(
    client: AsyncClient,
    *,
    email: str,
) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def create_customer(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    full_name: str,
    business_name: str,
) -> tuple[str, dict]:
    await register_and_verify(
        client,
        email_sender,
        email=email,
        full_name=full_name,
    )

    token = await login(
        client,
        email=email,
    )

    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": business_name},
    )
    assert onboarding.status_code == 201

    return token, onboarding.json()


async def create_platform_operator(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    full_name: str,
    role: UserRole,
) -> str:
    assert role in {
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_SERVICE,
    }

    await register_and_verify(
        client,
        email_sender,
        email=email,
        full_name=full_name,
    )

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = role
        await session.commit()

    return await login(
        client,
        email=email,
    )


@pytest.mark.integration
async def test_customer_cannot_access_platform_admin_endpoints(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    customer_token, _ = await create_customer(
        client,
        email_sender,
        email="m7-customer@example.com",
        full_name="M7 Customer",
        business_name="M7 Customer Business",
    )

    users = await client.get(
        "/api/v1/admin/users",
        headers=bearer(customer_token),
    )
    businesses = await client.get(
        "/api/v1/admin/businesses",
        headers=bearer(customer_token),
    )

    assert users.status_code == 403
    assert businesses.status_code == 403


@pytest.mark.integration
async def test_customer_service_sees_customers_but_not_platform_staff(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_customer(
        client,
        email_sender,
        email="m7-alpha@example.com",
        full_name="Alpha Customer",
        business_name="Alpha Analytics",
    )
    await create_customer(
        client,
        email_sender,
        email="m7-beta@example.com",
        full_name="Beta Customer",
        business_name="Beta Finance",
    )

    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-support@example.com",
        full_name="M7 Customer Service",
        role=UserRole.CUSTOMER_SERVICE,
    )

    await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-admin@example.com",
        full_name="M7 Super Admin",
        role=UserRole.SUPER_ADMIN,
    )

    response = await client.get(
        "/api/v1/admin/users",
        headers=bearer(support_token),
    )

    assert response.status_code == 200

    payload = response.json()
    emails = {item["email"] for item in payload["items"]}

    assert "m7-alpha@example.com" in emails
    assert "m7-beta@example.com" in emails

    assert "m7-support@example.com" not in emails
    assert "m7-admin@example.com" not in emails

    assert all(item["role"] == "user" for item in payload["items"])

    assert all("password_hash" not in item for item in payload["items"])

    search = await client.get(
        "/api/v1/admin/users",
        headers=bearer(support_token),
        params={"q": "Beta Customer"},
    )

    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["email"] == "m7-beta@example.com"


@pytest.mark.integration
async def test_super_admin_can_enumerate_platform_roles(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_customer(
        client,
        email_sender,
        email="m7-super-customer@example.com",
        full_name="Super Admin Customer",
        business_name="Super Admin Customer Business",
    )

    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-super-support@example.com",
        full_name="Support Operator",
        role=UserRole.CUSTOMER_SERVICE,
    )
    assert support_token

    admin_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-super-admin@example.com",
        full_name="Super Administrator",
        role=UserRole.SUPER_ADMIN,
    )

    response = await client.get(
        "/api/v1/admin/users",
        headers=bearer(admin_token),
    )

    assert response.status_code == 200

    payload = response.json()

    role_by_email = {item["email"]: item["role"] for item in payload["items"]}

    assert role_by_email["m7-super-customer@example.com"] == "user"
    assert role_by_email["m7-super-support@example.com"] == "customer_service"
    assert role_by_email["m7-super-admin@example.com"] == "super_admin"

    search = await client.get(
        "/api/v1/admin/users",
        headers=bearer(admin_token),
        params={"q": "m7-super-support@example.com"},
    )

    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["role"] == "customer_service"


@pytest.mark.integration
async def test_customer_service_business_search_pagination_and_data_boundary(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_customer(
        client,
        email_sender,
        email="m7-business-alpha@example.com",
        full_name="Business Alpha Owner",
        business_name="M7 Search Alpha",
    )
    await create_customer(
        client,
        email_sender,
        email="m7-business-beta@example.com",
        full_name="Business Beta Owner",
        business_name="M7 Search Beta",
    )
    await create_customer(
        client,
        email_sender,
        email="unrelated@example.com",
        full_name="Unrelated Owner",
        business_name="Unrelated Company",
    )

    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-business-support@example.com",
        full_name="Business Support Operator",
        role=UserRole.CUSTOMER_SERVICE,
    )

    first_page = await client.get(
        "/api/v1/admin/businesses",
        headers=bearer(support_token),
        params={
            "q": "M7 Search",
            "limit": 1,
            "offset": 0,
        },
    )
    second_page = await client.get(
        "/api/v1/admin/businesses",
        headers=bearer(support_token),
        params={
            "q": "M7 Search",
            "limit": 1,
            "offset": 1,
        },
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200

    first = first_page.json()
    second = second_page.json()

    assert first["total"] == second["total"] == 2
    assert first["limit"] == second["limit"] == 1
    assert first["offset"] == 0
    assert second["offset"] == 1

    assert len(first["items"]) == 1
    assert len(second["items"]) == 1

    first_item = first["items"][0]
    second_item = second["items"][0]

    assert first_item["id"] != second_item["id"]

    returned_names = {
        first_item["name"],
        second_item["name"],
    }

    assert returned_names == {
        "M7 Search Alpha",
        "M7 Search Beta",
    }

    expected_business_fields = {
        "id",
        "name",
        "industry",
        "owner_user_id",
        "owner",
        "role_assignment_role",
        "role_assignment_is_active",
        "created_at",
        "updated_at",
    }

    expected_owner_fields = {
        "id",
        "email",
        "full_name",
        "role",
        "is_active",
        "is_verified",
        "last_login_at",
        "created_at",
        "updated_at",
    }

    assert set(first_item) == expected_business_fields
    assert set(first_item["owner"]) == expected_owner_fields

    assert first_item["owner"]["role"] == "user"
    assert first_item["role_assignment_role"] == "owner"

    forbidden_fields = {
        "password_hash",
        "subscription",
        "subscription_id",
        "stripe_customer_id",
        "stripe_subscription_id",
        "billing",
        "invoices",
        "uploads",
        "reports",
        "dashboards",
        "ai_conversations",
        "ai_messages",
    }

    assert forbidden_fields.isdisjoint(first_item)
    assert forbidden_fields.isdisjoint(first_item["owner"])
