from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.base import utc_now
from app.models.processing import ReportProcessingJob
from app.services.processing import ReportProcessingService, validate_processing_error_code
from app.services.processing_dispatch import (
    REPORT_PROCESSING_TASK_NAME,
    CeleryReportProcessingDispatcher,
    ReportProcessingDispatchError,
)
from app.workers.celery_app import celery_app
from app.workers.tasks import process_report_upload


class ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.parametrize(
    "error_code",
    [
        "malformed_data",
        "temporary_s3_failure",
        "database_unavailable_2",
    ],
)
def test_processing_error_codes_accept_only_safe_identifiers(error_code: str) -> None:
    assert validate_processing_error_code(error_code) == error_code


@pytest.mark.parametrize(
    "error_code",
    [
        "",
        "S3Failure",
        "../../private-key",
        "database error: password=secret",
        "a" * 65,
    ],
)
def test_processing_error_codes_reject_internal_or_unsafe_details(error_code: str) -> None:
    with pytest.raises(ValueError, match="safe snake_case"):
        validate_processing_error_code(error_code)


@pytest.mark.asyncio
async def test_celery_dispatch_uses_the_job_uuid_as_the_task_id(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_send_task(name: str, **kwargs):
        calls.append({"name": name, **kwargs})
        return object()

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)
    job_id = uuid4()

    await CeleryReportProcessingDispatcher().dispatch(job_id)

    assert calls == [
        {
            "name": REPORT_PROCESSING_TASK_NAME,
            "args": [str(job_id)],
            "task_id": str(job_id),
        }
    ]


@pytest.mark.asyncio
async def test_celery_dispatch_hides_broker_exception_details(monkeypatch) -> None:
    def failing_send_task(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("redis://user:secret@private-broker")

    monkeypatch.setattr(celery_app, "send_task", failing_send_task)

    with pytest.raises(ReportProcessingDispatchError) as captured:
        await CeleryReportProcessingDispatcher().dispatch(uuid4())

    assert str(captured.value) == ""


@pytest.mark.asyncio
async def test_dispatch_service_commits_before_publish_and_records_success() -> None:
    session = Mock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    now = utc_now()
    job = ReportProcessingJob(
        business_id=uuid4(),
        report_upload_id=uuid4(),
        source_storage_version_id="source-version",
        source_etag="source-etag",
        available_at=now,
    )
    session.execute.side_effect = [ScalarResult(job), ScalarResult(job)]
    published: list[object] = []

    class CommitCheckingDispatcher:
        async def dispatch(self, job_id) -> None:
            assert session.commit.await_count == 1
            published.append(job_id)

    dispatched = await ReportProcessingService(session=session).dispatch_due_jobs(
        job_ids=(job.id,),
        dispatcher=CommitCheckingDispatcher(),
        now=now,
    )

    assert dispatched == 1
    assert published == [job.id]
    assert session.commit.await_count == 2
    assert session.rollback.await_count == 0
    assert job.dispatch_attempt_count == 1
    assert job.last_dispatched_at is not None


def test_report_processing_task_is_registered_and_rejects_invalid_ids() -> None:
    assert process_report_upload.name == REPORT_PROCESSING_TASK_NAME
    assert process_report_upload.apply(args=["not-a-job-uuid"]).get() == {"status": "rejected"}
