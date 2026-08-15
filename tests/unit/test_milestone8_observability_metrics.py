from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from pytest import MonkeyPatch, fixture

from app.api.v1 import health as health_api
from app.core.config import get_settings
from app.core.metrics import (
    MetricsRegistry,
    ReportProcessingMetricsSnapshot,
    metrics_registry,
    render_report_processing_metrics,
)
from app.main import create_app


@fixture(autouse=True)
def stub_processing_metrics_loader(monkeypatch: MonkeyPatch) -> None:
    async def load_stub(
        *,
        session: object,
    ) -> ReportProcessingMetricsSnapshot:
        del session

        return ReportProcessingMetricsSnapshot(
            status_counts={},
            failed_by_error_code={},
            retrying_jobs=0,
            stale_leases=0,
        )

    monkeypatch.setattr(
        health_api,
        "load_report_processing_metrics",
        load_stub,
    )


def test_http_metrics_aggregate_by_low_cardinality_labels() -> None:
    registry = MetricsRegistry()

    registry.observe_http_request(
        method="GET",
        route="/items/{item_id}",
        status_code=200,
        duration_seconds=0.04,
    )
    registry.observe_http_request(
        method="GET",
        route="/items/{item_id}",
        status_code=204,
        duration_seconds=0.20,
    )

    rendered = registry.render_prometheus()

    assert (
        'spike_http_requests_total{method="GET",'
        'route="/items/{item_id}",status_class="2xx"} 2' in rendered
    )

    assert (
        "spike_http_request_duration_seconds_count"
        '{method="GET",route="/items/{item_id}",'
        'status_class="2xx"} 2' in rendered
    )


def test_http_duration_histogram_is_cumulative() -> None:
    registry = MetricsRegistry()

    registry.observe_http_request(
        method="POST",
        route="/jobs",
        status_code=503,
        duration_seconds=0.2,
    )

    rendered = registry.render_prometheus()

    assert (
        "spike_http_request_duration_seconds_bucket"
        '{method="POST",route="/jobs",status_class="5xx",'
        'le="0.1"} 0' in rendered
    )

    assert (
        "spike_http_request_duration_seconds_bucket"
        '{method="POST",route="/jobs",status_class="5xx",'
        'le="0.25"} 1' in rendered
    )

    assert (
        "spike_http_request_duration_seconds_bucket"
        '{method="POST",route="/jobs",status_class="5xx",'
        'le="+Inf"} 1' in rendered
    )


def test_unknown_http_methods_are_collapsed() -> None:
    registry = MetricsRegistry()

    registry.observe_http_request(
        method="CUSTOM-USER-CONTROLLED",
        route="<unmatched>",
        status_code=404,
        duration_seconds=0.01,
    )

    rendered = registry.render_prometheus()

    assert 'method="OTHER"' in rendered
    assert "CUSTOM-USER-CONTROLLED" not in rendered


def test_processing_metrics_render_durable_job_state() -> None:
    snapshot = ReportProcessingMetricsSnapshot(
        status_counts={
            "queued": 3,
            "processing": 2,
            "completed": 10,
            "failed": 4,
        },
        failed_by_error_code={
            "processing_internal_error": 1,
            "source_state_invalid": 3,
        },
        retrying_jobs=2,
        stale_leases=1,
    )

    rendered = render_report_processing_metrics(snapshot)

    assert "spike_report_processing_metrics_available 1" in rendered
    assert 'spike_report_processing_jobs{status="queued"} 3' in rendered
    assert 'spike_report_processing_jobs{status="processing"} 2' in rendered
    assert 'spike_report_processing_jobs{status="completed"} 10' in rendered
    assert 'spike_report_processing_jobs{status="failed"} 4' in rendered

    assert (
        'spike_report_processing_failed_jobs{error_code="processing_internal_error"} 1' in rendered
    )
    assert 'spike_report_processing_failed_jobs{error_code="source_state_invalid"} 3' in rendered

    assert "spike_report_processing_retrying_jobs 2" in rendered
    assert "spike_report_processing_stale_leases 1" in rendered


def test_processing_metrics_failure_does_not_fabricate_zero_job_state() -> None:
    rendered = render_report_processing_metrics(None)

    assert "spike_report_processing_metrics_available 0" in rendered
    assert "spike_report_processing_jobs{" not in rendered
    assert "spike_report_processing_failed_jobs{" not in rendered


async def test_metrics_endpoint_uses_route_templates_and_omits_query_data() -> None:
    metrics_registry.reset()

    application = create_app()
    transport = ASGITransport(app=application)

    sensitive_path = "private-user-segment"
    sensitive_query = "must-not-be-exported"

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            live = await client.get(f"/api/v1/health/live?token={sensitive_query}")
            missing = await client.get(f"/{sensitive_path}?secret={sensitive_query}")
            metrics = await client.get("/api/v1/health/metrics")

        assert live.status_code == 200
        assert missing.status_code == 404
        assert metrics.status_code == 200

        rendered = metrics.text

        assert 'route="/health/live"' in rendered
        assert 'route="<unmatched>"' in rendered

        assert sensitive_path not in rendered
        assert sensitive_query not in rendered

        assert "spike_http_requests_total" in rendered
        assert "spike_http_request_duration_seconds" in rendered
        assert "spike_report_processing_metrics_available" in rendered
    finally:
        metrics_registry.reset()


async def test_configured_metrics_token_hides_endpoint_without_credentials() -> None:
    metrics_registry.reset()

    application = create_app()
    protected_settings = get_settings().model_copy(
        update={"metrics_bearer_token": SecretStr("metrics-test-secret-" + ("x" * 32))}
    )

    application.dependency_overrides[get_settings] = lambda: protected_settings

    transport = ASGITransport(app=application)

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            missing = await client.get("/api/v1/health/metrics")
            wrong = await client.get(
                "/api/v1/health/metrics",
                headers={"Authorization": ("Bearer definitely-wrong-token")},
            )

        assert missing.status_code == 404
        assert wrong.status_code == 404

        assert missing.json() == {"detail": "Not found."}
        assert wrong.json() == {"detail": "Not found."}
    finally:
        application.dependency_overrides.clear()
        metrics_registry.reset()


async def test_configured_metrics_token_allows_valid_bearer_token() -> None:
    metrics_registry.reset()

    token = "metrics-test-secret-" + ("y" * 32)

    application = create_app()
    protected_settings = get_settings().model_copy(
        update={"metrics_bearer_token": SecretStr(token)}
    )

    application.dependency_overrides[get_settings] = lambda: protected_settings

    transport = ASGITransport(app=application)

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/v1/health/metrics",
                headers={"Authorization": (f"Bearer {token}")},
            )

        assert response.status_code == 200
        assert "spike_http_requests_total" in response.text
        assert "spike_report_processing_metrics_available" in response.text
        assert token not in response.text
    finally:
        application.dependency_overrides.clear()
        metrics_registry.reset()
