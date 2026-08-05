from __future__ import annotations

import asyncio
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.models.ai import AICreditAccount, AICreditLedgerEntry, AICreditLedgerStatus
from app.models.base import utc_now
from app.models.subscription import Subscription, SubscriptionEntitlement
from app.services.ai_credits import (
    AICreditReservationNotFoundError,
    AICreditService,
    AICreditStateConflictError,
)
from app.services.subscriptions import (
    EntitlementLimitReachedError,
    SubscriptionInactiveError,
)
from app.services.tenant import TenantScope
from tests.conftest import InMemoryEmailSender


async def onboard_owner(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    business_name: str,
) -> tuple[TenantScope, UUID]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "AI Credit Owner",
            "email": email,
            "password": "CorrectHorseBattery9",
            "industry": "Technology",
            "job_role": "Executive / C-Suite",
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
            "password": "CorrectHorseBattery9",
        },
    )
    assert login.status_code == 200

    onboarding = await client.post(
        "/api/v1/businesses",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"name": business_name},
    )
    assert onboarding.status_code == 201
    payload = onboarding.json()
    return (
        TenantScope(UUID(payload["business"]["id"])),
        UUID(payload["subscription"]["id"]),
    )


@pytest.mark.integration
async def test_reservation_is_idempotent_and_stores_only_a_digest(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-idempotency@example.com",
        business_name="AI Idempotency Tenant",
    )
    raw_key = "  request-idempotency-key-001  "

    async with session_factory() as session:
        created = await AICreditService(session).reserve(
            scope,
            idempotency_key=raw_key,
        )
    async with session_factory() as session:
        replayed = await AICreditService(session).reserve(
            scope,
            idempotency_key=raw_key,
        )

    assert created.replayed is False
    assert replayed.replayed is True
    assert replayed.entry_id == created.entry_id
    assert replayed.status is AICreditLedgerStatus.RESERVED
    assert replayed.reserved_count == 1
    assert replayed.consumed_count == 0
    assert replayed.remaining == 14

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        entry = (await session.execute(select(AICreditLedgerEntry))).scalar_one()
        assert account.reserved_count == 1
        assert account.consumed_count == 0
        assert entry.idempotency_key_digest == sha256(raw_key.strip().encode()).hexdigest()
        assert raw_key.strip() not in entry.idempotency_key_digest


@pytest.mark.integration
async def test_consume_and_release_are_terminal_and_replay_safe(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-transitions@example.com",
        business_name="AI Transition Tenant",
    )

    async with session_factory() as session:
        service = AICreditService(session)
        consumed_reservation = await service.reserve(
            scope,
            idempotency_key="consume-me",
        )
        released_reservation = await service.reserve(
            scope,
            idempotency_key="release-me",
        )

    async with session_factory() as session:
        consumed = await AICreditService(session).consume(
            scope,
            entry_id=consumed_reservation.entry_id,
        )
    async with session_factory() as session:
        consumed_replay = await AICreditService(session).consume(
            scope,
            entry_id=consumed_reservation.entry_id,
        )
    assert consumed.status is AICreditLedgerStatus.CONSUMED
    assert consumed.replayed is False
    assert consumed_replay.replayed is True

    async with session_factory() as session:
        with pytest.raises(AICreditStateConflictError):
            await AICreditService(session).release(
                scope,
                entry_id=consumed_reservation.entry_id,
                reason="provider_timeout",
            )

    async with session_factory() as session:
        released = await AICreditService(session).release(
            scope,
            entry_id=released_reservation.entry_id,
            reason="  provider_timeout  ",
        )
    async with session_factory() as session:
        released_replay = await AICreditService(session).release(
            scope,
            entry_id=released_reservation.entry_id,
            reason="a_later_retry_reason",
        )
    assert released.status is AICreditLedgerStatus.RELEASED
    assert released.release_reason == "provider_timeout"
    assert released.replayed is False
    assert released_replay.release_reason == "provider_timeout"
    assert released_replay.replayed is True

    async with session_factory() as session:
        with pytest.raises(AICreditStateConflictError):
            await AICreditService(session).consume(
                scope,
                entry_id=released_reservation.entry_id,
            )

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        entries = (
            (
                await session.execute(
                    select(AICreditLedgerEntry).order_by(AICreditLedgerEntry.reserved_at)
                )
            )
            .scalars()
            .all()
        )
        assert account.reserved_count == 0
        assert account.consumed_count == 1
        assert {AICreditLedgerStatus(item.status) for item in entries} == {
            AICreditLedgerStatus.CONSUMED,
            AICreditLedgerStatus.RELEASED,
        }


