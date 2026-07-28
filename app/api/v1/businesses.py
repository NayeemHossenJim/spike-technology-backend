from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant, get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.business import (
    BusinessContextRead,
    BusinessCreate,
    BusinessRead,
    RoleAssignmentRead,
)
from app.schemas.subscription import SubscriptionRead
from app.services.business import (
    BusinessAlreadyExistsError,
    BusinessOnboardingForbiddenError,
    BusinessService,
)
from app.services.subscriptions import SubscriptionService
from app.services.tenant import TenantContext

router = APIRouter(prefix="/businesses", tags=["Businesses"])


@router.post("", response_model=BusinessContextRead, status_code=status.HTTP_201_CREATED)
async def onboard_business(
    payload: BusinessCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BusinessContextRead:
    try:
        created = await BusinessService(session).onboard(
            user=current_user,
            payload=payload,
        )
    except BusinessAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account already belongs to a business.",
        ) from exc
    except BusinessOnboardingForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account cannot create a business.",
        ) from exc

    return BusinessContextRead(
        business=BusinessRead.model_validate(created.business),
        role_assignment=RoleAssignmentRead.model_validate(created.role_assignment),
        subscription=SubscriptionRead.model_validate(created.subscription),
    )


@router.get("/me", response_model=BusinessContextRead)
async def read_current_business(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BusinessContextRead:
    subscription = await SubscriptionService(session).get_current(tenant.scope)
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The business does not have a current subscription record.",
        )
    return BusinessContextRead(
        business=BusinessRead.model_validate(tenant.business),
        role_assignment=RoleAssignmentRead.model_validate(tenant.role_assignment),
        subscription=SubscriptionRead.model_validate(subscription),
    )
