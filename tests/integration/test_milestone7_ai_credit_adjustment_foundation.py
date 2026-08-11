from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.ai import (
    AICreditAccount,
    AICreditAdjustmentLedgerEntry,
    AICreditAdjustmentReason,
)
from app.models.base import utc_now
from app.models.subscription import SubscriptionEntitlement
from app.services.ai_credits import AICreditService
from tests.integration.test_milestone5_ai_credits import onboard_owner


@pytest.mark.integration
async def test_adjustment_ledger_extends_effective_credit_limit(
    client,
    email_sender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, subscription_id = await onboard_owner(
        client,
        email_sender,
        email="m7-adjustment-foundation@example.com",
        business_name="M7 Adjustment Foundation",
    )

    async with session_factory() as session:
        entitlement = (
            await session.execute(
                select(SubscriptionEntitlement).where(
                    SubscriptionEntitlement.business_id == scope.business_id,
                    SubscriptionEntitlement.subscription_id == subscription_id,
                )
            )
        ).scalar_one()

        entitlement.limit_value = 1
        await session.commit()

    async with session_factory() as session:
        first = await AICreditService(session).reserve(
            scope,
            idempotency_key="foundation-first",
        )

    assert first.limit_value == 1
    assert first.remaining == 0

    async with session_factory() as session:
        account = (
            await session.execute(
                select(AICreditAccount).where(
                    AICreditAccount.business_id == scope.business_id,
                )
            )
        ).scalar_one()

        now = utc_now()

        session.add(
            AICreditAdjustmentLedgerEntry(
                business_id=scope.business_id,
                account_id=account.id,
                subscription_id=subscription_id,
                entitlement_key=account.entitlement_key,
                actor_user_id=uuid4(),
                idempotency_key_digest="a" * 64,
                delta=1,
                reason_code=AICreditAdjustmentReason.SUPPORT_CREDIT,
                request_id="m7-foundation-test",
                base_limit_value=1,
                effective_limit_before=1,
                effective_limit_after=2,
                reserved_count=1,
                consumed_count=0,
                adjusted_at=now,
            )
        )

        await session.commit()

    async with session_factory() as session:
        second = await AICreditService(session).reserve(
            scope,
            idempotency_key="foundation-second",
        )

    assert second.limit_value == 2
    assert second.reserved_count == 2
    assert second.remaining == 0

    async with session_factory() as session:
        history = await AICreditService(session).usage_history(
            scope,
            limit=50,
            offset=0,
        )

    assert history.current.limit_value == 2
    assert history.current.reserved_count == 2
    assert history.current.remaining == 0


@pytest.mark.integration
async def test_adjustment_ledger_rows_are_database_immutable(
    client,
    email_sender,
    session_factory: async_sessionmaker[AsyncSession],
    test_engine: AsyncEngine,
) -> None:
    scope, subscription_id = await onboard_owner(
        client,
        email_sender,
        email="m7-adjustment-immutable@example.com",
        business_name="M7 Adjustment Immutable",
    )

    async with session_factory() as session:
        await AICreditService(session).reserve(
            scope,
            idempotency_key="immutable-account-creator",
        )

        account = (
            await session.execute(
                select(AICreditAccount).where(
                    AICreditAccount.business_id == scope.business_id,
                )
            )
        ).scalar_one()

        entry = AICreditAdjustmentLedgerEntry(
            business_id=scope.business_id,
            account_id=account.id,
            subscription_id=subscription_id,
            entitlement_key=account.entitlement_key,
            actor_user_id=uuid4(),
            idempotency_key_digest="b" * 64,
            delta=1,
            reason_code=AICreditAdjustmentReason.SERVICE_RECOVERY,
            request_id="m7-immutable-test",
            base_limit_value=15,
            effective_limit_before=15,
            effective_limit_after=16,
            reserved_count=1,
            consumed_count=0,
            adjusted_at=utc_now(),
        )

        session.add(entry)
        await session.commit()
        entry_id = entry.id

    async with test_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    AICreditAdjustmentLedgerEntry.__table__.update()
                    .where(AICreditAdjustmentLedgerEntry.id == entry_id)
                    .values(delta=2)
                )
        finally:
            if transaction.is_active:
                await transaction.rollback()

    async with test_engine.connect() as connection:
        transaction = await connection.begin()

        try:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    AICreditAdjustmentLedgerEntry.__table__.delete().where(
                        AICreditAdjustmentLedgerEntry.id == entry_id
                    )
                )
        finally:
            if transaction.is_active:
                await transaction.rollback()

    async with session_factory() as session:
        persisted = await session.get(
            AICreditAdjustmentLedgerEntry,
            entry_id,
        )

        assert persisted is not None
        assert persisted.delta == 1
