from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_tenant_roles
from app.db.session import get_session
from app.models.business import TenantRole
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardPageRead,
    DashboardRead,
    DashboardUpdate,
)
from app.services.dashboards import (
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardService,
)
from app.services.subscriptions import (
    EntitlementDeniedError,
    EntitlementLimitReachedError,
    SubscriptionInactiveError,
    SubscriptionRequiredError,
)
from app.services.tenant import TenantContext

router = APIRouter(prefix="/dashboards", tags=["Dashboards"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Dashboard not found.",
    )


def _conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The dashboard operation could not be completed safely.",
    )


@router.post(
    "",
    response_model=DashboardRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        402: {"description": "An active subscription is required."},
        403: {"description": "Dashboard entitlement is disabled."},
        409: {"description": "Dashboard limit or persistence conflict."},
    },
)
async def create_dashboard(
    payload: DashboardCreate,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardRead:
    try:
        dashboard = await DashboardService(session).create(
            tenant.scope,
            created_by_user_id=tenant.role_assignment.user_id,
            payload=payload,
        )
    except SubscriptionRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="An active subscription is required to create dashboards.",
        ) from exc
    except SubscriptionInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="The current subscription cannot create dashboards.",
        ) from exc
    except EntitlementDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard access is not enabled for the current subscription.",
        ) from exc
    except EntitlementLimitReachedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The dashboard limit has been reached for the current subscription.",
        ) from exc
    except DashboardConflictError as exc:
        raise _conflict() from exc

    return DashboardRead.model_validate(dashboard)


@router.get(
    "",
    response_model=DashboardPageRead,
)
async def list_dashboards(
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DashboardPageRead:
    page = await DashboardService(session).list(
        tenant.scope,
        limit=limit,
        offset=offset,
    )

    return DashboardPageRead(
        items=[DashboardRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{dashboard_id}",
    response_model=DashboardRead,
)
async def read_dashboard(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardRead:
    try:
        dashboard = await DashboardService(session).get(
            tenant.scope,
            dashboard_id,
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc

    return DashboardRead.model_validate(dashboard)


@router.patch(
    "/{dashboard_id}",
    response_model=DashboardRead,
)
async def update_dashboard(
    dashboard_id: UUID,
    payload: DashboardUpdate,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardRead:
    try:
        dashboard = await DashboardService(session).update(
            tenant.scope,
            dashboard_id,
            payload=payload,
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardConflictError as exc:
        raise _conflict() from exc

    return DashboardRead.model_validate(dashboard)


@router.delete(
    "/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_dashboard(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        await DashboardService(session).delete(
            tenant.scope,
            dashboard_id,
        )
    except DashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardConflictError as exc:
        raise _conflict() from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
