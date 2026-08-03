from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.base import utc_now
from app.models.processing import ReportProcessingJob, ReportProcessingStatus
from app.models.upload import ReportUpload, ReportUploadStatus
from app.services.processing_dispatch import (
    ReportProcessingDispatcher,
    ReportProcessingDispatchError,
)
from app.services.tenant import TenantScope

REPORT_PROCESSING_LEASE_DURATION = timedelta(minutes=15)
REPORT_PROCESSING_REDISPATCH_INTERVAL = timedelta(minutes=5)
REPORT_PROCESSING_ATTEMPT_LIMIT_ERROR = "attempt_limit_exceeded"
REPORT_PROCESSING_SOURCE_INVALID_ERROR = "source_state_invalid"
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ReportProcessingConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReportProcessingClaim:
    job_id: UUID
    business_id: UUID
    report_upload_id: UUID
    lease_token: UUID
    attempt_count: int
    max_attempts: int
    storage_bucket: str
    storage_key: str
    storage_version_id: str
    source_etag: str
    original_filename: str
    file_extension: str
    content_type: str
    expected_size_bytes: int


@dataclass(frozen=True, slots=True)
class ReportProcessingOutput:
    storage_bucket: str
    normalized_storage_key: str
    normalized_storage_version_id: str
    normalized_etag: str
    normalized_size_bytes: int
    profile_storage_key: str
    profile_storage_version_id: str
    profile_etag: str
    record_count: int
    sheet_count: int


def validate_processing_error_code(error_code: str) -> str:
    if not _SAFE_ERROR_CODE.fullmatch(error_code):
        raise ValueError("Processing error codes must be safe snake_case identifiers.")
    return error_code


def _validate_output(output: ReportProcessingOutput) -> None:
    required_strings = (
        output.storage_bucket,
        output.normalized_storage_key,
        output.normalized_storage_version_id,
        output.normalized_etag,
        output.profile_storage_key,
        output.profile_storage_version_id,
        output.profile_etag,
    )
    if any(not value.strip() for value in required_strings):
        raise ValueError("Completed processing outputs require non-empty storage metadata.")
    if not 3 <= len(output.storage_bucket) <= 63:
        raise ValueError("The processing result bucket length is invalid.")
    if output.normalized_size_bytes <= 0:
        raise ValueError("The normalized output must contain at least one byte.")
    if output.record_count < 0 or output.sheet_count <= 0:
        raise ValueError("Processing output counts are invalid.")


def _terminal_failure(
    job: ReportProcessingJob,
    *,
    error_code: str,
    now: datetime,
) -> None:
    if job.attempt_count == 0:
        job.attempt_count = 1
    if job.started_at is None:
        job.started_at = now
    job.status = ReportProcessingStatus.FAILED
    job.available_at = None
    job.lease_token = None
    job.lease_expires_at = None
    job.error_code = error_code
    job.finished_at = now


