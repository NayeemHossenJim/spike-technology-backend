from __future__ import annotations

from functools import lru_cache
from typing import Protocol
from uuid import UUID

from asyncer import asyncify

from app.workers.celery_app import celery_app

REPORT_PROCESSING_TASK_NAME = "spike.reports.process"


class ReportProcessingDispatchError(Exception):
    """A processing job remains durable, but broker publication did not finish safely."""


class ReportProcessingDispatcher(Protocol):
    async def dispatch(self, job_id: UUID) -> None: ...


class CeleryReportProcessingDispatcher:
    async def dispatch(self, job_id: UUID) -> None:
        serialized_job_id = str(job_id)
        try:
            await asyncify(celery_app.send_task)(
                REPORT_PROCESSING_TASK_NAME,
                args=[serialized_job_id],
                task_id=serialized_job_id,
            )
        except Exception as exc:
            raise ReportProcessingDispatchError from exc


@lru_cache
def get_report_processing_dispatcher() -> ReportProcessingDispatcher:
    return CeleryReportProcessingDispatcher()


__all__ = [
    "REPORT_PROCESSING_TASK_NAME",
    "CeleryReportProcessingDispatcher",
    "ReportProcessingDispatchError",
    "ReportProcessingDispatcher",
    "get_report_processing_dispatcher",
]
