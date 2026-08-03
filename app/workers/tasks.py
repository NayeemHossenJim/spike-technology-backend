from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import async_session_factory, dispose_database
from app.services.processing import ReportProcessingService
from app.services.processing_dispatch import REPORT_PROCESSING_TASK_NAME
from app.workers.celery_app import celery_app


@celery_app.task(name="spike.system.ping")
def ping() -> dict[str, str]:
    """Phase 1 worker smoke task; Phase 2 adds ingestion and AI tasks."""

    return {"status": "ok", "at": datetime.now(UTC).isoformat()}


async def _claim_report_processing_job(job_id: UUID) -> dict[str, str]:
    try:
        async with async_session_factory() as session:
            claim = await ReportProcessingService(session=session).claim_job(job_id)
    finally:
        # Celery calls this coroutine through asyncio.run. Clear loop-bound asyncpg
        # connections before the temporary event loop is closed.
        await dispose_database()
    return {"status": "claimed" if claim is not None else "ignored"}


@celery_app.task(
    name=REPORT_PROCESSING_TASK_NAME,
    autoretry_for=(SQLAlchemyError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def process_report_upload(job_id: str) -> dict[str, str]:
    """Claim one durable job; parsing and output creation are added in Stage 3."""

    try:
        parsed_job_id = UUID(job_id)
    except (TypeError, ValueError):
        return {"status": "rejected"}
    return asyncio.run(_claim_report_processing_job(parsed_job_id))


__all__ = ["ping", "process_report_upload"]
