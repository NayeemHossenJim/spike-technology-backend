from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import AppEnvironment, get_settings
from app.core.logging import configure_logging
from app.core.metrics import metrics_registry
from app.db.session import dispose_database
from app.services.gemini_gateway import get_gemini_gateway
from app.services.redis import close_redis

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

logger = logging.getLogger(__name__)


def _request_route_template(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)

    if isinstance(route_path, str) and route_path:
        return route_path

    return "<unmatched>"


def _request_outcome(status_code: int) -> str:
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "success"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    yield
    await get_gemini_gateway().aclose()
    await close_redis()
    await dispose_database()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.4.0",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    @app.middleware("http")
    async def add_request_context_and_security_headers(request: Request, call_next):
        started = perf_counter()
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid4().hex
        )
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_seconds = perf_counter() - started
            route = _request_route_template(request)

            metrics_registry.observe_http_request(
                method=request.method,
                route=route,
                status_code=500,
                duration_seconds=duration_seconds,
            )

            logger.error(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status_code": 500,
                    "duration_ms": int(duration_seconds * 1000),
                    "outcome": "server_error",
                    "exception_type": type(exc).__name__,
                },
            )
            raise

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.app_env is AppEnvironment.PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        duration_seconds = perf_counter() - started
        route = _request_route_template(request)

        metrics_registry.observe_http_request(
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_seconds=duration_seconds,
        )

        log_level = logging.ERROR if response.status_code >= 500 else logging.INFO
        logger.log(
            log_level,
            "http_request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": route,
                "status_code": response.status_code,
                "duration_ms": int(duration_seconds * 1000),
                "outcome": _request_outcome(response.status_code),
            },
        )

        return response

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