@pytest.mark.integration
async def test_concurrent_reservations_cannot_overspend(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, subscription_id = await onboard_owner(
        client,
        email_sender,
        email="ai-concurrency@example.com",
        business_name="AI Concurrency Tenant",
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
        entitlement.limit_value = 2
        await session.commit()

    async def reserve_once(sequence: int):
        async with session_factory() as session:
            return await AICreditService(session).reserve(
                scope,
                idempotency_key=f"parallel-request-{sequence}",
            )

    results = await asyncio.gather(
        *(reserve_once(sequence) for sequence in range(5)),
        return_exceptions=True,
    )
    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]

    assert len(successes) == 2
    assert len(failures) == 3
    assert all(isinstance(item, EntitlementLimitReachedError) for item in failures)

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        ledger_count = await session.scalar(select(func.count()).select_from(AICreditLedgerEntry))
        assert account.limit_value == 2
        assert account.reserved_count == 2
        assert account.consumed_count == 0
        assert ledger_count == 2


@pytest.mark.integration
async def test_ledger_operations_are_tenant_isolated(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope_a, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-tenant-a@example.com",
        business_name="AI Tenant A",
    )
    scope_b, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-tenant-b@example.com",
        business_name="AI Tenant B",
    )

    async with session_factory() as session:
        reservation_a = await AICreditService(session).reserve(
            scope_a,
            idempotency_key="same-key-across-tenants",
        )
    async with session_factory() as session:
        reservation_b = await AICreditService(session).reserve(
            scope_b,
            idempotency_key="same-key-across-tenants",
        )

    assert reservation_a.entry_id != reservation_b.entry_id

    async with session_factory() as session:
        with pytest.raises(AICreditReservationNotFoundError):
            await AICreditService(session).consume(
                scope_b,
                entry_id=reservation_a.entry_id,
            )
    async with session_factory() as session:
        with pytest.raises(AICreditReservationNotFoundError):
            await AICreditService(session).release(
                scope_a,
                entry_id=reservation_b.entry_id,
                reason="provider_error",
            )

    async with session_factory() as session:
        consumed = await AICreditService(session).consume(
            scope_a,
            entry_id=reservation_a.entry_id,
        )
    async with session_factory() as session:
        released = await AICreditService(session).release(
            scope_b,
            entry_id=reservation_b.entry_id,
            reason="provider_error",
        )
    assert consumed.business_id == scope_a.business_id
    assert released.business_id == scope_b.business_id


@pytest.mark.integration
async def test_inactive_subscription_creates_no_credit_records(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, subscription_id = await onboard_owner(
        client,
        email_sender,
        email="ai-expired@example.com",
        business_name="AI Expired Tenant",
    )

    async with session_factory() as session:
        subscription = await session.get(Subscription, subscription_id)
        assert subscription is not None
        subscription.trial_ends_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(SubscriptionInactiveError, match="trial_expired"):
            await AICreditService(session).reserve(
                scope,
                idempotency_key="expired-trial-request",
            )

    async with session_factory() as session:
        account_count = await session.scalar(select(func.count()).select_from(AICreditAccount))
        ledger_count = await session.scalar(select(func.count()).select_from(AICreditLedgerEntry))
        assert account_count == 0
        assert ledger_count == 0


@pytest.mark.integration
async def test_concurrent_same_idempotency_key_creates_one_reservation(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-same-key-race@example.com",
        business_name="AI Same Key Race Tenant",
    )

    start = asyncio.Event()

    async def reserve_once():
        async with session_factory() as session:
            await start.wait()
            return await AICreditService(session).reserve(
                scope,
                idempotency_key="same-concurrent-key",
            )

    tasks = [
        asyncio.create_task(reserve_once()),
        asyncio.create_task(reserve_once()),
    ]

    await asyncio.sleep(0)
    start.set()
    results = await asyncio.gather(*tasks)

    assert len({item.entry_id for item in results}) == 1
    assert {item.replayed for item in results} == {False, True}
    assert all(item.status is AICreditLedgerStatus.RESERVED for item in results)

    async with session_factory() as session:
        account_count = await session.scalar(select(func.count()).select_from(AICreditAccount))
        entry_count = await session.scalar(select(func.count()).select_from(AICreditLedgerEntry))
        account = (await session.execute(select(AICreditAccount))).scalar_one()

        assert account_count == 1
        assert entry_count == 1
        assert account.reserved_count == 1
        assert account.consumed_count == 0


@pytest.mark.integration
async def test_concurrent_consume_and_release_allow_one_terminal_transition(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scope, _ = await onboard_owner(
        client,
        email_sender,
        email="ai-terminal-race@example.com",
        business_name="AI Terminal Race Tenant",
    )

    async with session_factory() as session:
        reservation = await AICreditService(session).reserve(
            scope,
            idempotency_key="terminal-race-reservation",
        )

    start = asyncio.Event()

    async def consume_once():
        async with session_factory() as session:
            await start.wait()
            return await AICreditService(session).consume(
                scope,
                entry_id=reservation.entry_id,
            )

    async def release_once():
        async with session_factory() as session:
            await start.wait()
            return await AICreditService(session).release(
                scope,
                entry_id=reservation.entry_id,
                reason="provider_race",
            )

    tasks = [
        asyncio.create_task(consume_once()),
        asyncio.create_task(release_once()),
    ]

    await asyncio.sleep(0)
    start.set()

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    successes = [item for item in results if not isinstance(item, Exception)]
    failures = [item for item in results if isinstance(item, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], AICreditStateConflictError)

    winner = successes[0]
    assert winner.replayed is False
    assert winner.status in {
        AICreditLedgerStatus.CONSUMED,
        AICreditLedgerStatus.RELEASED,
    }

    async with session_factory() as session:
        account = (await session.execute(select(AICreditAccount))).scalar_one()
        entry = (await session.execute(select(AICreditLedgerEntry))).scalar_one()

        assert account.reserved_count == 0
        assert AICreditLedgerStatus(entry.status) is winner.status

        if winner.status is AICreditLedgerStatus.CONSUMED:
            assert account.consumed_count == 1
            assert entry.consumed_at is not None
            assert entry.released_at is None
            assert entry.release_reason is None
        else:
            assert account.consumed_count == 0
            assert entry.consumed_at is None
            assert entry.released_at is not None
            assert entry.release_reason == "provider_race"
