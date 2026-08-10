from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
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
    AdminBusinessPageRead,
    AdminBusinessRead,
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
