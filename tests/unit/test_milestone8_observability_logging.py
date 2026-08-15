from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import AppEnvironment
from app.core.logging import (
    JsonLogFormatter,
    configure_logging,
)
from app.main import create_app
from app.workers import tasks as worker_tasks
from app.workers.tasks import (
    ReportProcessingTaskOutcome,
    process_report_upload,
)


def _record(
    message: str = "test_event",
    *,
    level: int = logging.INFO,
) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_emits_base_contract() -> None:
    formatter = JsonLogFormatter(
        service="Spike Technology API",
        environment="production",
    )

    payload = json.loads(formatter.format(_record()))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["service"] == "Spike Technology API"
    assert payload["environment"] == "production"
    assert payload["event"] == "test_event"
    assert payload["timestamp"].endswith("Z")


def test_json_formatter_uses_whitelisted_context_only() -> None:
    record = _record("http_request_completed")
    record.request_id = "request-123"
    record.method = "GET"
    record.route = "/api/v1/items/{item_id}"
    record.status_code = 200
    record.duration_ms = 17
    record.authorization = "Bearer must-not-be-serialized"

    formatter = JsonLogFormatter(
        service="Spike Technology API",
        environment="production",
    )

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert payload["request_id"] == "request-123"
    assert payload["method"] == "GET"
    assert payload["route"] == ("/api/v1/items/{item_id}")
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 17
    assert "authorization" not in payload
    assert "must-not-be-serialized" not in rendered


def test_json_formatter_omits_exception_message() -> None:
    try:
        raise ValueError("sensitive-provider-detail")
    except ValueError:
        exc_info = sys.exc_info()

    record = _record(
        "safe_failure_event",
        level=logging.ERROR,
    )
    record.exc_info = exc_info

    formatter = JsonLogFormatter(
        service="Spike Technology API",
        environment="production",
    )

    rendered = formatter.format(record)
    payload = json.loads(rendered)

    assert payload["exception_type"] == "ValueError"
    assert "sensitive-provider-detail" not in rendered


def test_production_logging_configures_json(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    class FakeLogger:
        def __init__(self) -> None:
            self.disabled = False
            self.handlers: list[logging.Handler] = []

    loggers = {
        "uvicorn.access": FakeLogger(),
        "uvicorn": FakeLogger(),
        "uvicorn.error": FakeLogger(),
    }

    original_get_logger = logging.getLogger

    def fake_get_logger(
        name: str | None = None,
    ):
        if name in loggers:
            return loggers[name]

        return original_get_logger(name)

    def fake_basic_config(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        logging,
        "getLogger",
        fake_get_logger,
    )
    monkeypatch.setattr(
        logging,
        "basicConfig",
        fake_basic_config,
    )

    settings = SimpleNamespace(
        app_name="Spike Technology API",
        app_env=AppEnvironment.PRODUCTION,
        log_level="INFO",
    )

    configure_logging(  # type: ignore[arg-type]
        settings
    )

    assert calls
    assert calls[-1]["force"] is True

    handlers = calls[-1]["handlers"]

    assert len(handlers) == 1
    assert isinstance(
        handlers[0].formatter,
        JsonLogFormatter,
    )
    assert loggers["uvicorn.access"].disabled is True


def test_http_log_preserves_request_id_and_route(
    caplog,
) -> None:
    application = create_app()
    client = TestClient(application)

    try:
        with caplog.at_level(
            logging.INFO,
            logger="app.main",
        ):
            response = client.get(
                "/api/v1/health/live?token=must-not-be-logged",
                headers={
                    "X-Request-ID": "observability-request-001",
                },
            )
    finally:
        client.close()

    assert response.status_code == 200

    records = [
        record
        for record in caplog.records
        if record.name == "app.main" and record.getMessage() == "http_request_completed"
    ]

    assert records

    record = records[-1]

    assert record.request_id == "observability-request-001"
    assert record.method == "GET"
    assert record.route == "/health/live"
    assert record.status_code == 200
    assert record.outcome == "success"
    assert isinstance(record.duration_ms, int)

    assert "must-not-be-logged" not in record.getMessage()
    assert "must-not-be-logged" not in record.route


def test_unmatched_request_does_not_log_raw_target(
    caplog,
) -> None:
    application = create_app()
    sensitive_fragment = "private-user-supplied-segment"
    client = TestClient(application)

    try:
        with caplog.at_level(
            logging.INFO,
            logger="app.main",
        ):
            response = client.get(f"/{sensitive_fragment}?secret=must-not-be-logged")
    finally:
        client.close()

    assert response.status_code == 404

    records = [
        record
        for record in caplog.records
        if record.name == "app.main" and record.getMessage() == "http_request_completed"
    ]

    assert records

    record = records[-1]

    assert record.route == "<unmatched>"
    assert record.status_code == 404
    assert record.outcome == "client_error"

    assert sensitive_fragment not in record.getMessage()
    assert sensitive_fragment not in record.route
    assert "must-not-be-logged" not in record.getMessage()
    assert "must-not-be-logged" not in record.route


def test_processing_task_rejection_is_safe(
    caplog,
) -> None:
    invalid_job_id = "not-a-valid-job-id"

    with caplog.at_level(
        logging.WARNING,
        logger="app.workers.tasks",
    ):
        result = process_report_upload.run(invalid_job_id)

    assert result == {"status": "rejected"}

    records = [
        record
        for record in caplog.records
        if record.name == "app.workers.tasks"
        and record.getMessage() == "report_processing_task_rejected"
    ]

    assert records
    assert records[-1].outcome == "rejected"
    assert invalid_job_id not in caplog.text


def test_processing_task_completion_is_logged(
    monkeypatch,
    caplog,
) -> None:
    job_id = uuid4()

    def fake_run(coroutine):
        coroutine.close()

        return ReportProcessingTaskOutcome(
            status="completed",
        )

    monkeypatch.setattr(
        worker_tasks.asyncio,
        "run",
        fake_run,
    )

    with caplog.at_level(
        logging.INFO,
        logger="app.workers.tasks",
    ):
        result = process_report_upload.run(str(job_id))

    assert result == {"status": "completed"}

    records = [
        record
        for record in caplog.records
        if record.name == "app.workers.tasks"
        and record.getMessage() == "report_processing_task_outcome"
    ]

    assert records

    record = records[-1]

    assert record.job_id == str(job_id)
    assert record.outcome == "completed"
    assert record.retry_after_seconds is None
    assert isinstance(record.duration_ms, int)
