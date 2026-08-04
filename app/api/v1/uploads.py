from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_tenant
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.schemas.upload import (
    PresignedPostRead,
    PresignedReportUploadRead,
    ReportUploadBatchCreate,
    ReportUploadBatchCreatedRead,
    ReportUploadBatchRead,
    ReportUploadRead,
)
from app.services.processing import ReportProcessingConflictError
from app.services.processing_dispatch import (
    ReportProcessingDispatcher,
    ReportProcessingDispatchError,
    get_report_processing_dispatcher,
)
from app.services.s3_storage import (
    S3StorageError,
    S3UploadGateway,
    get_s3_upload_gateway,
)
from app.services.tenant import TenantContext
from app.services.uploads import (
    CreatedReportUploadBatch,
    ReportUploadBatchResult,
    ReportUploadConflictError,
    ReportUploadRequestRejectedError,
    ReportUploadsDisabledError,
    ReportUploadService,
)

router = APIRouter(prefix="/report-uploads", tags=["Report Uploads"])


def _service(
    *,
    session: AsyncSession,
    settings: Settings,
    storage: S3UploadGateway,
) -> ReportUploadService:
    return ReportUploadService(
        session=session,
        settings=settings,
        storage=storage,
    )


def _batch_read(result: ReportUploadBatchResult) -> ReportUploadBatchRead:
    return ReportUploadBatchRead(
        id=result.batch.id,
        status=result.batch.status,
        expires_at=result.batch.expires_at,
        completed_at=result.batch.completed_at,
        files=[ReportUploadRead.model_validate(upload) for upload in result.uploads],
    )


def _created_batch_read(result: CreatedReportUploadBatch) -> ReportUploadBatchCreatedRead:
    return ReportUploadBatchCreatedRead(
        id=result.batch.id,
        status=result.batch.status,
        expires_at=result.batch.expires_at,
        completed_at=result.batch.completed_at,
        files=[
            PresignedReportUploadRead(
                **ReportUploadRead.model_validate(item.record).model_dump(),
                upload=PresignedPostRead(
                    url=item.presigned_post.url,
                    fields=item.presigned_post.fields,
                ),
            )
            for item in result.uploads
        ],
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Report upload batch not found.",
    )


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Report upload storage is temporarily unavailable.",
    )


@router.post(
    "/batches",
    response_model=ReportUploadBatchCreatedRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_report_upload_batch(
    payload: ReportUploadBatchCreate,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[S3UploadGateway, Depends(get_s3_upload_gateway)],
) -> ReportUploadBatchCreatedRead:
    try:
        created = await _service(
            session=session,
            settings=settings,
            storage=storage,
        ).create_batch(
            scope=tenant.scope,
            uploader_user_id=tenant.role_assignment.user_id,
            payload=payload,
        )
    except ReportUploadRequestRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
        ) from exc
    except ReportUploadsDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report uploads are not configured.",
        ) from exc
    except S3StorageError as exc:
        raise _storage_unavailable() from exc
    except ReportUploadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The report upload batch could not be created safely.",
        ) from exc
    return _created_batch_read(created)


@router.get("/batches/{batch_id}", response_model=ReportUploadBatchRead)
async def read_report_upload_batch(
    batch_id: UUID,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[S3UploadGateway, Depends(get_s3_upload_gateway)],
) -> ReportUploadBatchRead:
    result = await _service(
        session=session,
        settings=settings,
        storage=storage,
    ).get_batch(
        scope=tenant.scope,
        batch_id=batch_id,
    )
    if result is None:
        raise _not_found()
    return _batch_read(result)


@router.post("/batches/{batch_id}/complete", response_model=ReportUploadBatchRead)
async def complete_report_upload_batch(
    batch_id: UUID,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[S3UploadGateway, Depends(get_s3_upload_gateway)],
    dispatcher: Annotated[
        ReportProcessingDispatcher,
        Depends(get_report_processing_dispatcher),
    ],
) -> ReportUploadBatchRead:
    try:
        result = await _service(
            session=session,
            settings=settings,
            storage=storage,
        ).complete_batch(
            scope=tenant.scope,
            batch_id=batch_id,
            dispatcher=dispatcher,
        )
    except ReportUploadsDisabledError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report uploads are not configured.",
        ) from exc
    except S3StorageError as exc:
        await session.rollback()
        raise _storage_unavailable() from exc
    except ReportUploadConflictError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The report upload batch is inconsistent.",
        ) from exc
    except ReportProcessingConflictError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The report processing state is inconsistent.",
        ) from exc
    except ReportProcessingDispatchError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report processing is temporarily unavailable.",
        ) from exc
    if result is None:
        raise _not_found()
    return _batch_read(result)
