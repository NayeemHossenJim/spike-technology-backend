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
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    enforce_ai_rate_limit_dependency,
    get_current_user,
    require_tenant_roles,
)
from app.api.v1.ai_errors import ai_execution_http_error
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.business import TenantRole
from app.models.user import User
from app.schemas.ai import (
    AIConversationRead,
    AICreditUsageSummaryRead,
    AIMessageRead,
    AIMessageSubmissionRead,
)
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardInsightContractRead,
    DashboardPageRead,
    DashboardRead,
    DashboardSnapshotAIAnalysisRead,
    DashboardSnapshotCreate,
    DashboardSnapshotDetailRead,
    DashboardSnapshotPageRead,
    DashboardSnapshotRead,
    DashboardSnapshotSourceRead,
    DashboardSourceSchemaRead,
    DashboardUpdate,
)
from app.services.ai_conversations import (
    AIConversationIntegrityError,
    AIConversationService,
    AIConversationSourceNotFoundError,
    AIConversationSourceNotReadyError,
    AIMessageContentError,
    AIMessageIdempotencyConflictError,
)
from app.services.ai_execution import (
    AI_DASHBOARD_ANALYSIS_REQUEST,
    AIExecutionFailedError,
    AIExecutionIntegrityError,
    AIExecutionResult,
    AIExecutionService,
)
from app.services.dashboard_insights import dashboard_insight_contract_payload
from app.services.dashboard_pdf import (
    DashboardPDFDashboardNotFoundError,
    DashboardPDFRenderer,
    DashboardPDFRendererError,
    DashboardPDFRendererUnavailableError,
    DashboardPDFService,
    DashboardPDFSnapshotNotFoundError,
    get_dashboard_pdf_renderer,
)
from app.services.dashboard_snapshots import (
    DashboardSnapshotArtifactError,
    DashboardSnapshotConflictError,
    DashboardSnapshotMaterialization,
    DashboardSnapshotNotFoundError,
    DashboardSnapshotService,
    DashboardSnapshotSourceMismatchError,
    DashboardSnapshotSourceNotFoundError,
    DashboardSnapshotSourceNotReadyError,
    DashboardSnapshotStorageError,
    DashboardSourceSchemaService,
)
from app.services.dashboards import (
    DashboardConflictError,
    DashboardInsightConfigurationError,
    DashboardNotFoundError,
    DashboardService,
)
from app.services.gemini_gateway import GeminiGateway, get_gemini_gateway
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


async def _snapshot_detail(
    result: DashboardSnapshotMaterialization,
    *,
    session: AsyncSession,
    tenant: TenantContext,
) -> DashboardSnapshotDetailRead:
    state = await AIConversationService(session).dashboard_analysis_state(
        tenant.scope,
        snapshot_id=result.snapshot.id,
    )
    analysis: DashboardSnapshotAIAnalysisRead | None = None
    if state is not None and state.user_message is not None:
        user_message = state.user_message
        assistant_message = state.assistant_message
        analysis = DashboardSnapshotAIAnalysisRead(
            snapshot_id=result.snapshot.id,
            conversation_id=state.conversation.id,
            purpose=state.conversation.purpose,
            status=user_message.status,
            user_message_id=user_message.id,
            assistant_message_id=(assistant_message.id if assistant_message is not None else None),
            content=(assistant_message.content if assistant_message is not None else None),
            provider_response_id=(
                assistant_message.provider_response_id if assistant_message is not None else None
            ),
            provider_model=(
                assistant_message.provider_model if assistant_message is not None else None
            ),
            provider_finish_reason=(
                assistant_message.provider_finish_reason if assistant_message is not None else None
            ),
            completed_at=(
                assistant_message.completed_at
                if assistant_message is not None
                else user_message.completed_at
            ),
            error_code=user_message.error_code,
        )
    return DashboardSnapshotDetailRead(
        **DashboardSnapshotRead.model_validate(result.snapshot).model_dump(exclude_unset=True),
        sources=[DashboardSnapshotSourceRead.model_validate(source) for source in result.sources],
        ai_analysis=analysis,
    )


