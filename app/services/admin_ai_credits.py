from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from app.models.ai import (
    AICreditAccount,
    AICreditAdjustmentLedgerEntry,
    AICreditAdjustmentReason,
)
from app.models.base import utc_now
from app.models.business import Business
from app.models.subscription import EntitlementKey, Subscription
from app.models.user import User, UserRole
from app.services.admin_audit import AdminAuditService
from app.services.ai_credits import (
    AICreditIdempotencyKeyError,
    digest_ai_idempotency_key,
)
from app.services.subscriptions import (
    CURRENT_SUBSCRIPTION_STATUSES,
    EntitlementService,
    SubscriptionService,
)
from app.services.tenant import TenantScope


class AdminAICreditAdjustmentError(Exception):
    pass


class AdminAICreditActorForbiddenError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditBusinessNotFoundError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditSubscriptionUnavailableError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditEntitlementUnavailableError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditUnlimitedEntitlementError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditAdjustmentConflictError(AdminAICreditAdjustmentError):
    pass


class AdminAICreditIdempotencyConflictError(AdminAICreditAdjustmentError):
    pass


@dataclass(frozen=True, slots=True)
class AdminAICreditAdjustmentResult:
    adjustment: AICreditAdjustmentLedgerEntry
    remaining_after: int
    replayed: bool


