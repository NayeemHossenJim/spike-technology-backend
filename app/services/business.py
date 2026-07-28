from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.base import utc_now
from app.models.business import Business, RoleAssignment, TenantRole
from app.models.subscription import (
    AI_FULL_RESPONSES_PER_PERIOD,
    TRIAL_DAYS,
    EntitlementKey,
    EntitlementSource,
    Subscription,
    SubscriptionEntitlement,
    SubscriptionStatus,
)
from app.models.user import User, UserRole
from app.schemas.business import BusinessCreate


class BusinessAlreadyExistsError(Exception):
    pass


class BusinessOnboardingForbiddenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class BusinessOnboardingResult:
    business: Business
    role_assignment: RoleAssignment
    subscription: Subscription


class BusinessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def onboard(
        self,
        *,
        user: User,
        payload: BusinessCreate,
    ) -> BusinessOnboardingResult:
        if user.role != UserRole.USER or not user.is_active or not user.is_verified:
            raise BusinessOnboardingForbiddenError

        # Serialize onboarding by user. Database uniqueness constraints remain the
        # final guard when two requests race before either transaction commits.
        locked_user_result = await self.session.execute(
            select(User).where(User.id == user.id).with_for_update()
        )
        locked_user = locked_user_result.scalar_one_or_none()
        if (
            locked_user is None
            or locked_user.role != UserRole.USER
            or not locked_user.is_active
            or not locked_user.is_verified
        ):
            raise BusinessOnboardingForbiddenError

        assignment_result = await self.session.execute(
            select(RoleAssignment.id).where(RoleAssignment.user_id == user.id)
        )
        business_result = await self.session.execute(
            select(Business.id).where(Business.owner_user_id == user.id)
        )
        if (
            assignment_result.scalar_one_or_none() is not None
            or business_result.scalar_one_or_none() is not None
        ):
            raise BusinessAlreadyExistsError

        now = utc_now()
        trial_ends_at = now + timedelta(days=TRIAL_DAYS)
        business = Business(
            owner_user_id=user.id,
            name=payload.name,
            industry=payload.industry if payload.industry is not None else user.industry,
        )
        assignment = RoleAssignment(
            business_id=business.id,
            user_id=user.id,
            role=TenantRole.OWNER,
        )
        subscription = Subscription(
            business_id=business.id,
            plan_id=None,
            status=SubscriptionStatus.TRIALING,
            trial_started_at=now,
            trial_ends_at=trial_ends_at,
            current_period_started_at=now,
            current_period_ends_at=trial_ends_at,
        )
        trial_ai_entitlement = SubscriptionEntitlement(
            business_id=business.id,
            subscription_id=subscription.id,
            key=EntitlementKey.AI_FULL_RESPONSES,
            source=EntitlementSource.TRIAL,
            is_enabled=True,
            limit_value=AI_FULL_RESPONSES_PER_PERIOD,
        )
        self.session.add_all([business, assignment, subscription, trial_ai_entitlement])
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise BusinessAlreadyExistsError from exc

        return BusinessOnboardingResult(
            business=business,
            role_assignment=assignment,
            subscription=subscription,
        )
