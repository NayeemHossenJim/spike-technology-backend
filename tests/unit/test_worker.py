from app.workers.tasks import ping


def test_celery_smoke_task_runs_locally() -> None:
    result = ping.apply().get()
    assert result["status"] == "ok"
    assert "at" in result
