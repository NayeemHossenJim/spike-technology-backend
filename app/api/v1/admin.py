from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db.session import get_session
from app.models.user import User, UserRole
from app.schemas.admin import (
    AdminBusinessPageRead,
    AdminBusinessRead,
    AdminUserPageRead,
    AdminUserRead,
)
from app.services.admin import AdminService

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


def _business_read(item) -> AdminBusinessRead:
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
