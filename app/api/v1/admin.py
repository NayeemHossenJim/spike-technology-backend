from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db.session import get_session
from app.models.user import User, UserRole
from app.schemas.admin import (
    AdminAccountActionRead,
    AdminAccountActionRequest,
    AdminAICreditAdjustmentRead,
    AdminAICreditAdjustmentRequest,
    AdminBusinessPageRead,
    AdminBusinessRead,
    AdminEffectiveEntitlementRead,
    AdminPlanRead,
    AdminSubscriptionAccessRead,
    AdminSubscriptionRead,
    AdminUserPageRead,
    AdminUserRead,
)
from app.services.admin import AdminBusinessRecord, AdminService
from app.services.admin_accounts import (
    AdminAccountActorForbiddenError,
    AdminAccountService,
    AdminAccountTargetForbiddenError,
    AdminAccountTargetNotFoundError,
)
from app.services.admin_ai_credits import (
    AdminAICreditActorForbiddenError,
    AdminAICreditAdjustmentConflictError,
    AdminAICreditAdjustmentResult,
    AdminAICreditAdjustmentService,
    AdminAICreditBusinessNotFoundError,
    AdminAICreditEntitlementUnavailableError,
    AdminAICreditIdempotencyConflictError,
    AdminAICreditSubscriptionUnavailableError,
    AdminAICreditUnlimitedEntitlementError,
)
from app.services.admin_subscriptions import (
    AdminBusinessNotFoundError,
    AdminBusinessSubscriptionNotFoundError,
    AdminSubscriptionRecord,
    AdminSubscriptionService,
)
from app.services.ai_credits import AICreditIdempotencyKeyError

router = APIRouter(prefix="/admin", tags=["Admin"])


PlatformSupportUser = Annotated[
    User,
    Depends(
        require_roles(
            UserRole.SUPER_ADMIN,
            UserRole.CUSTOMER_SERVICE,
        )
    ),
]

SuperAdminUser = Annotated[
    User,
    Depends(
        require_roles(
            UserRole.SUPER_ADMIN,
        )
    ),
]


def _business_read(item: AdminBusinessRecord) -> AdminBusinessRead:
    return AdminBusinessRead(
        id=item.business.id,
        name=item.business.name,
        industry=item.business.industry,
        owner_user_id=item.business.owner_user_id,
        owner=AdminUserRead.model_validate(item.owner),
        role_assignment_role=item.role_assignment.role,
        role_assignment_is_active=item.role_assignment.is_active,
        created_at=item.business.created_at,
        updated_at=item.business.updated_at,
    )


