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
    DashboardSnapshotCreate,
    DashboardSnapshotDetailRead,
    DashboardSnapshotPageRead,
    DashboardSnapshotRead,
    DashboardSnapshotSourceRead,
    DashboardUpdate,
)
from app.services.dashboard_snapshots import (
    DashboardSnapshotArtifactError,
    DashboardSnapshotConflictError,
    DashboardSnapshotMaterialization,
    DashboardSnapshotNotFoundError,
    DashboardSnapshotService,
    DashboardSnapshotSourceNotFoundError,
    DashboardSnapshotSourceNotReadyError,
    DashboardSnapshotStorageError,
)
from app.services.dashboards import (
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardService,
)
from app.services.s3_storage import (
    S3UploadGateway,
    get_s3_upload_gateway,
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


def _snapshot_detail(
    result: DashboardSnapshotMaterialization,
) -> DashboardSnapshotDetailRead:
    return DashboardSnapshotDetailRead(
        **DashboardSnapshotRead.model_validate(result.snapshot).model_dump(),
        sources=[DashboardSnapshotSourceRead.model_validate(source) for source in result.sources],
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


@router.post(
    "/{dashboard_id}/snapshots",
    response_model=DashboardSnapshotDetailRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Identical snapshot replay."},
        404: {"description": "Dashboard or source not found."},
        409: {"description": "Source is not ready or changed."},
        503: {"description": "Processed artifact storage unavailable."},
    },
)
async def materialize_dashboard_snapshot(
    dashboard_id: UUID,
    payload: DashboardSnapshotCreate,
    response: Response,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[
        S3UploadGateway,
        Depends(get_s3_upload_gateway),
    ],
) -> DashboardSnapshotDetailRead:
    try:
        result = await DashboardSnapshotService(
            session,
            storage=storage,
        ).materialize(
            tenant.scope,
            dashboard_id,
            created_by_user_id=tenant.role_assignment.user_id,
            source_processing_job_ids=tuple(payload.source_processing_job_ids),
        )
    except DashboardSnapshotNotFoundError as exc:
        raise _not_found() from exc
    except DashboardSnapshotSourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard source not found.",
        ) from exc
    except DashboardSnapshotSourceNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Dashboard sources must be completed report-processing jobs."),
        ) from exc
    except DashboardSnapshotArtifactError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Processed report data is invalid or inconsistent.",
        ) from exc
    except DashboardSnapshotStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Processed report data is temporarily unavailable.",
        ) from exc
    except DashboardSnapshotConflictError as exc:
        raise _conflict() from exc

    if result.replayed:
        response.status_code = status.HTTP_200_OK

    return _snapshot_detail(result)


@router.get(
    "/{dashboard_id}/snapshots/latest",
    response_model=DashboardSnapshotDetailRead,
)
async def read_latest_dashboard_snapshot(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[
        S3UploadGateway,
        Depends(get_s3_upload_gateway),
    ],
) -> DashboardSnapshotDetailRead:
    try:
        result = await DashboardSnapshotService(
            session,
            storage=storage,
        ).latest(
            tenant.scope,
            dashboard_id,
        )
    except DashboardSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard snapshot not found.",
        ) from exc

    return _snapshot_detail(result)


@router.get(
    "/{dashboard_id}/snapshots",
    response_model=DashboardSnapshotPageRead,
)
async def list_dashboard_snapshots(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[
        S3UploadGateway,
        Depends(get_s3_upload_gateway),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DashboardSnapshotPageRead:
    try:
        page = await DashboardSnapshotService(
            session,
            storage=storage,
        ).list(
            tenant.scope,
            dashboard_id,
            limit=limit,
            offset=offset,
        )
    except DashboardSnapshotNotFoundError as exc:
        raise _not_found() from exc

    return DashboardSnapshotPageRead(
        items=[DashboardSnapshotRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
