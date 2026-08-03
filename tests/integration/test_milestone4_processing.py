from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.base import utc_now
from app.models.processing import ReportProcessingJob, ReportProcessingStatus
from app.models.upload import (
    ReportUpload,
    ReportUploadBatch,
    ReportUploadBatchStatus,
    ReportUploadStatus,
)
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone3_uploads import onboard_owner


async def create_verified_upload(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    business_id: UUID,
    user_id: UUID,
    filename: str,
) -> ReportUpload:
    now = utc_now()
    batch = ReportUploadBatch(
        business_id=business_id,
        created_by_user_id=user_id,
        status=ReportUploadBatchStatus.COMPLETE,
        file_count=1,
        expires_at=now + timedelta(minutes=10),
        completed_at=now,
    )
    upload = ReportUpload(
        business_id=business_id,
        batch_id=batch.id,
        batch_position=0,
        uploaded_by_user_id=user_id,
        original_filename=filename,
        file_extension="csv",
        content_type="text/csv",
        expected_size_bytes=16,
        storage_bucket="spike-private-test-uploads",
        storage_key=f"report-uploads/{business_id}/{batch.id}/{uuid4()}.csv",
        status=ReportUploadStatus.UPLOADED,
        expires_at=batch.expires_at,
        actual_size_bytes=16,
        storage_version_id=f"version-{uuid4()}",
        etag=f"etag-{uuid4()}",
        uploaded_at=now,
    )
    async with session_factory() as session:
        session.add(batch)
        await session.flush()
        session.add(upload)
        await session.commit()
    return upload


def queued_job_values(*, business_id: UUID, upload: ReportUpload) -> dict[str, object]:
    now = utc_now()
    return {
        "id": uuid4(),
        "business_id": business_id,
        "report_upload_id": upload.id,
        "source_storage_version_id": upload.storage_version_id,
        "source_etag": upload.etag,
        "status": ReportProcessingStatus.QUEUED.value,
        "attempt_count": 0,
        "max_attempts": 3,
        "dispatch_attempt_count": 0,
        "available_at": now,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.integration
async def test_processing_jobs_are_one_per_upload_tenant_bound_and_state_checked(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    test_engine: AsyncEngine,
) -> None:
    _, first_onboarding = await onboard_owner(
        client,
        email_sender,
        email="processing-tenant-a@example.com",
        business_name="Processing Tenant A",
    )
    _, second_onboarding = await onboard_owner(
        client,
        email_sender,
        email="processing-tenant-b@example.com",
        business_name="Processing Tenant B",
    )
    first_business_id = UUID(first_onboarding["business"]["id"])
    first_user_id = UUID(first_onboarding["role_assignment"]["user_id"])
    second_business_id = UUID(second_onboarding["business"]["id"])
    second_user_id = UUID(second_onboarding["role_assignment"]["user_id"])

    first_upload = await create_verified_upload(
        session_factory,
        business_id=first_business_id,
        user_id=first_user_id,
        filename="first.csv",
    )
    second_upload = await create_verified_upload(
        session_factory,
        business_id=second_business_id,
        user_id=second_user_id,
        filename="second.csv",
    )

    async with session_factory() as session:
        job = ReportProcessingJob(
            business_id=first_business_id,
            report_upload_id=first_upload.id,
            source_storage_version_id=first_upload.storage_version_id or "",
            source_etag=first_upload.etag or "",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        assert job.status == ReportProcessingStatus.QUEUED

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(ReportProcessingJob).values(
                    **queued_job_values(
                        business_id=second_business_id,
                        upload=first_upload,
                    )
                )
            )

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(ReportProcessingJob).values(
                    **queued_job_values(
                        business_id=first_business_id,
                        upload=first_upload,
                    )
                )
            )

    invalid_completed = queued_job_values(
        business_id=second_business_id,
        upload=second_upload,
    )
    started_at = utc_now()
    invalid_completed.update(
        status=ReportProcessingStatus.COMPLETED.value,
        attempt_count=1,
        available_at=None,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
    )
    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(insert(ReportProcessingJob).values(**invalid_completed))
