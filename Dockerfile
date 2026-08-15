FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv

FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock README.md ./
COPY app ./app

RUN uv sync --frozen --no-dev --no-editable --extra phase3


FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME="/home/app" \
    XDG_CACHE_HOME="/home/app/.cache" \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-dejavu-core \
        libharfbuzz-subset0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app \
    && mkdir -p /home/app/.cache/fontconfig \
    && chown -R app:app /home/app

COPY --from=builder /app/.venv /app/.venv

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

USER app

RUN fc-cache -f

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
