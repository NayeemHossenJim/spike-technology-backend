import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"

CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_UV_ACTION = "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"


def load_workflow() -> dict:
    return yaml.load(
        WORKFLOW_PATH.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def run_commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"] if isinstance(step, dict))


def test_ci_workflow_uses_safe_triggers_permissions_and_concurrency() -> None:
    workflow = load_workflow()
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    triggers = workflow["on"]

    assert set(triggers) == {
        "push",
        "pull_request",
        "workflow_dispatch",
    }
    assert triggers["pull_request"]["branches"] == ["develop"]
    assert "develop" in triggers["push"]["branches"]
    assert "milestone-*" in triggers["push"]["branches"]

    assert "pull_request_target" not in text
    assert workflow["permissions"] == {"contents": "read"}

    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] == "true"
    assert "${{ github.workflow }}" in concurrency["group"]
    assert "${{ github.ref }}" in concurrency["group"]


def test_ci_external_actions_are_full_sha_pinned_and_credentials_are_not_persisted() -> None:
    workflow = load_workflow()
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    allowed_actions = {
        CHECKOUT_ACTION,
        SETUP_UV_ACTION,
    }

    references: list[str] = []

    for job in workflow["jobs"].values():
        for step in job["steps"]:
            reference = step.get("uses")

            if reference is None:
                continue

            references.append(reference)

            assert reference in allowed_actions
            assert re.fullmatch(
                r"[^@\s]+@[0-9a-f]{40}",
                reference,
            )

            if reference == CHECKOUT_ACTION:
                assert step["with"]["persist-credentials"] == "false"

    assert references
    assert "${{ secrets." not in text


def test_ci_jobs_are_bounded_and_use_fixed_runner_family() -> None:
    jobs = load_workflow()["jobs"]

    assert set(jobs) == {
        "quality",
        "integration",
        "image",
    }

    for job in jobs.values():
        assert job["runs-on"] == "ubuntu-24.04"
        assert int(job["timeout-minutes"]) > 0
        assert int(job["timeout-minutes"]) <= 30


def test_quality_job_enforces_lock_lint_format_and_unit_tests() -> None:
    job = load_workflow()["jobs"]["quality"]
    commands = run_commands(job)

    assert "uv lock --check" in commands
    assert "uv sync --frozen --all-extras" in commands
    assert "ruff check ." in commands
    assert "ruff format --check ." in commands
    assert 'pytest -q -m "not integration"' in commands


def test_integration_job_uses_isolated_postgres_redis_and_migrations() -> None:
    job = load_workflow()["jobs"]["integration"]

    services = job["services"]
    postgres = services["postgres"]
    redis = services["redis"]

    assert postgres["image"] == "postgres:16-alpine"
    assert postgres["ports"] == ["5432:5432"]
    assert postgres["env"]["POSTGRES_DB"] == "spike_test"

    assert redis["image"] == "redis:7-alpine"
    assert redis["ports"] == ["6379:6379"]

    environment = job["env"]

    assert environment["APP_ENV"] == "test"
    assert environment["RUN_INTEGRATION_TESTS"] == "1"
    assert environment["TEST_DATABASE_URL"].endswith("/spike_test")
    assert environment["TEST_REDIS_URL"].endswith("/15")
    assert environment["TEST_CELERY_RESULT_BACKEND"].endswith("/14")

    assert environment["STRIPE_ENABLED"] == "false"
    assert environment["GEMINI_ENABLED"] == "false"
    assert environment["S3_ENABLED"] == "false"

    commands = run_commands(job)

    assert "alembic heads" in commands
    assert "alembic upgrade head" in commands
    assert "alembic current" in commands
    assert "pytest -q tests/integration" in commands


def test_image_job_builds_and_smoke_tests_hardened_runtime() -> None:
    job = load_workflow()["jobs"]["image"]
    commands = run_commands(job)

    assert "docker compose config --quiet" in commands
    assert "docker build --tag spike-backend:ci ." in commands
    assert ".Config.User" in commands
    assert "shutil.which('uv')" in commands
    assert "shutil.which('uvx')" in commands
    assert "import fastapi, celery, weasyprint" in commands
    assert "write_pdf()" in commands
