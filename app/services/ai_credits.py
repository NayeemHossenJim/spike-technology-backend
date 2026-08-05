from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AICreditAccount, AICreditLedgerEntry, AICreditLedgerStatus
from app.models.base import utc_now
from app.models.subscription import EntitlementKey, Subscription
from app.services.subscriptions import (
    CURRENT_SUBSCRIPTION_STATUSES,
    EntitlementDeniedError,
    EntitlementLimitReachedError,
    EntitlementService,
    SubscriptionInactiveError,
    SubscriptionRequiredError,
)
from app.services.tenant import TenantScope

AI_IDEMPOTENCY_KEY_MAX_LENGTH = 255
AI_RELEASE_REASON_MAX_LENGTH = 255


class AICreditError(Exception):
    pass


class AICreditIdempotencyKeyError(AICreditError, ValueError):
    pass


class AICreditReleaseReasonError(AICreditError, ValueError):
    pass


class AICreditReservationNotFoundError(AICreditError):
    pass


class AICreditStateConflictError(AICreditError):
    pass


class AICreditIntegrityError(AICreditError):
    pass


@dataclass(frozen=True, slots=True)
class AICreditLedgerSnapshot:
    entry_id: UUID
    account_id: UUID
    business_id: UUID
    subscription_id: UUID
    status: AICreditLedgerStatus
    limit_value: int | None
    reserved_count: int
    consumed_count: int
    remaining: int | None
    period_started_at: datetime
    period_ends_at: datetime
    reserved_at: datetime
    consumed_at: datetime | None
    released_at: datetime | None
    release_reason: str | None
    replayed: bool


def digest_ai_idempotency_key(idempotency_key: str) -> str:
    normalized = idempotency_key.strip()
    if not normalized or len(normalized) > AI_IDEMPOTENCY_KEY_MAX_LENGTH:
        raise AICreditIdempotencyKeyError
    return sha256(normalized.encode("utf-8")).hexdigest()


def normalize_ai_release_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized or len(normalized) > AI_RELEASE_REASON_MAX_LENGTH:
        raise AICreditReleaseReasonError
    return normalized


class AICreditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entitlements = EntitlementService(session)

    @staticmethod
    def _snapshot(
        entry: AICreditLedgerEntry,
        account: AICreditAccount,
        *,
        replayed: bool,
    ) -> AICreditLedgerSnapshot:
        used = account.reserved_count + account.consumed_count
        remaining = None if account.limit_value is None else account.limit_value - used
        return AICreditLedgerSnapshot(
            entry_id=entry.id,
            account_id=account.id,
            business_id=entry.business_id,
            subscription_id=entry.subscription_id,
            status=AICreditLedgerStatus(entry.status),
            limit_value=account.limit_value,
            reserved_count=account.reserved_count,
            consumed_count=account.consumed_count,
            remaining=remaining,
            period_started_at=account.period_started_at,
            period_ends_at=account.period_ends_at,
            reserved_at=entry.reserved_at,
            consumed_at=entry.consumed_at,
            released_at=entry.released_at,
            release_reason=entry.release_reason,
            replayed=replayed,
        )

    async def _entry_by_digest_for_update(
        self,
        scope: TenantScope,
        digest: str,
    ) -> AICreditLedgerEntry | None:
        result = await self.session.execute(
            scope.select(
                AICreditLedgerEntry,
                AICreditLedgerEntry.idempotency_key_digest == digest,
            ).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _entry_by_id_for_update(
        self,
        scope: TenantScope,
        entry_id: UUID,
    ) -> AICreditLedgerEntry | None:
        result = await self.session.execute(
            scope.select(
                AICreditLedgerEntry,
                AICreditLedgerEntry.id == entry_id,
            ).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _account_for_entry_for_update(
        self,
        scope: TenantScope,
        entry: AICreditLedgerEntry,
    ) -> AICreditAccount:
        result = await self.session.execute(
            scope.select(
                AICreditAccount,
                AICreditAccount.id == entry.account_id,
                AICreditAccount.subscription_id == entry.subscription_id,
            ).with_for_update()
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise AICreditIntegrityError
        return account

    async def _current_subscription_for_update(
        self,
        scope: TenantScope,
    ) -> Subscription | None:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _existing_snapshot(
        self,
        scope: TenantScope,
        digest: str,
    ) -> AICreditLedgerSnapshot | None:
        entry = await self._entry_by_digest_for_update(scope, digest)
        if entry is None:
            return None
        account = await self._account_for_entry_for_update(scope, entry)
        snapshot = self._snapshot(entry, account, replayed=True)
        await self.session.commit()
        return snapshot

    async def reserve(
        self,
        scope: TenantScope,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> AICreditLedgerSnapshot:
        digest = digest_ai_idempotency_key(idempotency_key)
        reserved_at = now or utc_now()

        try:
            existing = await self._existing_snapshot(scope, digest)
            if existing is not None:
                return existing

            subscription = await self._current_subscription_for_update(scope)
            if subscription is None:
                raise SubscriptionRequiredError

            access = self.entitlements.subscriptions.evaluate_access(
                subscription,
                now=reserved_at,
            )
            if not access.active:
                raise SubscriptionInactiveError(access.reason)
            if access.period_started_at is None or access.period_ends_at is None:
                raise AICreditIntegrityError

            # Recheck after obtaining the subscription lock. A concurrent request
            # with the same key may have committed while this request was waiting.
            existing = await self._existing_snapshot(scope, digest)
            if existing is not None:
                return existing

            effective = await self.entitlements.effective_entitlements(scope, subscription)
            entitlement = next(
                (item for item in effective if item.key == EntitlementKey.AI_FULL_RESPONSES),
                None,
            )
            if entitlement is None or not entitlement.is_enabled:
                raise EntitlementDeniedError

            account_result = await self.session.execute(
                scope.select(
                    AICreditAccount,
                    AICreditAccount.subscription_id == subscription.id,
                    AICreditAccount.entitlement_key == EntitlementKey.AI_FULL_RESPONSES,
                    AICreditAccount.period_started_at == access.period_started_at,
                    AICreditAccount.period_ends_at == access.period_ends_at,
                ).with_for_update()
            )
            account = account_result.scalar_one_or_none()
            if account is None:
                account = AICreditAccount(
                    business_id=scope.business_id,
                    subscription_id=subscription.id,
                    entitlement_key=EntitlementKey.AI_FULL_RESPONSES,
                    period_started_at=access.period_started_at,
                    period_ends_at=access.period_ends_at,
                    limit_value=entitlement.limit_value,
                )
                self.session.add(account)
                await self.session.flush()

            used = account.reserved_count + account.consumed_count
            current_limit = entitlement.limit_value
            if current_limit is not None and used >= current_limit:
                raise EntitlementLimitReachedError

            if account.limit_value != current_limit:
                if current_limit is not None and used > current_limit:
                    raise EntitlementLimitReachedError
                account.limit_value = current_limit

            account.reserved_count += 1
            entry = AICreditLedgerEntry(
                business_id=scope.business_id,
                account_id=account.id,
                subscription_id=subscription.id,
                entitlement_key=EntitlementKey.AI_FULL_RESPONSES,
                idempotency_key_digest=digest,
                status=AICreditLedgerStatus.RESERVED,
                quantity=1,
                reserved_at=reserved_at,
            )
            self.session.add(entry)
            await self.session.flush()

            snapshot = self._snapshot(entry, account, replayed=False)
            await self.session.commit()
            return snapshot
        except IntegrityError as exc:
            await self.session.rollback()
            try:
                duplicate = await self._existing_snapshot(scope, digest)
                if duplicate is not None:
                    return duplicate
            except Exception:
                await self.session.rollback()
                raise
            raise AICreditIntegrityError from exc
        except Exception:
            await self.session.rollback()
            raise

    async def consume(
        self,
        scope: TenantScope,
        *,
        entry_id: UUID,
        now: datetime | None = None,
    ) -> AICreditLedgerSnapshot:
        consumed_at = now or utc_now()
        try:
            entry = await self._entry_by_id_for_update(scope, entry_id)
            if entry is None:
                raise AICreditReservationNotFoundError
            account = await self._account_for_entry_for_update(scope, entry)
            status = AICreditLedgerStatus(entry.status)

            if status is AICreditLedgerStatus.CONSUMED:
                snapshot = self._snapshot(entry, account, replayed=True)
                await self.session.commit()
                return snapshot
            if status is AICreditLedgerStatus.RELEASED:
                raise AICreditStateConflictError
            if account.reserved_count < 1:
                raise AICreditIntegrityError

            account.reserved_count -= 1
            account.consumed_count += 1
            entry.status = AICreditLedgerStatus.CONSUMED
            entry.consumed_at = consumed_at
            entry.released_at = None
            entry.release_reason = None

            snapshot = self._snapshot(entry, account, replayed=False)
            await self.session.commit()
            return snapshot
        except Exception:
            await self.session.rollback()
            raise

    async def release(
        self,
        scope: TenantScope,
        *,
        entry_id: UUID,
        reason: str,
        now: datetime | None = None,
    ) -> AICreditLedgerSnapshot:
        normalized_reason = normalize_ai_release_reason(reason)
        released_at = now or utc_now()
        try:
            entry = await self._entry_by_id_for_update(scope, entry_id)
            if entry is None:
                raise AICreditReservationNotFoundError
            account = await self._account_for_entry_for_update(scope, entry)
            status = AICreditLedgerStatus(entry.status)

            if status is AICreditLedgerStatus.RELEASED:
                snapshot = self._snapshot(entry, account, replayed=True)
                await self.session.commit()
                return snapshot
            if status is AICreditLedgerStatus.CONSUMED:
                raise AICreditStateConflictError
            if account.reserved_count < 1:
                raise AICreditIntegrityError

            account.reserved_count -= 1
            entry.status = AICreditLedgerStatus.RELEASED
            entry.consumed_at = None
            entry.released_at = released_at
            entry.release_reason = normalized_reason

            snapshot = self._snapshot(entry, account, replayed=False)
            await self.session.commit()
            return snapshot
        except Exception:
            await self.session.rollback()
            raise
