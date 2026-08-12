from __future__ import annotations

from app.workers.celery_app import celery_app


def test_worker_retries_broker_connection_during_startup() -> None:
    assert celery_app.conf.broker_connection_retry is True
    assert celery_app.conf.broker_connection_retry_on_startup is True


def test_worker_cancels_late_ack_task_after_connection_loss() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True


def test_worker_redis_connections_use_bounded_socket_timeouts() -> None:
    settings = celery_app.conf

    broker_options = dict(settings.broker_transport_options or {})

    assert broker_options["socket_connect_timeout"] > 0
    assert broker_options["socket_timeout"] > 0
    assert settings.redis_socket_connect_timeout > 0
    assert settings.redis_socket_timeout > 0
    assert settings.redis_retry_on_timeout is True


def test_worker_keeps_existing_prefetch_and_result_policy() -> None:
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_track_started is True
    assert celery_app.conf.result_expires is not None
