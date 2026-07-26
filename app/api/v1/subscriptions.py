from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.db.session import get_session
from app.schemas.subscription import (
    EffectiveEntitlementRead,
    EntitlementSummaryRead,
    SubscriptionRead,
)
from app.services.subscriptions import (
    EntitlementService,
    SubscriptionRequiredError,
    SubscriptionService,
)
from app.services.tenant import TenantContext

subscription_router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])
entitlement_router = APIRouter(prefix="/entitlements", tags=["Entitlements"])


@subscription_router.get("/me", response_model=SubscriptionRead)
async def read_current_subscription(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionRead:
    subscription = await SubscriptionService(session).get_current(tenant.scope)
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current subscription was found.",
        )
    return SubscriptionRead.model_validate(subscription)


@subscription_router.get("/{subscription_id}", response_model=SubscriptionRead)
async def read_subscription(
    subscription_id: UUID,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionRead:
    subscription = await SubscriptionService(session).get_by_id(
        tenant.scope,
        subscription_id,
    )
    if subscription is None:
        # A cross-tenant ID is deliberately indistinguishable from an unknown ID.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found.",
        )
    return SubscriptionRead.model_validate(subscription)


@entitlement_router.get("/me", response_model=EntitlementSummaryRead)
async def read_current_entitlements(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EntitlementSummaryRead:
    try:
        summary = await EntitlementService(session).summarize(tenant.scope)
    except SubscriptionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No current subscription was found.",
        ) from exc

    return EntitlementSummaryRead(
        subscription_id=summary.subscription.id,
        subscription_status=summary.subscription.status,
        access_active=summary.access.active,
        access_reason=summary.access.reason,
        period_started_at=summary.access.period_started_at,
        period_ends_at=summary.access.period_ends_at,
        entitlements=[
            EffectiveEntitlementRead(
                key=item.key,
                is_enabled=item.is_enabled,
                limit_value=item.limit_value,
                source=item.source,
            )
            for item in summary.entitlements
        ],
    )
