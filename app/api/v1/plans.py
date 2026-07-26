from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.subscription import PlanEntitlementRead, PlanRead
from app.services.subscriptions import PlanService

router = APIRouter(prefix="/plans", tags=["Plans"])


@router.get("", response_model=list[PlanRead])
async def list_public_plans(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PlanRead]:
    results = await PlanService(session).list_public()
    return [
        PlanRead(
            id=item.plan.id,
            code=item.plan.code,
            name=item.plan.name,
            description=item.plan.description,
            monthly_price_cents=item.plan.monthly_price_cents,
            currency=item.plan.currency,
            is_custom_pricing=item.plan.is_custom_pricing,
            entitlements=[
                PlanEntitlementRead.model_validate(entitlement) for entitlement in item.entitlements
            ],
        )
        for item in results
    ]