class ReportProcessingService:
    def __init__(self, *, session: AsyncSession) -> None:
        self.session = session

    async def ensure_jobs_for_uploads(
        self,
        *,
        scope: TenantScope,
        uploads: Iterable[ReportUpload],
    ) -> tuple[ReportProcessingJob, ...]:
        verified_uploads = tuple(
            upload
            for upload in uploads
            if ReportUploadStatus(upload.status) is ReportUploadStatus.UPLOADED
        )
        if not verified_uploads:
            return ()
        if any(upload.business_id != scope.business_id for upload in verified_uploads):
            raise ReportProcessingConflictError
        if any(not upload.storage_version_id or not upload.etag for upload in verified_uploads):
            raise ReportProcessingConflictError

        upload_ids = tuple(upload.id for upload in verified_uploads)
        existing_result = await self.session.execute(
            scope.select(
                ReportProcessingJob,
                ReportProcessingJob.report_upload_id.in_(upload_ids),
            )
        )
        existing_by_upload_id = {
            job.report_upload_id: job for job in existing_result.scalars().all()
        }

        jobs: list[ReportProcessingJob] = []
        for upload in verified_uploads:
            existing = existing_by_upload_id.get(upload.id)
            if existing is not None:
                if (
                    existing.source_storage_version_id != upload.storage_version_id
                    or existing.source_etag != upload.etag
                ):
                    raise ReportProcessingConflictError
                jobs.append(existing)
                continue

            job = ReportProcessingJob(
                business_id=scope.business_id,
                report_upload_id=upload.id,
                source_storage_version_id=upload.storage_version_id or "",
                source_etag=upload.etag or "",
            )
            self.session.add(job)
            jobs.append(job)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ReportProcessingConflictError from exc
        return tuple(jobs)

    async def dispatch_due_jobs(
        self,
        *,
        job_ids: Iterable[UUID],
        dispatcher: ReportProcessingDispatcher,
        now: datetime | None = None,
    ) -> int:
        dispatched = 0
        dispatch_time = now or utc_now()
        redispatch_before = dispatch_time - REPORT_PROCESSING_REDISPATCH_INTERVAL

        for job_id in dict.fromkeys(job_ids):
            try:
                result = await self.session.execute(
                    select(ReportProcessingJob).where(ReportProcessingJob.id == job_id)
                )
                job = result.scalar_one_or_none()
                is_due = (
                    job is not None
                    and ReportProcessingStatus(job.status) is ReportProcessingStatus.QUEUED
                    and job.available_at is not None
                    and job.available_at <= dispatch_time
                    and (
                        job.last_dispatched_at is None
                        or job.last_dispatched_at <= redispatch_before
                    )
                )
                # End the read transaction before touching the broker. A commit is
                # intentional here: rollback would expire the already-committed batch
                # objects that the API still needs for its response.
                await self.session.commit()
            except SQLAlchemyError as exc:
                await self.session.rollback()
                raise ReportProcessingDispatchError from exc

            if not is_due:
                continue

            await dispatcher.dispatch(job_id)
            recorded_at = utc_now()
            try:
                locked_result = await self.session.execute(
                    select(ReportProcessingJob)
                    .where(ReportProcessingJob.id == job_id)
                    .with_for_update()
                )
                locked_job = locked_result.scalar_one_or_none()
                if locked_job is not None:
                    locked_job.dispatch_attempt_count += 1
                    locked_job.last_dispatched_at = recorded_at
                await self.session.commit()
            except SQLAlchemyError as exc:
                await self.session.rollback()
                raise ReportProcessingDispatchError from exc
            dispatched += 1

        return dispatched

    async def claim_job(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
        lease_duration: timedelta = REPORT_PROCESSING_LEASE_DURATION,
    ) -> ReportProcessingClaim | None:
        if lease_duration <= timedelta(0):
            raise ValueError("The processing lease duration must be positive.")
        claimed_at = now or utc_now()
        result = await self.session.execute(
            select(ReportProcessingJob).where(ReportProcessingJob.id == job_id).with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None:
            await self.session.commit()
            return None

        status = ReportProcessingStatus(job.status)
        if status in {ReportProcessingStatus.COMPLETED, ReportProcessingStatus.FAILED}:
            await self.session.commit()
            return None
        if (
            status is ReportProcessingStatus.PROCESSING
            and job.lease_expires_at is not None
            and job.lease_expires_at > claimed_at
        ):
            await self.session.commit()
            return None
        if (
            status is ReportProcessingStatus.QUEUED
            and job.available_at is not None
            and job.available_at > claimed_at
        ):
            await self.session.commit()
            return None
        if job.attempt_count >= job.max_attempts:
            _terminal_failure(
                job,
                error_code=REPORT_PROCESSING_ATTEMPT_LIMIT_ERROR,
                now=claimed_at,
            )
            await self.session.commit()
            return None

        upload_result = await self.session.execute(
            select(ReportUpload).where(
                ReportUpload.id == job.report_upload_id,
                ReportUpload.business_id == job.business_id,
            )
        )
        upload = upload_result.scalar_one_or_none()
        source_is_valid = (
            upload is not None
            and ReportUploadStatus(upload.status) is ReportUploadStatus.UPLOADED
            and upload.storage_version_id == job.source_storage_version_id
            and upload.etag == job.source_etag
            and upload.actual_size_bytes is not None
        )
        if not source_is_valid or upload is None:
            _terminal_failure(
                job,
                error_code=REPORT_PROCESSING_SOURCE_INVALID_ERROR,
                now=claimed_at,
            )
            await self.session.commit()
            return None

        lease_token = uuid4()
        job.status = ReportProcessingStatus.PROCESSING
        job.attempt_count += 1
        job.available_at = None
        job.started_at = job.started_at or claimed_at
        job.lease_token = lease_token
        job.lease_expires_at = claimed_at + lease_duration
        job.finished_at = None
        job.error_code = None
        await self.session.commit()

        return ReportProcessingClaim(
            job_id=job.id,
            business_id=job.business_id,
            report_upload_id=job.report_upload_id,
            lease_token=lease_token,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            storage_bucket=upload.storage_bucket,
            storage_key=upload.storage_key,
            storage_version_id=job.source_storage_version_id,
            source_etag=job.source_etag,
            original_filename=upload.original_filename,
            file_extension=upload.file_extension,
            content_type=upload.content_type,
            expected_size_bytes=upload.actual_size_bytes,
        )

    async def claim_retry_delay(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> timedelta | None:
        """Return when a safe duplicate/redelivery should try to claim again."""

        checked_at = now or utc_now()
        result = await self.session.execute(
            select(ReportProcessingJob).where(ReportProcessingJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        retry_delay: timedelta | None = None
        if job is not None:
            status = ReportProcessingStatus(job.status)
            if status is ReportProcessingStatus.QUEUED:
                retry_delay = max(
                    (job.available_at or checked_at) - checked_at,
                    timedelta(seconds=1),
                )
            elif status is ReportProcessingStatus.PROCESSING:
                retry_delay = max(
                    (job.lease_expires_at or checked_at) - checked_at,
                    timedelta(seconds=1),
                )
        await self.session.commit()
        return retry_delay

    async def retry_job(
        self,
        job_id: UUID,
        *,
        lease_token: UUID,
        error_code: str,
        retry_delay: timedelta,
        now: datetime | None = None,
    ) -> bool:
        validate_processing_error_code(error_code)
        if retry_delay < timedelta(0):
            raise ValueError("The processing retry delay cannot be negative.")
        transition_at = now or utc_now()
        job = await self._locked_processing_job(job_id, lease_token)
        if job is None:
            return False

        if job.attempt_count >= job.max_attempts:
            _terminal_failure(job, error_code=error_code, now=transition_at)
        else:
            job.status = ReportProcessingStatus.QUEUED
            job.available_at = transition_at + retry_delay
            job.lease_token = None
            job.lease_expires_at = None
            job.error_code = error_code
        await self.session.commit()
        return True

    async def fail_job(
        self,
        job_id: UUID,
        *,
        lease_token: UUID,
        error_code: str,
        now: datetime | None = None,
    ) -> bool:
        validate_processing_error_code(error_code)
        transition_at = now or utc_now()
        job = await self._locked_processing_job(job_id, lease_token)
        if job is None:
            return False
        _terminal_failure(job, error_code=error_code, now=transition_at)
        await self.session.commit()
        return True

    async def complete_job(
        self,
        job_id: UUID,
        *,
        lease_token: UUID,
        output: ReportProcessingOutput,
        now: datetime | None = None,
    ) -> bool:
        _validate_output(output)
        transition_at = now or utc_now()
        job = await self._locked_processing_job(job_id, lease_token)
        if job is None:
            return False

        job.status = ReportProcessingStatus.COMPLETED
        job.available_at = None
        job.lease_token = None
        job.lease_expires_at = None
        job.finished_at = transition_at
        job.error_code = None
        job.result_storage_bucket = output.storage_bucket
        job.normalized_storage_key = output.normalized_storage_key
        job.normalized_storage_version_id = output.normalized_storage_version_id
        job.normalized_etag = output.normalized_etag
        job.normalized_size_bytes = output.normalized_size_bytes
        job.profile_storage_key = output.profile_storage_key
        job.profile_storage_version_id = output.profile_storage_version_id
        job.profile_etag = output.profile_etag
        job.record_count = output.record_count
        job.sheet_count = output.sheet_count
        await self.session.commit()
        return True

    async def _locked_processing_job(
        self,
        job_id: UUID,
        lease_token: UUID,
    ) -> ReportProcessingJob | None:
        result = await self.session.execute(
            select(ReportProcessingJob).where(ReportProcessingJob.id == job_id).with_for_update()
        )
        job = result.scalar_one_or_none()
        if (
            job is None
            or ReportProcessingStatus(job.status) is not ReportProcessingStatus.PROCESSING
            or job.lease_token != lease_token
        ):
            await self.session.commit()
            return None
        return job


__all__ = [
    "REPORT_PROCESSING_ATTEMPT_LIMIT_ERROR",
    "REPORT_PROCESSING_LEASE_DURATION",
    "REPORT_PROCESSING_REDISPATCH_INTERVAL",
    "REPORT_PROCESSING_SOURCE_INVALID_ERROR",
    "ReportProcessingClaim",
    "ReportProcessingConflictError",
    "ReportProcessingOutput",
    "ReportProcessingService",
    "validate_processing_error_code",
]
