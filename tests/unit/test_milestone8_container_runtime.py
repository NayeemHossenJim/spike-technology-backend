from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_runtime_image_runs_as_non_root_app_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "\nUSER app\n" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile


def test_migrations_are_separate_from_api_startup() -> None:
    services = load_compose()["services"]

    migrate = services["migrate"]
    api = services["api"]

    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert migrate["restart"] == "no"

    assert api["command"][0] == "uvicorn"
    assert "alembic" not in " ".join(api["command"])

    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_worker_waits_for_successful_schema_migration() -> None:
    worker = load_compose()["services"]["worker"]

    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_api_and_worker_have_hardened_runtime_controls() -> None:
    services = load_compose()["services"]

    for name in ("api", "worker"):
        service = services[name]

        assert service["init"] is True
        assert service["restart"] == "unless-stopped"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert any(entry.startswith("/tmp:") for entry in service["tmpfs"])
        assert service["environment"]["XDG_CACHE_HOME"] == "/tmp/.cache"


def test_migration_service_is_hardened_and_one_shot() -> None:
    migrate = load_compose()["services"]["migrate"]

    assert migrate["read_only"] is True
    assert migrate["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in migrate["security_opt"]
    assert migrate["restart"] == "no"


def test_api_healthcheck_uses_liveness_not_external_dependencies() -> None:
    api = load_compose()["services"]["api"]
    command = " ".join(api["healthcheck"]["test"])

    assert "/api/v1/health/live" in command
    assert "/api/v1/health/ready" not in command


def test_worker_healthcheck_targets_the_current_worker_node() -> None:
    worker = load_compose()["services"]["worker"]
    command = " ".join(worker["healthcheck"]["test"])

    assert "inspect ping" in command
    assert "celery@$$HOSTNAME" in command


def test_local_database_and_redis_ports_remain_available_for_development() -> None:
    services = load_compose()["services"]

    assert services["postgres"]["ports"] == ["5432:5432"]
    assert services["redis"]["ports"] == ["6379:6379"]