def _ai_submission_read(result: AIExecutionResult) -> AIMessageSubmissionRead:
    return AIMessageSubmissionRead(
        conversation=AIConversationRead.model_validate(result.conversation),
        user_message=AIMessageRead.model_validate(result.user_message),
        assistant_message=(
            AIMessageRead.model_validate(result.assistant_message)
            if result.assistant_message is not None
            else None
        ),
        usage=(
            AICreditUsageSummaryRead.model_validate(
                result.usage,
                from_attributes=True,
            )
            if result.usage is not None
            else None
        ),
        replayed=result.replayed,
    )


@router.get(
    "/contracts",
    response_model=DashboardInsightContractRead,
)
async def read_dashboard_insight_contract(
    _: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
) -> DashboardInsightContractRead:
    return DashboardInsightContractRead.model_validate(dashboard_insight_contract_payload())


@router.get(
    "/sources/{processing_job_id}/schema",
    response_model=DashboardSourceSchemaRead,
    responses={
        404: {"description": "Completed processing source not found."},
        409: {"description": "Source is not ready, valid, or stable."},
        503: {"description": "Processed artifact storage unavailable."},
    },
)
async def read_dashboard_source_schema(
    processing_job_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[
        S3UploadGateway,
        Depends(get_s3_upload_gateway),
    ],
) -> DashboardSourceSchemaRead:
    try:
        source = await DashboardSourceSchemaService(
            session,
            storage=storage,
        ).get(
            tenant.scope,
            processing_job_id,
        )
    except DashboardSnapshotSourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard source not found.",
        ) from exc
    except DashboardSnapshotSourceNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dashboard source processing is not complete.",
        ) from exc
    except DashboardSnapshotArtifactError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Processed report schema is invalid or inconsistent.",
        ) from exc
    except DashboardSnapshotConflictError as exc:
        raise _conflict() from exc
    except DashboardSnapshotStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Processed report schema is temporarily unavailable.",
        ) from exc

    return DashboardSourceSchemaRead.model_validate(source)


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
    except DashboardInsightConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Dashboard insight mappings do not match the dashboard type.",
        ) from exc
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
    response_model_exclude_unset=True,
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
    except DashboardSnapshotSourceMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Snapshot sources must exactly match the configured report mappings.",
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

    return await _snapshot_detail(
        result,
        session=session,
        tenant=tenant,
    )


@router.post(
    "/{dashboard_id}/snapshots/{snapshot_id}/analysis",
    response_model=AIMessageSubmissionRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "A completed idempotent replay."},
        202: {"description": "The idempotent request is already processing."},
        402: {"description": "An active subscription is required."},
        403: {"description": "AI entitlement is disabled."},
        404: {"description": "Dashboard snapshot not found."},
        409: {"description": "Snapshot is untyped or idempotency conflicts."},
        429: {"description": "AI rate or credit limit reached."},
        502: {"description": "The provider returned an unusable response."},
        503: {"description": "AI service is temporarily unavailable."},
    },
)
async def generate_dashboard_snapshot_analysis(
    dashboard_id: UUID,
    snapshot_id: UUID,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=255,
            pattern=r"^[!-~]+$",
        ),
    ],
    current_user: Annotated[User, Depends(get_current_user)],
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    gateway: Annotated[GeminiGateway, Depends(get_gemini_gateway)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(enforce_ai_rate_limit_dependency)],
) -> AIMessageSubmissionRead:
    try:
        materialization = await DashboardSnapshotService(session).get(
            tenant.scope,
            dashboard_id,
            snapshot_id,
        )
        conversation = await AIConversationService(session).get_or_create_dashboard_analysis(
            tenant.scope,
            user=current_user,
            snapshot_id=materialization.snapshot.id,
            title=f"Dashboard snapshot v{materialization.snapshot.version} analysis",
        )
        result = await AIExecutionService(
            session,
            gateway=gateway,
            settings=settings,
        ).execute(
            tenant.scope,
            user=current_user,
            conversation_id=conversation.id,
            content=AI_DASHBOARD_ANALYSIS_REQUEST,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except (DashboardSnapshotNotFoundError, AIConversationSourceNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard snapshot not found.",
        ) from exc
    except DashboardSnapshotArtifactError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dashboard snapshot is invalid or inconsistent.",
        ) from exc
    except AIConversationSourceNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dashboard analysis requires a typed dashboard snapshot.",
        ) from exc
    except AIMessageIdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different AI message.",
        ) from exc
    except AIMessageContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="AI message content is invalid.",
        ) from exc
    except AIExecutionFailedError as exc:
        raise ai_execution_http_error(exc) from exc
    except (AIConversationIntegrityError, AIExecutionIntegrityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is temporarily unavailable.",
        ) from exc

    if result.in_progress:
        response.status_code = status.HTTP_202_ACCEPTED
    elif result.replayed:
        response.status_code = status.HTTP_200_OK

    return _ai_submission_read(result)