def _subscription_read(
    item: AdminSubscriptionRecord,
) -> AdminSubscriptionRead:
    subscription = item.subscription

    plan = (
        AdminPlanRead(
            id=item.plan.id,
            code=item.plan.code,
            name=item.plan.name,
            monthly_price_cents=item.plan.monthly_price_cents,
            currency=item.plan.currency,
            is_custom_pricing=item.plan.is_custom_pricing,
        )
        if item.plan is not None
        else None
    )

    return AdminSubscriptionRead(
        id=subscription.id,
        business_id=subscription.business_id,
        plan=plan,
        status=subscription.status,
        trial_started_at=subscription.trial_started_at,
        trial_ends_at=subscription.trial_ends_at,
        current_period_started_at=(subscription.current_period_started_at),
        current_period_ends_at=subscription.current_period_ends_at,
        cancel_at_period_end=subscription.cancel_at_period_end,
        canceled_at=subscription.canceled_at,
        ended_at=subscription.ended_at,
        stripe_managed=(subscription.stripe_subscription_id is not None),
        last_stripe_synced_at=subscription.last_stripe_synced_at,
        access=AdminSubscriptionAccessRead(
            active=item.access.active,
            reason=item.access.reason,
            period_started_at=item.access.period_started_at,
            period_ends_at=item.access.period_ends_at,
        ),
        entitlements=[
            AdminEffectiveEntitlementRead(
                key=entitlement.key,
                is_enabled=entitlement.is_enabled,
                limit_value=entitlement.limit_value,
                source=entitlement.source,
            )
            for entitlement in item.entitlements
        ],
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


def _ai_credit_adjustment_read(
    result: AdminAICreditAdjustmentResult,
) -> AdminAICreditAdjustmentRead:
    adjustment = result.adjustment

    return AdminAICreditAdjustmentRead(
        id=adjustment.id,
        business_id=adjustment.business_id,
        account_id=adjustment.account_id,
        subscription_id=adjustment.subscription_id,
        delta=adjustment.delta,
        reason_code=adjustment.reason_code,
        base_limit_value=adjustment.base_limit_value,
        effective_limit_before=adjustment.effective_limit_before,
        effective_limit_after=adjustment.effective_limit_after,
        reserved_count=adjustment.reserved_count,
        consumed_count=adjustment.consumed_count,
        remaining_after=result.remaining_after,
        adjusted_at=adjustment.adjusted_at,
        replayed=result.replayed,
    )


def _account_action_read(result) -> AdminAccountActionRead:
    return AdminAccountActionRead(
        user=AdminUserRead.model_validate(result.user),
        changed=result.changed,
        sessions_revoked=result.sessions_revoked,
    )


def _raise_account_action_error(exc: Exception) -> None:
    if isinstance(exc, AdminAccountTargetNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        ) from exc

    if isinstance(
        exc,
        (
            AdminAccountActorForbiddenError,
            AdminAccountTargetForbiddenError,
        ),
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account cannot be managed by this action.",
        ) from exc

    raise exc


@router.get("/users", response_model=AdminUserPageRead)
async def list_admin_users(
    operator: PlatformSupportUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    query: Annotated[
        str | None,
        Query(
            alias="q",
            min_length=1,
            max_length=200,
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserPageRead:
    result = await AdminService(session).list_users(
        query=query,
        limit=limit,
        offset=offset,
        include_platform_roles=operator.role == UserRole.SUPER_ADMIN,
    )

    return AdminUserPageRead(
        items=[AdminUserRead.model_validate(user) for user in result.items],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


@router.get("/businesses", response_model=AdminBusinessPageRead)
async def list_admin_businesses(
    _: PlatformSupportUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    query: Annotated[
        str | None,
        Query(
            alias="q",
            min_length=1,
            max_length=200,
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminBusinessPageRead:
    result = await AdminService(session).list_businesses(
        query=query,
        limit=limit,
        offset=offset,
    )

    return AdminBusinessPageRead(
        items=[_business_read(item) for item in result.items],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


@router.get(
    "/businesses/{business_id}/subscription",
    response_model=AdminSubscriptionRead,
)
async def read_admin_business_subscription(
    business_id: UUID,
    _: PlatformSupportUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminSubscriptionRead:
    try:
        result = await AdminSubscriptionService(session).get_latest(
            business_id=business_id,
        )
    except (
        AdminBusinessNotFoundError,
        AdminBusinessSubscriptionNotFoundError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business subscription not found.",
        ) from exc

    return _subscription_read(result)


@router.post(
    "/businesses/{business_id}/ai-credits/adjustments",
    response_model=AdminAICreditAdjustmentRead,
)
async def adjust_admin_ai_credits(
    business_id: UUID,
    payload: AdminAICreditAdjustmentRequest,
    request: Request,
    operator: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
        ),
    ],
) -> AdminAICreditAdjustmentRead:
    try:
        result = await AdminAICreditAdjustmentService(session).adjust(
            actor=operator,
            business_id=business_id,
            delta=payload.delta,
            reason_code=payload.reason_code,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except AdminAICreditBusinessNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found.",
        ) from exc
    except AdminAICreditActorForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This credit operation is not permitted.",
        ) from exc
    except AICreditIdempotencyKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid Idempotency-Key.",
        ) from exc
    except (
        AdminAICreditAdjustmentConflictError,
        AdminAICreditEntitlementUnavailableError,
        AdminAICreditIdempotencyConflictError,
        AdminAICreditSubscriptionUnavailableError,
        AdminAICreditUnlimitedEntitlementError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The AI credit adjustment conflicts with current credit state.",
        ) from exc

    return _ai_credit_adjustment_read(result)


@router.post(
    "/users/{user_id}/suspend",
    response_model=AdminAccountActionRead,
)
async def suspend_admin_user(
    user_id: UUID,
    payload: AdminAccountActionRequest,
    request: Request,
    operator: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminAccountActionRead:
    try:
        result = await AdminAccountService(session).suspend_user(
            actor=operator,
            target_user_id=user_id,
            reason_code=payload.reason_code,
            request_id=request.state.request_id,
        )
    except (
        AdminAccountActorForbiddenError,
        AdminAccountTargetForbiddenError,
        AdminAccountTargetNotFoundError,
    ) as exc:
        _raise_account_action_error(exc)
        raise AssertionError("unreachable") from exc

    return _account_action_read(result)


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminAccountActionRead,
)
async def reactivate_admin_user(
    user_id: UUID,
    payload: AdminAccountActionRequest,
    request: Request,
    operator: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminAccountActionRead:
    try:
        result = await AdminAccountService(session).reactivate_user(
            actor=operator,
            target_user_id=user_id,
            reason_code=payload.reason_code,
            request_id=request.state.request_id,
        )
    except (
        AdminAccountActorForbiddenError,
        AdminAccountTargetForbiddenError,
        AdminAccountTargetNotFoundError,
    ) as exc:
        _raise_account_action_error(exc)
        raise AssertionError("unreachable") from exc

    return _account_action_read(result)
