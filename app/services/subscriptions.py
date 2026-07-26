from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.base import utc_now
from app.models.subscription import (
    EntitlementKey,
    EntitlementSource,
    Plan,
    PlanEntitlement,
    Subscription,
    SubscriptionEntitlement,
    SubscriptionStatus,
)
from app.services.tenant import TenantScope

CURRENT_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.UNPAID,
    SubscriptionStatus.PAUSED,
    SubscriptionStatus.INCOMPLETE,
)


class SubscriptionRequiredError(Exception):
    pass


class SubscriptionInactiveError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EntitlementDeniedError(Exception):
    pass


class EntitlementLimitReachedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PlanWithEntitlements:
    plan: Plan
    entitlements: tuple[PlanEntitlement, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionAccess:
    active: bool
    reason: str
    period_started_at: datetime | None
    period_ends_at: datetime | None


@dataclass(frozen=True, slots=True)
class EffectiveEntitlement:
    key: EntitlementKey
    is_enabled: bool
    limit_value: int | None
    source: EntitlementSource


@dataclass(frozen=True, slots=True)
class EntitlementSummary:
    subscription: Subscription
    access: SubscriptionAccess
    entitlements: tuple[EffectiveEntitlement, ...]


@dataclass(frozen=True, slots=True)
class EntitlementGrant:
    subscription_id: UUID
    business_id: UUID
    key: EntitlementKey
    limit_value: int | None
    remaining: int | None
    period_started_at: datetime | None
    period_ends_at: datetime | None


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_public(self) -> list[PlanWithEntitlements]:
        plans_result = await self.session.execute(
            select(Plan)
            .where(Plan.is_active.is_(True), Plan.is_public.is_(True))
            .order_by(Plan.sort_order, Plan.name)
        )
        plans = list(plans_result.scalars().all())
        if not plans:
            return []

        plan_ids = [plan.id for plan in plans]
        entitlements_result = await self.session.execute(
            select(PlanEntitlement)
            .where(PlanEntitlement.plan_id.in_(plan_ids))
            .order_by(PlanEntitlement.key)
        )
        by_plan: dict[UUID, list[PlanEntitlement]] = {plan_id: [] for plan_id in plan_ids}
        for entitlement in entitlements_result.scalars().all():
            by_plan[entitlement.plan_id].append(entitlement)
        return [
            PlanWithEntitlements(
                plan=plan,
                entitlements=tuple(by_plan[plan.id]),
            )
            for plan in plans
        ]


class SubscriptionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current(self, scope: TenantScope) -> Subscription | None:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(
        self,
        scope: TenantScope,
        subscription_id: UUID,
    ) -> Subscription | None:
        return await scope.get(self.session, Subscription, subscription_id)

    @staticmethod
    def evaluate_access(
        subscription: Subscription,
        *,
        now: datetime | None = None,
    ) -> SubscriptionAccess:
        checked_at = now or utc_now()
        subscription_status = SubscriptionStatus(subscription.status)

        if subscription_status == SubscriptionStatus.TRIALING:
            if not subscription.trial_started_at or not subscription.trial_ends_at:
                return SubscriptionAccess(False, "trial_period_not_configured", None, None)
            if checked_at < subscription.trial_started_at:
                return SubscriptionAccess(
                    False,
                    "trial_not_started",
                    subscription.trial_started_at,
                    subscription.trial_ends_at,
                )
            return SubscriptionAccess(
                checked_at < subscription.trial_ends_at,
                "active" if checked_at < subscription.trial_ends_at else "trial_expired",
                subscription.trial_started_at,
                subscription.trial_ends_at,
            )

        if subscription_status == SubscriptionStatus.ACTIVE:
            if (
                not subscription.current_period_started_at
                or not subscription.current_period_ends_at
            ):
                return SubscriptionAccess(False, "billing_period_not_configured", None, None)
            if checked_at < subscription.current_period_started_at:
                return SubscriptionAccess(
                    False,
                    "billing_period_not_started",
                    subscription.current_period_started_at,
                    subscription.current_period_ends_at,
                )
            return SubscriptionAccess(
                checked_at < subscription.current_period_ends_at,
                "active" if checked_at < subscription.current_period_ends_at else "period_expired",
                subscription.current_period_started_at,
                subscription.current_period_ends_at,
            )

        return SubscriptionAccess(
            False,
            f"subscription_{subscription_status.value}",
            subscription.current_period_started_at,
            subscription.current_period_ends_at,
        )


class EntitlementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.subscriptions = SubscriptionService(session)

    async def _effective_entitlements(
        self,
        scope: TenantScope,
        subscription: Subscription,
    ) -> tuple[EffectiveEntitlement, ...]:
        effective: dict[EntitlementKey, EffectiveEntitlement] = {}

        if subscription.plan_id:
            plan_result = await self.session.execute(
                select(PlanEntitlement).where(PlanEntitlement.plan_id == subscription.plan_id)
            )
            for entitlement in plan_result.scalars().all():
                entitlement_key = EntitlementKey(entitlement.key)
                effective[entitlement_key] = EffectiveEntitlement(
                    key=entitlement_key,
                    is_enabled=entitlement.is_enabled,
                    limit_value=entitlement.limit_value,
                    source=EntitlementSource.PLAN,
                )

        subscription_result = await self.session.execute(
            scope.select(
                SubscriptionEntitlement,
                SubscriptionEntitlement.subscription_id == subscription.id,
            )
        )
        for entitlement in subscription_result.scalars().all():
            entitlement_key = EntitlementKey(entitlement.key)
            effective[entitlement_key] = EffectiveEntitlement(
                key=entitlement_key,
                is_enabled=entitlement.is_enabled,
                limit_value=entitlement.limit_value,
                source=EntitlementSource(entitlement.source),
            )

        return tuple(sorted(effective.values(), key=lambda item: item.key.value))

    async def summarize(self, scope: TenantScope) -> EntitlementSummary:
        subscription = await self.subscriptions.get_current(scope)
        if subscription is None:
            raise SubscriptionRequiredError
        return EntitlementSummary(
            subscription=subscription,
            access=self.subscriptions.evaluate_access(subscription),
            entitlements=await self._effective_entitlements(scope, subscription),
        )

    async def require(
        self,
        scope: TenantScope,
        key: EntitlementKey,
        *,
        current_usage: int,
    ) -> EntitlementGrant:
        if current_usage < 0:
            raise ValueError("current_usage cannot be negative")

        summary = await self.summarize(scope)
        if not summary.access.active:
            raise SubscriptionInactiveError(summary.access.reason)

        entitlement = next(
            (item for item in summary.entitlements if item.key == key),
            None,
        )
        if entitlement is None or not entitlement.is_enabled:
            raise EntitlementDeniedError
        if entitlement.limit_value is not None and current_usage >= entitlement.limit_value:
            raise EntitlementLimitReachedError

        remaining = (
            None if entitlement.limit_value is None else entitlement.limit_value - current_usage
        )
        return EntitlementGrant(
            subscription_id=summary.subscription.id,
            business_id=summary.subscription.business_id,
            key=key,
            limit_value=entitlement.limit_value,
            remaining=remaining,
            period_started_at=summary.access.period_started_at,
            period_ends_at=summary.access.period_ends_at,
        )
