from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import async_session_factory, dispose_database
from app.models.processing import ReportProcessingJob, ReportProcessingStatus
from app.services.processing import (
    REPORT_PROCESSING_LEASE_DURATION,
    ReportProcessingClaim,
    ReportProcessingOutput,
    ReportProcessingService,
)
from app.services.processing_artifacts import (
    ReportArtifactProcessor,
    RetryableReportProcessingError,
    TerminalReportProcessingError,
)
from app.services.processing_dispatch import REPORT_PROCESSING_TASK_NAME
from app.services.s3_storage import S3UploadGateway, get_s3_upload_gateway
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

REPORT_PROCESSING_INFRASTRUCTURE_RETRY_DELAY = timedelta(seconds=30)
REPORT_PROCESSING_LEASE_RETRY_BUFFER = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class ReportProcessingTaskOutcome:
    status: str
    retry_after_seconds: int | None = None


def _retry_seconds(delay: timedelta) -> int:
    return max(1, math.ceil(delay.total_seconds()))


@celery_app.task(name="spike.system.ping")
def ping() -> dict[str, str]:
    """Phase 1 worker smoke task."""

    return {"status": "ok", "at": datetime.now(UTC).isoformat()}


async def _claim_job(
    job_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[ReportProcessingClaim | None, ReportProcessingTaskOutcome | None]:
    try:
        async with session_factory() as session:
            service = ReportProcessingService(session=session)
            claim = await service.claim_job(job_id)
            if claim is not None:
                return claim, None
            retry_delay = await service.claim_retry_delay(job_id)
    except SQLAlchemyError:
        return None, ReportProcessingTaskOutcome(
            status="retrying",
            retry_after_seconds=_retry_seconds(REPORT_PROCESSING_INFRASTRUCTURE_RETRY_DELAY),
        )

    if retry_delay is None:
        return None, ReportProcessingTaskOutcome(status="ignored")
    return None, ReportProcessingTaskOutcome(
        status="deferred",
        retry_after_seconds=_retry_seconds(retry_delay),
    )


async def _fail_claim(
    claim: ReportProcessingClaim,
    *,
    error_code: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> ReportProcessingTaskOutcome:
    try:
        async with session_factory() as session:
            changed = await ReportProcessingService(session=session).fail_job(
                claim.job_id,
                lease_token=claim.lease_token,
                error_code=error_code,
            )
    except SQLAlchemyError:
        return ReportProcessingTaskOutcome(
            status="retrying",
            retry_after_seconds=_retry_seconds(
                REPORT_PROCESSING_LEASE_DURATION + REPORT_PROCESSING_LEASE_RETRY_BUFFER
            ),
        )
    return ReportProcessingTaskOutcome(status="failed" if changed else "ignored")


async def _retry_claim(
    claim: ReportProcessingClaim,
    *,
    error_code: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> ReportProcessingTaskOutcome:
    try:
        async with session_factory() as session:
            changed = await ReportProcessingService(session=session).retry_job(
                claim.job_id,
                lease_token=claim.lease_token,
                error_code=error_code,
                retry_delay=REPORT_PROCESSING_INFRASTRUCTURE_RETRY_DELAY,
            )
            job = await session.get(ReportProcessingJob, claim.job_id) if changed else None
    except SQLAlchemyError:
        return ReportProcessingTaskOutcome(
            status="retrying",
            retry_after_seconds=_retry_seconds(
                REPORT_PROCESSING_LEASE_DURATION + REPORT_PROCESSING_LEASE_RETRY_BUFFER
            ),
        )
    if not changed:
        return ReportProcessingTaskOutcome(status="ignored")
    if job is not None and ReportProcessingStatus(job.status) is ReportProcessingStatus.FAILED:
        return ReportProcessingTaskOutcome(status="failed")
    return ReportProcessingTaskOutcome(
        status="retrying",
        retry_after_seconds=_retry_seconds(REPORT_PROCESSING_INFRASTRUCTURE_RETRY_DELAY),
    )


async def _complete_claim(
    claim: ReportProcessingClaim,
    *,
    output: ReportProcessingOutput,
    session_factory: async_sessionmaker[AsyncSession],
) -> ReportProcessingTaskOutcome:
    try:
        async with session_factory() as session:
            changed = await ReportProcessingService(session=session).complete_job(
                claim.job_id,
                lease_token=claim.lease_token,
                output=output,
            )
    except SQLAlchemyError:
        return ReportProcessingTaskOutcome(
            status="retrying",
            retry_after_seconds=_retry_seconds(
                REPORT_PROCESSING_LEASE_DURATION + REPORT_PROCESSING_LEASE_RETRY_BUFFER
            ),
        )
    return ReportProcessingTaskOutcome(status="completed" if changed else "ignored")


async def execute_report_processing_job(
    job_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_factory,
    settings: Settings | None = None,
    storage: S3UploadGateway | None = None,
) -> ReportProcessingTaskOutcome:
    """Execute one lease-guarded processing attempt without exposing internal details."""

    claim, outcome = await _claim_job(job_id, session_factory=session_factory)
    if claim is None:
        return outcome or ReportProcessingTaskOutcome(status="ignored")

    processor = ReportArtifactProcessor(
        settings=settings or get_settings(),
        storage=storage or get_s3_upload_gateway(),
    )
    try:
        output = await processor.process(claim)
    except TerminalReportProcessingError as exc:
        return await _fail_claim(
            claim,
            error_code=exc.code,
            session_factory=session_factory,
        )
    except RetryableReportProcessingError as exc:
        return await _retry_claim(
            claim,
            error_code=exc.code,
            session_factory=session_factory,
        )
    except Exception as exc:
        logger.error(
            "Unexpected report-processing failure for job %s (%s)",
            job_id,
            type(exc).__name__,
        )
        return await _fail_claim(
            claim,
            error_code="processing_internal_error",
            session_factory=session_factory,
        )

    return await _complete_claim(
        claim,
        output=output,
        session_factory=session_factory,
    )


async def _execute_default_report_processing_job(job_id: UUID) -> ReportProcessingTaskOutcome:
    try:
        return await execute_report_processing_job(job_id)
    finally:
        # Celery enters through asyncio.run. Dispose loop-bound asyncpg connections
        # before that temporary event loop closes.
        await dispose_database()


@celery_app.task(
    bind=True,
    name=REPORT_PROCESSING_TASK_NAME,
    max_retries=None,
)
def process_report_upload(self, job_id: str) -> dict[str, str]:
    """Process one durable job; safe deferrals retain the original Celery task UUID."""

    try:
        parsed_job_id = UUID(job_id)
    except (TypeError, ValueError):
        return {"status": "rejected"}

    outcome = asyncio.run(_execute_default_report_processing_job(parsed_job_id))
    if outcome.retry_after_seconds is not None:
        raise self.retry(countdown=outcome.retry_after_seconds)
    return {"status": outcome.status}


__all__ = [
    "ReportProcessingTaskOutcome",
    "execute_report_processing_job",
    "ping",
    "process_report_upload",
]
