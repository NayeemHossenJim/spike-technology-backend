from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Final

from app.core.config import AppEnvironment, Settings

OBSERVABILITY_LOG_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "outcome",
    "error_code",
    "exception_type",
    "business_id",
    "user_id",
    "conversation_id",
    "message_id",
    "assistant_message_id",
    "ledger_entry_id",
    "provider_response_id",
    "provider_model",
    "prompt_token_count",
    "response_token_count",
    "total_token_count",
    "replayed",
    "task_id",
    "job_id",
    "report_upload_id",
    "attempt_count",
    "max_attempts",
    "retry_after_seconds",
    "phase",
)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        timestamp = (
            datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

        payload: dict[str, object] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "service": self.service,
            "environment": self.environment,
            "event": record.getMessage(),
        }

        for field_name in OBSERVABILITY_LOG_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        if record.exc_info and record.exc_info[0] is not None:
            payload.setdefault(
                "exception_type",
                record.exc_info[0].__name__,
            )

        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def _json_handler(settings: Settings) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonLogFormatter(
            service=settings.app_name,
            environment=settings.app_env.value,
        )
    )
    return handler


def configure_logging(settings: Settings) -> None:
    level = getattr(
        logging,
        settings.log_level.upper(),
        logging.INFO,
    )

    if settings.app_env is AppEnvironment.PRODUCTION:
        formatter = JsonLogFormatter(
            service=settings.app_name,
            environment=settings.app_env.value,
        )

        logging.basicConfig(
            level=level,
            handlers=[_json_handler(settings)],
            force=True,
        )

        # Uvicorn's default access log includes the raw request target,
        # including query strings. Our application middleware emits a safer
        # route-template request event instead.
        logging.getLogger("uvicorn.access").disabled = True

        # Preserve Uvicorn operational messages while formatting any existing
        # handlers consistently with the application JSON log stream.
        for logger_name in (
            "uvicorn",
            "uvicorn.error",
        ):
            named_logger = logging.getLogger(logger_name)
            for handler in named_logger.handlers:
                handler.setFormatter(formatter)

        return

    logging.getLogger("uvicorn.access").disabled = False

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


__all__ = [
    "JsonLogFormatter",
    "OBSERVABILITY_LOG_FIELDS",
    "configure_logging",
]
