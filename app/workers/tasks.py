from __future__ import annotations

from datetime import UTC, datetime

from app.workers.celery_app import celery_app


@celery_app.task(name="spike.system.ping")
def ping() -> dict[str, str]:
    """Phase 1 worker smoke task; Phase 2 adds ingestion and AI tasks."""

    return {"status": "ok", "at": datetime.now(UTC).isoformat()}
