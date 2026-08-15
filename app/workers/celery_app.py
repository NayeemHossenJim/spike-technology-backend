from __future__ import annotations

from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings)

celery_app = Celery(
    "spike_backend",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_send_sent_event=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    worker_send_task_events=True,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_transport_options={
        "socket_connect_timeout": settings.redis_socket_connect_timeout_seconds,
        "socket_timeout": settings.redis_socket_timeout_seconds,
    },
    redis_socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    redis_socket_timeout=settings.redis_socket_timeout_seconds,
    redis_retry_on_timeout=True,
)
