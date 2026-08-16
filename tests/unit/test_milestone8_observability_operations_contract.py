from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "ops" / "prometheus" / "alerts.yml"
RUNBOOK = ROOT / "docs" / "operations_runbook.md"


def test_observability_alert_rules_cover_core_operational_failures() -> None:
    content = ALERTS.read_text(encoding="utf-8")

    required_alerts = (
        "SpikeUnhandledApplicationErrors",
        "SpikeHighHttpServerErrorRate",
        "SpikePostgresqlUnavailable",
        "SpikeRedisUnavailable",
        "SpikeProcessingMetricsUnavailable",
        "SpikeStaleProcessingLeases",
    )

    for alert in required_alerts:
        assert f"alert: {alert}" in content

    required_metrics = (
        "spike_unhandled_errors_total",
        "spike_http_requests_total",
        "spike_postgresql_available",
        "spike_redis_available",
        "spike_report_processing_metrics_available",
        "spike_report_processing_stale_leases",
    )

    for metric in required_metrics:
        assert metric in content


def test_operational_runbook_documents_detection_and_response() -> None:
    content = RUNBOOK.read_text(encoding="utf-8")

    required_sections = (
        "## Health endpoints",
        "## Metrics access",
        "## Core metrics",
        "## Structured logs",
        "## Alert response",
        "## Celery worker checks",
        "## Sensitive-data handling",
        "## Recovery and rollback",
        "## Incident closure",
    )

    for section in required_sections:
        assert section in content

    assert "X-Request-ID" in content
    assert "spike_unhandled_errors_total" in content
    assert "ops/prometheus/alerts.yml" in content
    assert "Stage 8.9" in content
