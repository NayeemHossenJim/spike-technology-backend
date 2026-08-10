from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User, UserRole
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import (
    bearer,
    register_verify_and_login,
)


async def create_customer_business(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    business_name: str,
) -> tuple[str, UUID]:
    token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )

    response = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": business_name},
    )
    assert response.status_code == 201

    return token, UUID(response.json()["business"]["id"])


async def create_platform_operator(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    role: UserRole,
) -> str:
    token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )

    async with session_factory() as session:
        result = await session.execute(User.__table__.select().where(User.email == email))
        row = result.first()
        assert row is not None
        user_id = row._mapping["id"]

        user = await session.get(User, user_id)
        assert user is not None
        user.role = role
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "CorrectHorseBattery9",
            "remember_me": False,
        },
    )

    # Existing helper passwords may differ between fixture generations,
    # therefore fall back to the already issued access token if login is
    # not required by the helper implementation.
    if login.status_code == 200:
        return login.json()["access_token"]

    return token


@pytest.mark.integration
async def test_customer_service_can_read_safe_subscription_support_view(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-stage4-customer@example.com",
        business_name="Stage Four Customer",
    )

    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-stage4-support@example.com",
        role=UserRole.CUSTOMER_SERVICE,
    )

    response = await client.get(
        f"/api/v1/admin/businesses/{business_id}/subscription",
        headers=bearer(support_token),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["business_id"] == str(business_id)
    assert payload["status"] == "trialing"
    assert payload["stripe_managed"] is False
    assert payload["access"]["active"] is True
    assert payload["access"]["reason"] == "active"

    assert payload["entitlements"]
    assert {entitlement["source"] for entitlement in payload["entitlements"]} <= {
        "trial",
        "plan",
        "override",
    }

    serialized = str(payload)

    for sensitive_name in (
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_price_id",
        "last_stripe_event_id",
        "hosted_invoice_url",
        "invoice_pdf_url",
        "payment_method",
        "card",
    ):
        assert sensitive_name not in serialized


@pytest.mark.integration
async def test_super_admin_can_read_subscription_support_view(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-stage4-admin-customer@example.com",
        business_name="Stage Four Admin Customer",
    )

    admin_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-stage4-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    response = await client.get(
        f"/api/v1/admin/businesses/{business_id}/subscription",
        headers=bearer(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["business_id"] == str(business_id)


@pytest.mark.integration
async def test_customer_cannot_read_admin_subscription_view(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    token, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-stage4-normal-customer@example.com",
        business_name="Stage Four Normal Customer",
    )

    response = await client.get(
        f"/api/v1/admin/businesses/{business_id}/subscription",
        headers=bearer(token),
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_admin_subscription_unknown_business_is_not_found(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-stage4-not-found-support@example.com",
        role=UserRole.CUSTOMER_SERVICE,
    )

    response = await client.get(
        f"/api/v1/admin/businesses/{uuid4()}/subscription",
        headers=bearer(support_token),
    )

    assert response.status_code == 404
