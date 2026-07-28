from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import SQLModel, func, select

from app.models.base import utc_now
from app.models.business import Business, RoleAssignment
from app.models.subscription import (
    AI_FULL_RESPONSES_PER_PERIOD,
    TRIAL_DAYS,
    EntitlementKey,
    EntitlementSource,
    Subscription,
    SubscriptionEntitlement,
    SubscriptionStatus,
)
from app.services.subscriptions import (
    EntitlementDeniedError,
    EntitlementLimitReachedError,
    EntitlementService,
    SubscriptionInactiveError,
)
from app.services.tenant import TenantScope
from tests.conftest import InMemoryEmailSender


async def register_verify_and_login(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
) -> str:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Tenant Owner",
            "email": email,
            "password": "CorrectHorseBattery9",
            "industry": "Technology",
            "job_role": "Executive / C-Suite",
        },
    )
    assert registration.status_code == 201
    otp = email_sender.verification_otps[-1][1]
    verification = await client.post(
        "/api/v1/auth/verify-email",
        json={"email": email, "otp": otp},
    )
    assert verification.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "CorrectHorseBattery9"},
    )
    assert login.status_code == 200
    return login.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
async def test_migration_matches_sqlmodel_metadata(test_engine: AsyncEngine) -> None:
    async with test_engine.connect() as connection:
        differences = await connection.run_sync(
            lambda sync_connection: compare_metadata(
                MigrationContext.configure(
                    sync_connection,
                    opts={"compare_type": True},
                ),
                SQLModel.metadata,
            )
        )
    assert differences == []


@pytest.mark.integration
async def test_migration_seeds_only_confirmed_public_plan_values(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/plans")
    assert response.status_code == 200
    plans = response.json()
    assert [plan["code"] for plan in plans] == ["premium", "pro", "enterprise"]

    by_code = {plan["code"]: plan for plan in plans}
    assert by_code["premium"]["name"] == "Premium"
    assert by_code["premium"]["monthly_price_cents"] == 5999
    assert by_code["pro"]["name"] == "Pro Plan"
    assert by_code["pro"]["monthly_price_cents"] == 9999
    assert by_code["enterprise"]["monthly_price_cents"] is None
    assert by_code["enterprise"]["is_custom_pricing"] is True

    premium_entitlements = {
        entitlement["key"]: entitlement["limit_value"]
        for entitlement in by_code["premium"]["entitlements"]
    }
    assert premium_entitlements == {
        "ai_full_responses": 15,
        "dashboards": 20,
    }
    assert by_code["pro"]["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": 15,
        }
    ]
    assert by_code["enterprise"]["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": 15,
        }
    ]


@pytest.mark.integration
async def test_business_onboarding_atomically_creates_owner_trial_and_entitlement(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = await register_verify_and_login(
        client,
        email_sender,
        email="owner@example.com",
    )

    before_onboarding = await client.get("/api/v1/businesses/me", headers=bearer(token))
    assert before_onboarding.status_code == 409
    assert before_onboarding.json()["detail"] == "Business onboarding is required."

    created = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "  Acme Analytics  "},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["business"]["name"] == "Acme Analytics"
    assert payload["business"]["industry"] == "Technology"
    assert payload["role_assignment"]["role"] == "owner"
    assert payload["role_assignment"]["is_active"] is True
    assert payload["subscription"]["status"] == "trialing"
    assert payload["subscription"]["plan_id"] is None

    trial_start = payload["subscription"]["trial_started_at"]
    trial_end = payload["subscription"]["trial_ends_at"]
    assert trial_start is not None
    assert trial_end is not None

    current = await client.get("/api/v1/businesses/me", headers=bearer(token))
    assert current.status_code == 200
    assert current.json()["business"]["id"] == payload["business"]["id"]

    entitlements = await client.get("/api/v1/entitlements/me", headers=bearer(token))
    assert entitlements.status_code == 200
    entitlement_payload = entitlements.json()
    assert entitlement_payload["access_active"] is True
    assert entitlement_payload["access_reason"] == "active"
    assert entitlement_payload["entitlements"] == [
        {
            "key": "ai_full_responses",
            "is_enabled": True,
            "limit_value": AI_FULL_RESPONSES_PER_PERIOD,
            "source": "trial",
        }
    ]

    async with session_factory() as session:
        businesses = await session.scalar(select(func.count()).select_from(Business))
        assignments = await session.scalar(select(func.count()).select_from(RoleAssignment))
        subscriptions = await session.scalar(select(func.count()).select_from(Subscription))
        grants = await session.scalar(select(func.count()).select_from(SubscriptionEntitlement))
        subscription = (await session.execute(select(Subscription))).scalar_one()
        assert businesses == assignments == subscriptions == grants == 1
        assert subscription.trial_ends_at - subscription.trial_started_at == timedelta(
            days=TRIAL_DAYS
        )


