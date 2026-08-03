from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.base import utc_now
from app.models.processing import ReportProcessingJob, ReportProcessingStatus
from app.models.upload import (
    ReportUpload,
    ReportUploadBatch,
    ReportUploadBatchStatus,
    ReportUploadStatus,
)
from app.services.processing import ReportProcessingOutput, ReportProcessingService
from app.services.tenant import TenantScope
from tests.conftest import InMemoryEmailSender, InMemoryReportProcessingDispatcher
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import (
    FakeS3UploadGateway,
    configure_upload_app,
    onboard_owner,
)


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


@pytest.mark.integration
async def test_partial_batch_commits_one_job_before_idempotent_dispatch(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    processing_dispatcher: InMemoryReportProcessingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeS3UploadGateway()
    configure_upload_app(app, gateway)
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="processing-partial@example.com",
        business_name="Processing Partial Tenant",
    )
    valid_content = b"month,revenue\nJan,1200\n"
    rejected_content = b"MZ" + b"\x00" * 62
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "valid.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(valid_content),
                },
                {
                    "filename": "renamed.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(rejected_content),
                },
            ]
        },
    )
    assert created.status_code == 201
    payload = created.json()
    gateway.add_uploaded_object(
        file_payload=payload["files"][0],
        content=valid_content,
    )
    gateway.add_uploaded_object(
        file_payload=payload["files"][1],
        content=rejected_content,
    )

    async def assert_job_is_committed(job_id: UUID) -> None:
        async with session_factory() as verification_session:
            job = await verification_session.get(ReportProcessingJob, job_id)
            batch = await verification_session.get(
                ReportUploadBatch,
                UUID(payload["id"]),
            )
            assert job is not None
            assert job.report_upload_id == UUID(payload["files"][0]["id"])
            assert job.source_storage_version_id == (f"version-{payload['files'][0]['id']}")
            assert batch is not None
            assert ReportUploadBatchStatus(batch.status) is ReportUploadBatchStatus.PARTIAL

    processing_dispatcher.before_dispatch = assert_job_is_committed
    completed = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert completed_payload["status"] == "partial"
    assert [item["status"] for item in completed_payload["files"]] == [
        "uploaded",
        "rejected",
    ]
    assert len(processing_dispatcher.dispatched_job_ids) == 1

    business_id = UUID(onboarding["business"]["id"])
    async with session_factory() as session:
        jobs = (
            (
                await session.execute(
                    select(ReportProcessingJob).where(
                        ReportProcessingJob.business_id == business_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].id == processing_dispatcher.dispatched_job_ids[0]
        assert jobs[0].dispatch_attempt_count == 1
        assert jobs[0].last_dispatched_at is not None

    repeated = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert repeated.status_code == 200
    assert repeated.json() == completed_payload
    assert processing_dispatcher.attempted_job_ids == [jobs[0].id]


@pytest.mark.integration
async def test_broker_failure_preserves_job_and_retry_redrives_same_uuid(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    processing_dispatcher: InMemoryReportProcessingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeS3UploadGateway()
    configure_upload_app(app, gateway)
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="processing-redrive@example.com",
        business_name="Processing Redrive Tenant",
    )
    content = b"month,revenue\nFeb,1300\n"
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "redrive.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(content),
                }
            ]
        },
    )
    payload = created.json()
    gateway.add_uploaded_object(file_payload=payload["files"][0], content=content)
    processing_dispatcher.failures_remaining = 1

    unavailable = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Report processing is temporarily unavailable."}

    async with session_factory() as session:
        batch = await session.get(ReportUploadBatch, UUID(payload["id"]))
        job = (await session.execute(select(ReportProcessingJob))).scalar_one()
        assert batch is not None
        assert ReportUploadBatchStatus(batch.status) is ReportUploadBatchStatus.COMPLETE
        assert ReportProcessingStatus(job.status) is ReportProcessingStatus.QUEUED
        assert job.dispatch_attempt_count == 0
        assert job.last_dispatched_at is None
        durable_job_id = job.id

    recovered = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "complete"
    assert processing_dispatcher.attempted_job_ids == [durable_job_id, durable_job_id]
    assert processing_dispatcher.dispatched_job_ids == [durable_job_id]

    async with session_factory() as session:
        job = await session.get(ReportProcessingJob, durable_job_id)
        assert job is not None
        assert job.dispatch_attempt_count == 1
        assert job.last_dispatched_at is not None


@pytest.mark.integration
async def test_job_claim_retry_and_completion_are_lease_guarded(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, onboarding = await onboard_owner(
        client,
        email_sender,
        email="processing-lifecycle@example.com",
        business_name="Processing Lifecycle Tenant",
    )
    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    upload = await create_verified_upload(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="lifecycle.csv",
    )
    started_at = utc_now()

    async with session_factory() as session:
        service = ReportProcessingService(session=session)
        jobs = await service.ensure_jobs_for_uploads(
            scope=TenantScope(business_id=business_id),
            uploads=(upload,),
        )
        await session.commit()
        job_id = jobs[0].id

        first_claim = await service.claim_job(job_id, now=started_at)
        assert first_claim is not None
        assert first_claim.attempt_count == 1
        assert first_claim.storage_version_id == upload.storage_version_id

        assert (
            await service.claim_job(
                job_id,
                now=started_at + timedelta(seconds=1),
            )
            is None
        )
        assert not await service.retry_job(
            job_id,
            lease_token=uuid4(),
            error_code="temporary_database_failure",
            retry_delay=timedelta(seconds=30),
            now=started_at + timedelta(seconds=1),
        )
        assert await service.retry_job(
            job_id,
            lease_token=first_claim.lease_token,
            error_code="temporary_database_failure",
            retry_delay=timedelta(seconds=30),
            now=started_at + timedelta(seconds=1),
        )
        assert (
            await service.claim_job(
                job_id,
                now=started_at + timedelta(seconds=30),
            )
            is None
        )

        second_claim = await service.claim_job(
            job_id,
            now=started_at + timedelta(seconds=32),
        )
        assert second_claim is not None
        assert second_claim.attempt_count == 2

        output = ReportProcessingOutput(
            storage_bucket="spike-private-test-results",
            normalized_storage_key=f"processed/{business_id}/{job_id}/normalized.parquet",
            normalized_storage_version_id=f"normalized-version-{uuid4()}",
            normalized_etag=f"normalized-etag-{uuid4()}",
            normalized_size_bytes=128,
            profile_storage_key=f"processed/{business_id}/{job_id}/profile.json",
            profile_storage_version_id=f"profile-version-{uuid4()}",
            profile_etag=f"profile-etag-{uuid4()}",
            record_count=2,
            sheet_count=1,
        )
        assert await service.complete_job(
            job_id,
            lease_token=second_claim.lease_token,
            output=output,
            now=started_at + timedelta(seconds=33),
        )
        assert not await service.complete_job(
            job_id,
            lease_token=second_claim.lease_token,
            output=output,
            now=started_at + timedelta(seconds=34),
        )

        completed = await session.get(ReportProcessingJob, job_id)
        assert completed is not None
        assert ReportProcessingStatus(completed.status) is ReportProcessingStatus.COMPLETED
        assert completed.attempt_count == 2
        assert completed.record_count == 2
        assert completed.sheet_count == 1
        assert completed.lease_token is None
        assert completed.error_code is None
