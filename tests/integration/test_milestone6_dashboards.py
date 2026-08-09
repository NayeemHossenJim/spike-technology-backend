from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.models.dashboard import Dashboard
from app.models.subscription import (
    EntitlementKey,
    EntitlementSource,
    SubscriptionEntitlement,
)
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import onboard_owner


async def grant_dashboards(
    session_factory: async_sessionmaker[AsyncSession],
    onboarding: dict,
    *,
    limit: int,
) -> None:
    async with session_factory() as session:
        session.add(
            SubscriptionEntitlement(
                business_id=UUID(onboarding["business"]["id"]),
                subscription_id=UUID(onboarding["subscription"]["id"]),
                key=EntitlementKey.DASHBOARDS,
                source=EntitlementSource.OVERRIDE,
                is_enabled=True,
                limit_value=limit,
            )
        )
        await session.commit()


def dashboard_payload(
    title: str,
    *,
    dashboard_type: str = "executive_summary",
) -> dict:
    return {
        "dashboard_type": dashboard_type,
        "title": title,
        "configuration": {
            "currency": "USD",
            "period": "monthly",
        },
    }


@pytest.mark.integration
async def test_trial_without_dashboard_entitlement_is_denied(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="m6-trial-denied@example.com",
        business_name="M6 Trial Denied",
    )

    response = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token),
        json=dashboard_payload("Trial dashboard"),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Dashboard access is not enabled for the current subscription."
    }

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Dashboard)) == 0


@pytest.mark.integration
async def test_dashboard_crud_limit_pagination_and_tenant_isolation(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token_a, onboarding_a = await onboard_owner(
        client,
        email_sender,
        email="m6-dashboard-a@example.com",
        business_name="M6 Dashboard A",
    )
    token_b, onboarding_b = await onboard_owner(
        client,
        email_sender,
        email="m6-dashboard-b@example.com",
        business_name="M6 Dashboard B",
    )

    await grant_dashboards(
        session_factory,
        onboarding_a,
        limit=2,
    )
    await grant_dashboards(
        session_factory,
        onboarding_b,
        limit=1,
    )

    first = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token_a),
        json=dashboard_payload("Executive Overview"),
    )
    second = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token_a),
        json=dashboard_payload(
            "Finance Overview",
            dashboard_type="financial_performance",
        ),
    )
    foreign = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token_b),
        json=dashboard_payload(
            "Operations",
            dashboard_type="operational_kpi",
        ),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert foreign.status_code == 201

    first_payload = first.json()
    second_payload = second.json()
    foreign_payload = foreign.json()

    assert first_payload["business_id"] == onboarding_a["business"]["id"]
    assert first_payload["created_by_user_id"] == onboarding_a["role_assignment"]["user_id"]
    assert first_payload["dashboard_type"] == "executive_summary"

    capped = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token_a),
        json=dashboard_payload("Too many dashboards"),
    )
    assert capped.status_code == 409
    assert capped.json() == {
        "detail": "The dashboard limit has been reached for the current subscription."
    }

    page_one = await client.get(
        "/api/v1/dashboards?limit=1&offset=0",
        headers=bearer(token_a),
    )
    page_two = await client.get(
        "/api/v1/dashboards?limit=1&offset=1",
        headers=bearer(token_a),
    )

    assert page_one.status_code == page_two.status_code == 200
    assert page_one.json()["total"] == 2
    assert page_two.json()["total"] == 2
    assert len(page_one.json()["items"]) == 1
    assert len(page_two.json()["items"]) == 1

    own = await client.get(
        f"/api/v1/dashboards/{first_payload['id']}",
        headers=bearer(token_a),
    )
    cross_tenant = await client.get(
        f"/api/v1/dashboards/{foreign_payload['id']}",
        headers=bearer(token_a),
    )
    unknown = await client.get(
        f"/api/v1/dashboards/{uuid4()}",
        headers=bearer(token_a),
    )

    assert own.status_code == 200
    assert cross_tenant.status_code == unknown.status_code == 404
    assert cross_tenant.json() == unknown.json() == {"detail": "Dashboard not found."}

    updated = await client.patch(
        f"/api/v1/dashboards/{first_payload['id']}",
        headers=bearer(token_a),
        json={
            "title": "  Updated Executive Overview  ",
            "configuration": {
                "currency": "BDT",
                "period": "quarterly",
            },
        },
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated Executive Overview"
    assert updated.json()["configuration"] == {
        "currency": "BDT",
        "period": "quarterly",
    }

    cross_update = await client.patch(
        f"/api/v1/dashboards/{foreign_payload['id']}",
        headers=bearer(token_a),
        json={"title": "Forbidden"},
    )
    assert cross_update.status_code == 404

    deleted = await client.delete(
        f"/api/v1/dashboards/{second_payload['id']}",
        headers=bearer(token_a),
    )
    assert deleted.status_code == 204
    assert deleted.content == b""

    deleted_read = await client.get(
        f"/api/v1/dashboards/{second_payload['id']}",
        headers=bearer(token_a),
    )
    assert deleted_read.status_code == 404

    replacement = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token_a),
        json=dashboard_payload("Replacement dashboard"),
    )
    assert replacement.status_code == 201

    async with session_factory() as session:
        tenant_a_count = await session.scalar(
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.business_id == UUID(onboarding_a["business"]["id"]))
        )
        tenant_b_count = await session.scalar(
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.business_id == UUID(onboarding_b["business"]["id"]))
        )

        assert tenant_a_count == 2
        assert tenant_b_count == 1


@pytest.mark.integration
async def test_concurrent_dashboard_creates_cannot_exceed_limit(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="m6-dashboard-race@example.com",
        business_name="M6 Dashboard Race",
    )
    await grant_dashboards(
        session_factory,
        onboarding,
        limit=1,
    )

    async def create(title: str):
        return await client.post(
            "/api/v1/dashboards",
            headers=bearer(token),
            json=dashboard_payload(title),
        )

    first, second = await asyncio.gather(
        create("Concurrent A"),
        create("Concurrent B"),
    )

    assert sorted([first.status_code, second.status_code]) == [201, 409]

    limited = first if first.status_code == 409 else second
    assert limited.json() == {
        "detail": "The dashboard limit has been reached for the current subscription."
    }

    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.business_id == UUID(onboarding["business"]["id"]))
        )
        assert count == 1