@pytest.mark.integration
async def test_concurrent_onboarding_creates_exactly_one_tenant(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = await register_verify_and_login(
        client,
        email_sender,
        email="race@example.com",
    )

    async def onboard() -> int:
        response = await client.post(
            "/api/v1/businesses",
            headers=bearer(token),
            json={"name": "Race Safe Ltd"},
        )
        return response.status_code

    statuses = await asyncio.gather(onboard(), onboard())
    assert sorted(statuses) == [201, 409]

    duplicate = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "Second Business"},
    )
    assert duplicate.status_code == 409

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Business)) == 1
        assert await session.scalar(select(func.count()).select_from(RoleAssignment)) == 1
        assert await session.scalar(select(func.count()).select_from(Subscription)) == 1


@pytest.mark.integration
async def test_subscription_reads_and_entitlement_rows_are_tenant_isolated(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
) -> None:
    token_a = await register_verify_and_login(
        client,
        email_sender,
        email="owner-a@example.com",
    )
    token_b = await register_verify_and_login(
        client,
        email_sender,
        email="owner-b@example.com",
    )
    tenant_a = (
        await client.post(
            "/api/v1/businesses",
            headers=bearer(token_a),
            json={"name": "Tenant A"},
        )
    ).json()
    tenant_b = (
        await client.post(
            "/api/v1/businesses",
            headers=bearer(token_b),
            json={"name": "Tenant B"},
        )
    ).json()

    subscription_a = tenant_a["subscription"]["id"]
    subscription_b = tenant_b["subscription"]["id"]
    business_a = UUID(tenant_a["business"]["id"])

    own = await client.get(
        f"/api/v1/subscriptions/{subscription_a}",
        headers=bearer(token_a),
    )
    cross_tenant = await client.get(
        f"/api/v1/subscriptions/{subscription_b}",
        headers=bearer(token_a),
    )
    unknown = await client.get(
        "/api/v1/subscriptions/00000000-0000-4000-8000-000000000099",
        headers=bearer(token_a),
    )
    assert own.status_code == 200
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == unknown.json() == {"detail": "Subscription not found."}

    now = utc_now()
    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(SubscriptionEntitlement).values(
                    id=UUID("30000000-0000-4000-8000-000000000001"),
                    created_at=now,
                    updated_at=now,
                    business_id=business_a,
                    subscription_id=UUID(subscription_b),
                    key=EntitlementKey.DASHBOARDS.value,
                    source=EntitlementSource.OVERRIDE.value,
                    is_enabled=True,
                    limit_value=1,
                )
            )


@pytest.mark.integration
async def test_entitlements_enforce_limit_and_expired_trial(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = await register_verify_and_login(
        client,
        email_sender,
        email="credits@example.com",
    )
    created = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "Credits Tenant"},
    )
    business_id = UUID(created.json()["business"]["id"])
    subscription_id = UUID(created.json()["subscription"]["id"])

    async with session_factory() as session:
        service = EntitlementService(session)
        scope = TenantScope(business_id)
        grant = await service.require(
            scope,
            EntitlementKey.AI_FULL_RESPONSES,
            current_usage=14,
        )
        assert grant.remaining == 1

        with pytest.raises(EntitlementLimitReachedError):
            await service.require(
                scope,
                EntitlementKey.AI_FULL_RESPONSES,
                current_usage=15,
            )
        with pytest.raises(EntitlementDeniedError):
            await service.require(
                scope,
                EntitlementKey.DASHBOARDS,
                current_usage=0,
            )

        subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None
        subscription.trial_ends_at = utc_now() - timedelta(seconds=1)
        subscription.current_period_ends_at = subscription.trial_ends_at
        await session.commit()

    summary = await client.get("/api/v1/entitlements/me", headers=bearer(token))
    assert summary.status_code == 200
    assert summary.json()["access_active"] is False
    assert summary.json()["access_reason"] == "trial_expired"

    async with session_factory() as session:
        with pytest.raises(SubscriptionInactiveError, match="trial_expired"):
            await EntitlementService(session).require(
                TenantScope(business_id),
                EntitlementKey.AI_FULL_RESPONSES,
                current_usage=0,
            )


@pytest.mark.integration
async def test_database_rejects_second_current_subscription_for_business(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
) -> None:
    token = await register_verify_and_login(
        client,
        email_sender,
        email="one-subscription@example.com",
    )
    created = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": "One Subscription Tenant"},
    )
    business_id = UUID(created.json()["business"]["id"])
    now = utc_now()

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(Subscription).values(
                    id=UUID("30000000-0000-4000-8000-000000000002"),
                    created_at=now,
                    updated_at=now,
                    business_id=business_id,
                    plan_id=None,
                    status=SubscriptionStatus.ACTIVE.value,
                    trial_started_at=None,
                    trial_ends_at=None,
                    current_period_started_at=now,
                    current_period_ends_at=now + timedelta(days=30),
                    cancel_at_period_end=False,
                    canceled_at=None,
                    ended_at=None,
                    stripe_customer_id=None,
                    stripe_subscription_id=None,
                )
            )
