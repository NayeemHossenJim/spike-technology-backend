from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.business import Business
from app.models.subscription import Plan, Subscription
from app.services.subscriptions import (
    EffectiveEntitlement,
    EntitlementService,
    SubscriptionAccess,
    SubscriptionService,
)
from app.services.tenant import TenantScope


class AdminBusinessNotFoundError(Exception):
    pass


class AdminBusinessSubscriptionNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AdminSubscriptionRecord:
    subscription: Subscription
    plan: Plan | None
    access: SubscriptionAccess
    entitlements: tuple[EffectiveEntitlement, ...]


class AdminSubscriptionService:
    """Read-only platform support view over tenant subscription state.

    Paid lifecycle state remains Stripe-owned. This service deliberately
    performs no subscription, entitlement, billing, or Stripe mutations.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entitlements = EntitlementService(session)

    async def get_latest(
        self,
        *,
        business_id: UUID,
    ) -> AdminSubscriptionRecord:
        business = await self.session.get(Business, business_id)

        if business is None:
            raise AdminBusinessNotFoundError

        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.business_id == business_id)
            .order_by(
                Subscription.created_at.desc(),
                Subscription.id.desc(),
            )
            .limit(1)
        )
        subscription = result.scalar_one_or_none()

        if subscription is None:
            raise AdminBusinessSubscriptionNotFoundError

        plan: Plan | None = None

        if subscription.plan_id is not None:
            plan = await self.session.get(
                Plan,
                subscription.plan_id,
            )

        scope = TenantScope(business_id)

        access = SubscriptionService.evaluate_access(
            subscription,
        )
        entitlements = await self.entitlements.effective_entitlements(
            scope,
            subscription,
        )

        return AdminSubscriptionRecord(
            subscription=subscription,
            plan=plan,
            access=access,
            entitlements=entitlements,
        )
