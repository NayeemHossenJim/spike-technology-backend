from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from app.models.dashboard import Dashboard
from app.models.subscription import EntitlementKey, Subscription
from app.schemas.dashboard import DashboardCreate, DashboardUpdate
from app.services.subscriptions import (
    CURRENT_SUBSCRIPTION_STATUSES,
    EntitlementDeniedError,
    EntitlementLimitReachedError,
    EntitlementService,
    SubscriptionInactiveError,
    SubscriptionRequiredError,
)
from app.services.tenant import TenantScope


class DashboardNotFoundError(Exception):
    pass


class DashboardConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DashboardPage:
    items: tuple[Dashboard, ...]
    total: int
    limit: int
    offset: int


class DashboardService:
    """Tenant-safe dashboard CRUD with serialized entitlement enforcement."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entitlements = EntitlementService(session)

    async def _lock_current_subscription(
        self,
        scope: TenantScope,
    ) -> Subscription:
        result = await self.session.execute(
            scope.select(
                Subscription,
                Subscription.status.in_(CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .with_for_update()
            .limit(1)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise SubscriptionRequiredError
        return subscription

    async def _usage(self, scope: TenantScope) -> int:
        value = await self.session.scalar(
            select(func.count())
            .select_from(Dashboard)
            .where(Dashboard.business_id == scope.business_id)
        )
        return int(value or 0)

    async def create(
        self,
        scope: TenantScope,
        *,
        created_by_user_id: UUID,
        payload: DashboardCreate,
    ) -> Dashboard:
        try:
            # Every dashboard creation for a tenant locks the same current
            # subscription row. This serializes count -> entitlement -> insert,
            # preventing concurrent requests from exceeding a finite plan cap.
            await self._lock_current_subscription(scope)
            current_usage = await self._usage(scope)
            await self.entitlements.require(
                scope,
                EntitlementKey.DASHBOARDS,
                current_usage=current_usage,
            )
        except (
            SubscriptionRequiredError,
            SubscriptionInactiveError,
            EntitlementDeniedError,
            EntitlementLimitReachedError,
        ):
            await self.session.rollback()
            raise

        dashboard = Dashboard(
            business_id=scope.business_id,
            created_by_user_id=created_by_user_id,
            dashboard_type=payload.dashboard_type,
            title=payload.title,
            configuration=payload.configuration,
        )
        self.session.add(dashboard)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DashboardConflictError from exc

        return dashboard

    async def get(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
    ) -> Dashboard:
        dashboard = await scope.get(
            self.session,
            Dashboard,
            dashboard_id,
        )
        if dashboard is None:
            raise DashboardNotFoundError
        return dashboard

    async def list(
        self,
        scope: TenantScope,
        *,
        limit: int,
        offset: int,
    ) -> DashboardPage:
        total = await self._usage(scope)

        result = await self.session.execute(
            scope.select(Dashboard)
            .order_by(
                Dashboard.updated_at.desc(),
                Dashboard.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        return DashboardPage(
            items=tuple(result.scalars().all()),
            total=total,
            limit=limit,
            offset=offset,
        )

    async def update(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
        *,
        payload: DashboardUpdate,
    ) -> Dashboard:
        dashboard = await self.get(scope, dashboard_id)

        values = payload.model_dump(exclude_unset=True)
        for field, value in values.items():
            setattr(dashboard, field, value)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DashboardConflictError from exc

        return dashboard

    async def delete(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
    ) -> None:
        dashboard = await self.get(scope, dashboard_id)
        await self.session.delete(dashboard)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DashboardConflictError from exc
