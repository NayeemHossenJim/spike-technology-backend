from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.models.admin import AdminAuditEvent
from app.models.ai import (
    AICreditAccount,
    AICreditAdjustmentLedgerEntry,
    AICreditAdjustmentReason,
)
from app.models.user import User, UserRole
from app.services.admin_ai_credits import (
    AdminAICreditAdjustmentService,
)
from app.services.admin_audit import AdminAuditService
from app.services.ai_credits import AICreditService
from app.services.tenant import TenantScope
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone7_admin_subscriptions import (
    create_customer_business,
    create_platform_operator,
)


@pytest.mark.integration
async def test_super_admin_adjustment_is_atomic_audited_and_idempotent(
    client,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-credit-positive-customer@example.com",
        business_name="M7 Credit Positive",
    )

    admin_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-credit-positive-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    headers = {
        **bearer(admin_token),
        "Idempotency-Key": "m7-credit-positive-001",
    }

    created = await client.post(
        f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
        headers=headers,
        json={
            "delta": 5,
            "reason_code": "support_credit",
        },
    )

    assert created.status_code == 200
    payload = created.json()

    assert payload["delta"] == 5
    assert payload["reason_code"] == "support_credit"
    assert payload["base_limit_value"] == 15
    assert payload["effective_limit_before"] == 15
    assert payload["effective_limit_after"] == 20
    assert payload["reserved_count"] == 0
    assert payload["consumed_count"] == 0
    assert payload["remaining_after"] == 20
    assert payload["replayed"] is False

    replay = await client.post(
        f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
        headers=headers,
        json={
            "delta": 5,
            "reason_code": "support_credit",
        },
    )

    assert replay.status_code == 200
    assert replay.json()["id"] == payload["id"]
    assert replay.json()["replayed"] is True

    conflicting_replay = await client.post(
        f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
        headers=headers,
        json={
            "delta": 6,
            "reason_code": "support_credit",
        },
    )

    assert conflicting_replay.status_code == 409

    async with session_factory() as session:
        account = (
            await session.execute(
                select(AICreditAccount).where(AICreditAccount.business_id == business_id)
            )
        ).scalar_one()

        adjustment_count = await session.scalar(
            select(func.count())
            .select_from(AICreditAdjustmentLedgerEntry)
            .where(AICreditAdjustmentLedgerEntry.business_id == business_id)
        )

        audits = (
            (
                await session.execute(
                    select(AdminAuditEvent).where(
                        AdminAuditEvent.business_id == business_id,
                        AdminAuditEvent.action == "ai.credit.adjust",
                    )
                )
            )
            .scalars()
            .all()
        )

        assert account.limit_value == 20
        assert adjustment_count == 1
        assert len(audits) == 1
        assert audits[0].metadata_json["delta"] == 5
        assert audits[0].metadata_json["effective_limit_after"] == 20


@pytest.mark.integration
async def test_customer_and_customer_service_cannot_adjust_ai_credits(
    client,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    customer_token, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-credit-permission-customer@example.com",
        business_name="M7 Credit Permission",
    )

    support_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-credit-permission-support@example.com",
        role=UserRole.CUSTOMER_SERVICE,
    )

    for token, key in (
        (customer_token, "customer-adjustment"),
        (support_token, "support-adjustment"),
    ):
        response = await client.post(
            f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
            headers={
                **bearer(token),
                "Idempotency-Key": key,
            },
            json={
                "delta": 1,
                "reason_code": "support_credit",
            },
        )

        assert response.status_code == 403

    async with session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(AICreditAdjustmentLedgerEntry))
            == 0
        )


