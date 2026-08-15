from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.metrics import ReportProcessingMetricsSnapshot
from app.models.base import utc_now
from app.models.processing import ReportProcessingJob, ReportProcessingStatus


async def load_report_processing_metrics(
    *,
    session: AsyncSession,
    now: datetime | None = None,
) -> ReportProcessingMetricsSnapshot:
    checked_at = now or utc_now()

    status_result = await session.execute(
        select(
            ReportProcessingJob.status,
            func.count(),
        ).group_by(ReportProcessingJob.status)
    )

    status_counts = {
        ReportProcessingStatus(status).value: int(count) for status, count in status_result.all()
    }

    failure_result = await session.execute(
        select(
            ReportProcessingJob.error_code,
            func.count(),
        )
        .where(
            ReportProcessingJob.status == ReportProcessingStatus.FAILED,
            ReportProcessingJob.error_code.is_not(None),
        )
        .group_by(ReportProcessingJob.error_code)
    )

    failed_by_error_code = {
        str(error_code): int(count)
        for error_code, count in failure_result.all()
        if error_code is not None
    }

    retrying_result = await session.execute(
        select(func.count())
        .select_from(ReportProcessingJob)
        .where(
            ReportProcessingJob.status == ReportProcessingStatus.QUEUED,
            ReportProcessingJob.attempt_count > 0,
        )
    )

    stale_lease_result = await session.execute(
        select(func.count())
        .select_from(ReportProcessingJob)
        .where(
            ReportProcessingJob.status == ReportProcessingStatus.PROCESSING,
            ReportProcessingJob.lease_expires_at.is_not(None),
            ReportProcessingJob.lease_expires_at <= checked_at,
        )
    )

    return ReportProcessingMetricsSnapshot(
        status_counts=status_counts,
        failed_by_error_code=failed_by_error_code,
        retrying_jobs=int(retrying_result.scalar_one()),
        stale_leases=int(stale_lease_result.scalar_one()),
    )


__all__ = [
    "load_report_processing_metrics",
]