@router.get(
    "/{dashboard_id}/snapshots/latest",
    response_model=DashboardSnapshotDetailRead,
    response_model_exclude_unset=True,
    responses={
        404: {"description": "Dashboard snapshot not found."},
        409: {"description": "Stored snapshot integrity check failed."},
    },
)
async def read_latest_dashboard_snapshot(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardSnapshotDetailRead:
    try:
        result = await DashboardSnapshotService(session).latest(
            tenant.scope,
            dashboard_id,
        )
    except DashboardSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard snapshot not found.",
        ) from exc
    except DashboardSnapshotArtifactError as exc:
        raise _conflict() from exc

    return await _snapshot_detail(
        result,
        session=session,
        tenant=tenant,
    )


@router.get(
    "/{dashboard_id}/snapshots/{snapshot_id}",
    response_model=DashboardSnapshotDetailRead,
    response_model_exclude_unset=True,
    responses={
        404: {"description": "Dashboard snapshot not found."},
        409: {"description": "Stored snapshot integrity check failed."},
    },
)
async def read_dashboard_snapshot(
    dashboard_id: UUID,
    snapshot_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardSnapshotDetailRead:
    try:
        result = await DashboardSnapshotService(session).get(
            tenant.scope,
            dashboard_id,
            snapshot_id,
        )
    except DashboardSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard snapshot not found.",
        ) from exc
    except DashboardSnapshotArtifactError as exc:
        raise _conflict() from exc

    return await _snapshot_detail(
        result,
        session=session,
        tenant=tenant,
    )


@router.get(
    "/{dashboard_id}/snapshots",
    response_model=DashboardSnapshotPageRead,
    response_model_exclude_unset=True,
    responses={
        404: {"description": "Dashboard not found."},
        409: {"description": "Stored snapshot integrity check failed."},
    },
)
async def list_dashboard_snapshots(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DashboardSnapshotPageRead:
    try:
        page = await DashboardSnapshotService(session).list(
            tenant.scope,
            dashboard_id,
            limit=limit,
            offset=offset,
        )
    except DashboardSnapshotNotFoundError as exc:
        raise _not_found() from exc
    except DashboardSnapshotArtifactError as exc:
        raise _conflict() from exc

    return DashboardSnapshotPageRead(
        items=[DashboardSnapshotRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{dashboard_id}/export/pdf",
    response_class=Response,
    responses={
        200: {
            "description": "Latest immutable dashboard snapshot as PDF.",
            "content": {
                "application/pdf": {},
            },
        },
        404: {
            "description": "Dashboard or dashboard snapshot not found.",
        },
        503: {
            "description": "PDF renderer unavailable.",
        },
    },
)
async def export_dashboard_pdf(
    dashboard_id: UUID,
    tenant: Annotated[
        TenantContext,
        Depends(require_tenant_roles(TenantRole.OWNER)),
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    renderer: Annotated[
        DashboardPDFRenderer,
        Depends(get_dashboard_pdf_renderer),
    ],
) -> Response:
    try:
        exported = await DashboardPDFService(
            session,
            renderer=renderer,
        ).export_latest(
            tenant.scope,
            dashboard_id,
            business_name=tenant.business.name,
            business_industry=tenant.business.industry,
        )
    except DashboardPDFDashboardNotFoundError as exc:
        raise _not_found() from exc
    except DashboardPDFSnapshotNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard snapshot not found.",
        ) from exc
    except (
        DashboardPDFRendererUnavailableError,
        DashboardPDFRendererError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Dashboard PDF export is temporarily unavailable.",
        ) from exc

    return Response(
        content=exported.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (f'attachment; filename="{exported.filename}"'),
            "Cache-Control": "private, no-store",
            "X-Dashboard-Snapshot-Version": str(exported.snapshot_version),
            "X-Dashboard-Snapshot-Hash": (exported.snapshot_content_hash),
        },
    )