class AdminAICreditAdjustmentService:
    """Privileged, append-only AI credit limit adjustment service."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entitlements = EntitlementService(session)

    @staticmethod
    def _validate_actor(actor: User) -> None:
        if actor.role != UserRole.SUPER_ADMIN or not actor.is_active or not actor.is_verified:
            raise AdminAICreditActorForbiddenError

    async def _business_for_update(
        self,
        business_id: UUID,
    ) -> Business | None:
        result = await self.session.execute(
            select(Business).where(Business.id == business_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _existing_adjustment_for_update(
        self,
        *,
        business_id: UUID,
        digest: str,
    ) -> AICreditAdjustmentLedgerEntry | None:
        result = await self.session.execute(
            select(AICreditAdjustmentLedgerEntry)
            .where(
                AICreditAdjustmentLedgerEntry.business_id == business_id,
                AICreditAdjustmentLedgerEntry.idempotency_key_digest == digest,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _current_subscription_for_update(
        self,
        scope: TenantScope,
    ) -> Subscription | None:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(
                Subscription.created_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _account_for_update(
        self,
        *,
        scope: TenantScope,
        subscription: Subscription,
        period_started_at,
        period_ends_at,
        base_limit: int,
    ) -> AICreditAccount:
        result = await self.session.execute(
            scope.select(
                AICreditAccount,
                AICreditAccount.subscription_id == subscription.id,
                AICreditAccount.entitlement_key == EntitlementKey.AI_FULL_RESPONSES,
                AICreditAccount.period_started_at == period_started_at,
                AICreditAccount.period_ends_at == period_ends_at,
            ).with_for_update()
        )

        account = result.scalar_one_or_none()

        if account is None:
            account = AICreditAccount(
                business_id=scope.business_id,
                subscription_id=subscription.id,
                entitlement_key=EntitlementKey.AI_FULL_RESPONSES,
                period_started_at=period_started_at,
                period_ends_at=period_ends_at,
                limit_value=base_limit,
            )
            self.session.add(account)
            await self.session.flush()

        return account

    async def _adjustment_total(
        self,
        *,
        account: AICreditAccount,
    ) -> int:
        value = await self.session.scalar(
            select(
                func.coalesce(
                    func.sum(AICreditAdjustmentLedgerEntry.delta),
                    0,
                )
            ).where(
                AICreditAdjustmentLedgerEntry.business_id == account.business_id,
                AICreditAdjustmentLedgerEntry.account_id == account.id,
                AICreditAdjustmentLedgerEntry.subscription_id == account.subscription_id,
            )
        )

        return int(value or 0)

    async def adjust(
        self,
        *,
        actor: User,
        business_id: UUID,
        delta: int,
        reason_code: AICreditAdjustmentReason,
        idempotency_key: str,
        request_id: str | None,
    ) -> AdminAICreditAdjustmentResult:
        self._validate_actor(actor)

        if (
            isinstance(delta, bool)
            or not isinstance(delta, int)
            or delta == 0
            or delta < -1000
            or delta > 1000
        ):
            raise AdminAICreditAdjustmentConflictError

        try:
            reason = AICreditAdjustmentReason(reason_code)
            digest = digest_ai_idempotency_key(idempotency_key)

            business = await self._business_for_update(business_id)

            if business is None:
                raise AdminAICreditBusinessNotFoundError

            # Locking the business serializes all privileged credit
            # adjustments for the same tenant. This also makes replay
            # detection deterministic under concurrent requests.
            existing = await self._existing_adjustment_for_update(
                business_id=business_id,
                digest=digest,
            )

            if existing is not None:
                if (
                    existing.delta != delta
                    or AICreditAdjustmentReason(existing.reason_code) != reason
                ):
                    raise AdminAICreditIdempotencyConflictError

                remaining_after = (
                    existing.effective_limit_after
                    - existing.reserved_count
                    - existing.consumed_count
                )

                result = AdminAICreditAdjustmentResult(
                    adjustment=existing,
                    remaining_after=remaining_after,
                    replayed=True,
                )

                await self.session.commit()
                return result

            scope = TenantScope(business_id)

            subscription = await self._current_subscription_for_update(scope)

            if subscription is None:
                raise AdminAICreditSubscriptionUnavailableError

            adjusted_at = utc_now()

            access = SubscriptionService.evaluate_access(
                subscription,
                now=adjusted_at,
            )

            if (
                not access.active
                or access.period_started_at is None
                or access.period_ends_at is None
            ):
                raise AdminAICreditSubscriptionUnavailableError

            effective = await self.entitlements.effective_entitlements(
                scope,
                subscription,
            )

            entitlement = next(
                (item for item in effective if item.key == EntitlementKey.AI_FULL_RESPONSES),
                None,
            )

            if entitlement is None or not entitlement.is_enabled:
                raise AdminAICreditEntitlementUnavailableError

            if entitlement.limit_value is None:
                raise AdminAICreditUnlimitedEntitlementError

            base_limit = entitlement.limit_value

            account = await self._account_for_update(
                scope=scope,
                subscription=subscription,
                period_started_at=access.period_started_at,
                period_ends_at=access.period_ends_at,
                base_limit=base_limit,
            )

            adjustment_total = await self._adjustment_total(
                account=account,
            )

            effective_before = base_limit + adjustment_total
            used = account.reserved_count + account.consumed_count

            if effective_before < used:
                raise AdminAICreditAdjustmentConflictError

            effective_after = effective_before + delta

            # Never revoke already-consumed credits or credits reserved
            # by an in-flight AI execution.
            if effective_after < used or effective_after < 0:
                raise AdminAICreditAdjustmentConflictError

            account.limit_value = effective_after

            adjustment = AICreditAdjustmentLedgerEntry(
                business_id=business_id,
                account_id=account.id,
                subscription_id=subscription.id,
                entitlement_key=EntitlementKey.AI_FULL_RESPONSES,
                actor_user_id=actor.id,
                idempotency_key_digest=digest,
                delta=delta,
                reason_code=reason,
                request_id=request_id,
                base_limit_value=base_limit,
                effective_limit_before=effective_before,
                effective_limit_after=effective_after,
                reserved_count=account.reserved_count,
                consumed_count=account.consumed_count,
                adjusted_at=adjusted_at,
            )

            self.session.add(adjustment)
            await self.session.flush()

            await AdminAuditService(self.session).record(
                actor=actor,
                action="ai.credit.adjust",
                target_type="ai_credit_account",
                target_id=account.id,
                business_id=business_id,
                request_id=request_id,
                metadata={
                    "adjustment_id": str(adjustment.id),
                    "delta": delta,
                    "reason_code": reason.value,
                    "base_limit_value": base_limit,
                    "effective_limit_before": effective_before,
                    "effective_limit_after": effective_after,
                    "reserved_count": account.reserved_count,
                    "consumed_count": account.consumed_count,
                },
            )

            result = AdminAICreditAdjustmentResult(
                adjustment=adjustment,
                remaining_after=effective_after - used,
                replayed=False,
            )

            await self.session.commit()
            return result

        except AICreditIdempotencyKeyError:
            await self.session.rollback()
            raise
        except Exception:
            await self.session.rollback()
            raise