@pytest.mark.integration
async def test_negative_adjustment_cannot_revoke_reserved_credits(
    client,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-credit-floor-customer@example.com",
        business_name="M7 Credit Floor",
    )

    admin_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-credit-floor-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    scope = TenantScope(business_id)

    for sequence in range(2):
        async with session_factory() as session:
            await AICreditService(session).reserve(
                scope,
                idempotency_key=f"m7-floor-reservation-{sequence}",
            )

    rejected = await client.post(
        f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
        headers={
            **bearer(admin_token),
            "Idempotency-Key": "m7-floor-rejected",
        },
        json={
            "delta": -14,
            "reason_code": "administrative_correction",
        },
    )

    assert rejected.status_code == 409

    accepted = await client.post(
        f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
        headers={
            **bearer(admin_token),
            "Idempotency-Key": "m7-floor-accepted",
        },
        json={
            "delta": -13,
            "reason_code": "administrative_correction",
        },
    )

    assert accepted.status_code == 200
    assert accepted.json()["effective_limit_after"] == 2
    assert accepted.json()["reserved_count"] == 2
    assert accepted.json()["remaining_after"] == 0

    async with session_factory() as session:
        account = (
            await session.execute(
                select(AICreditAccount).where(AICreditAccount.business_id == business_id)
            )
        ).scalar_one()

        assert account.limit_value == 2
        assert account.reserved_count == 2

        adjustment_count = await session.scalar(
            select(func.count())
            .select_from(AICreditAdjustmentLedgerEntry)
            .where(AICreditAdjustmentLedgerEntry.business_id == business_id)
        )

        assert adjustment_count == 1


@pytest.mark.integration
async def test_concurrent_admin_adjustments_serialize_without_lost_updates(
    client,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-credit-concurrent-customer@example.com",
        business_name="M7 Credit Concurrent",
    )

    admin_token = await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-credit-concurrent-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    async def add_one(sequence: int):
        return await client.post(
            f"/api/v1/admin/businesses/{business_id}/ai-credits/adjustments",
            headers={
                **bearer(admin_token),
                "Idempotency-Key": f"m7-concurrent-{sequence}",
            },
            json={
                "delta": 1,
                "reason_code": "service_recovery",
            },
        )

    responses = await asyncio.gather(
        add_one(1),
        add_one(2),
    )

    assert [item.status_code for item in responses] == [200, 200]

    assert {item.json()["effective_limit_after"] for item in responses} == {16, 17}

    async with session_factory() as session:
        account = (
            await session.execute(
                select(AICreditAccount).where(AICreditAccount.business_id == business_id)
            )
        ).scalar_one()

        adjustment_count = await session.scalar(
            select(func.count())
            .select_from(AICreditAdjustmentLedgerEntry)
            .where(AICreditAdjustmentLedgerEntry.business_id == business_id)
        )

        audit_count = await session.scalar(
            select(func.count())
            .select_from(AdminAuditEvent)
            .where(
                AdminAuditEvent.business_id == business_id,
                AdminAuditEvent.action == "ai.credit.adjust",
            )
        )

        assert account.limit_value == 17
        assert adjustment_count == 2
        assert audit_count == 2


@pytest.mark.integration
async def test_audit_failure_rolls_back_credit_adjustment_atomically(
    client,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    _, business_id = await create_customer_business(
        client,
        email_sender,
        email="m7-credit-rollback-customer@example.com",
        business_name="M7 Credit Rollback",
    )

    await create_platform_operator(
        client,
        email_sender,
        session_factory,
        email="m7-credit-rollback-admin@example.com",
        role=UserRole.SUPER_ADMIN,
    )

    async def fail_audit(self, **kwargs):
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(
        AdminAuditService,
        "record",
        fail_audit,
    )

    async with session_factory() as session:
        actor = (
            await session.execute(
                select(User).where(User.email == "m7-credit-rollback-admin@example.com")
            )
        ).scalar_one()

        with pytest.raises(RuntimeError, match="forced audit failure"):
            await AdminAICreditAdjustmentService(session).adjust(
                actor=actor,
                business_id=business_id,
                delta=3,
                reason_code=AICreditAdjustmentReason.SUPPORT_CREDIT,
                idempotency_key="m7-credit-rollback",
                request_id="m7-credit-rollback-request",
            )

    async with session_factory() as session:
        account_count = await session.scalar(
            select(func.count())
            .select_from(AICreditAccount)
            .where(AICreditAccount.business_id == business_id)
        )

        adjustment_count = await session.scalar(
            select(func.count())
            .select_from(AICreditAdjustmentLedgerEntry)
            .where(AICreditAdjustmentLedgerEntry.business_id == business_id)
        )

        audit_count = await session.scalar(
            select(func.count())
            .select_from(AdminAuditEvent)
            .where(
                AdminAuditEvent.business_id == business_id,
                AdminAuditEvent.action == "ai.credit.adjust",
            )
        )

        assert account_count == 0
        assert adjustment_count == 0
        assert audit_count == 0
