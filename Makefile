.PHONY: install lint test test-integration migrate revision run worker infra-up infra-down

install:
	uv sync --extra dev

lint:
	uv run ruff check .

test:
	uv run pytest tests/unit

test-integration:
	RUN_INTEGRATION_TESTS=1 TEST_DATABASE_URL=postgresql+asyncpg://spike:spike_local_password@localhost:5432/spike_test uv run pytest tests/integration

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(message)"

run:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	uv run celery -A app.workers.celery_app:celery_app worker --loglevel=INFO

infra-up:
	docker compose up -d postgres redis

infra-down:
	docker compose down
