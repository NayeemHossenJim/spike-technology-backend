from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Final

PROMETHEUS_CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"

HTTP_DURATION_BUCKETS_SECONDS: Final[tuple[float, ...]] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

REPORT_PROCESSING_STATUSES: Final[tuple[str, ...]] = (
    "queued",
    "processing",
    "completed",
    "failed",
)

_ALLOWED_HTTP_METHODS: Final[frozenset[str]] = frozenset(
    {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
        "HEAD",
    }
)


@dataclass
class _HTTPSeries:
    count: int = 0
    duration_sum_seconds: float = 0.0
    bucket_counts: list[int] = field(
        default_factory=lambda: [0] * len(HTTP_DURATION_BUCKETS_SECONDS)
    )


@dataclass(frozen=True, slots=True)
class ReportProcessingMetricsSnapshot:
    status_counts: Mapping[str, int]
    failed_by_error_code: Mapping[str, int]
    retrying_jobs: int
    stale_leases: int


@dataclass(frozen=True, slots=True)
class InfrastructureMetricsSnapshot:
    postgresql_available: bool
    postgresql_probe_duration_seconds: float
    redis_available: bool
    redis_probe_duration_seconds: float


def _normalize_method(method: str) -> str:
    normalized = method.upper()
    if normalized in _ALLOWED_HTTP_METHODS:
        return normalized
    return "OTHER"


def _status_class(status_code: int) -> str:
    group = status_code // 100
    if group in {1, 2, 3, 4, 5}:
        return f"{group}xx"
    return "other"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def render_infrastructure_metrics(
    snapshot: InfrastructureMetricsSnapshot,
) -> str:
    lines = [
        "# HELP spike_postgresql_available Whether PostgreSQL responded to the metrics probe.",
        "# TYPE spike_postgresql_available gauge",
        f"spike_postgresql_available {1 if snapshot.postgresql_available else 0}",
        (
            "# HELP spike_postgresql_probe_duration_seconds "
            "Duration of the most recent PostgreSQL metrics probe."
        ),
        "# TYPE spike_postgresql_probe_duration_seconds gauge",
        (
            "spike_postgresql_probe_duration_seconds "
            f"{_format_number(max(0.0, snapshot.postgresql_probe_duration_seconds))}"
        ),
        "# HELP spike_redis_available Whether Redis responded to the metrics probe.",
        "# TYPE spike_redis_available gauge",
        f"spike_redis_available {1 if snapshot.redis_available else 0}",
        (
            "# HELP spike_redis_probe_duration_seconds "
            "Duration of the most recent Redis metrics probe."
        ),
        "# TYPE spike_redis_probe_duration_seconds gauge",
        (
            "spike_redis_probe_duration_seconds "
            f"{_format_number(max(0.0, snapshot.redis_probe_duration_seconds))}"
        ),
    ]

    return "\n".join(lines) + "\n"


def render_report_processing_metrics(
    snapshot: ReportProcessingMetricsSnapshot | None,
) -> str:
    available = 1 if snapshot is not None else 0

    lines = [
        (
            "# HELP spike_report_processing_metrics_available "
            "Whether durable report-processing metrics were loaded successfully."
        ),
        "# TYPE spike_report_processing_metrics_available gauge",
        f"spike_report_processing_metrics_available {available}",
    ]

    if snapshot is None:
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            (
                "# HELP spike_report_processing_jobs "
                "Current report-processing jobs by durable status."
            ),
            "# TYPE spike_report_processing_jobs gauge",
        ]
    )

    for status in REPORT_PROCESSING_STATUSES:
        count = max(0, int(snapshot.status_counts.get(status, 0)))
        lines.append(f'spike_report_processing_jobs{{status="{_escape_label(status)}"}} {count}')

    lines.extend(
        [
            (
                "# HELP spike_report_processing_failed_jobs "
                "Current terminal report-processing failures by error code."
            ),
            "# TYPE spike_report_processing_failed_jobs gauge",
        ]
    )

    for error_code, raw_count in sorted(snapshot.failed_by_error_code.items()):
        count = max(0, int(raw_count))
        lines.append(
            "spike_report_processing_failed_jobs"
            f'{{error_code="{_escape_label(error_code)}"}} {count}'
        )

    lines.extend(
        [
            (
                "# HELP spike_report_processing_retrying_jobs "
                "Current queued jobs that have already started at least one attempt."
            ),
            "# TYPE spike_report_processing_retrying_jobs gauge",
            (f"spike_report_processing_retrying_jobs {max(0, int(snapshot.retrying_jobs))}"),
            (
                "# HELP spike_report_processing_stale_leases "
                "Current processing jobs whose worker lease has expired."
            ),
            "# TYPE spike_report_processing_stale_leases gauge",
            (f"spike_report_processing_stale_leases {max(0, int(snapshot.stale_leases))}"),
        ]
    )

    return "\n".join(lines) + "\n"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._http: dict[
            tuple[str, str, str],
            _HTTPSeries,
        ] = {}

    def observe_http_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        key = (
            _normalize_method(method),
            route,
            _status_class(status_code),
        )

        safe_duration = max(0.0, duration_seconds)

        with self._lock:
            series = self._http.setdefault(
                key,
                _HTTPSeries(),
            )

            series.count += 1
            series.duration_sum_seconds += safe_duration

            for index, upper_bound in enumerate(HTTP_DURATION_BUCKETS_SECONDS):
                if safe_duration <= upper_bound:
                    series.bucket_counts[index] += 1

    def reset(self) -> None:
        with self._lock:
            self._http.clear()

    def render_prometheus(self) -> str:
        with self._lock:
            snapshot = [
                (
                    key,
                    _HTTPSeries(
                        count=value.count,
                        duration_sum_seconds=value.duration_sum_seconds,
                        bucket_counts=list(value.bucket_counts),
                    ),
                )
                for key, value in self._http.items()
            ]

        snapshot.sort(key=lambda item: item[0])

        lines = [
            "# HELP spike_http_requests_total Total HTTP requests processed by the API.",
            "# TYPE spike_http_requests_total counter",
        ]

        for (method, route, status_class), series in snapshot:
            labels = (
                f'method="{_escape_label(method)}",'
                f'route="{_escape_label(route)}",'
                f'status_class="{_escape_label(status_class)}"'
            )

            lines.append(f"spike_http_requests_total{{{labels}}} {series.count}")

        lines.extend(
            [
                ("# HELP spike_http_request_duration_seconds HTTP request duration in seconds."),
                "# TYPE spike_http_request_duration_seconds histogram",
            ]
        )

        for (method, route, status_class), series in snapshot:
            labels = (
                f'method="{_escape_label(method)}",'
                f'route="{_escape_label(route)}",'
                f'status_class="{_escape_label(status_class)}"'
            )

            for upper_bound, count in zip(
                HTTP_DURATION_BUCKETS_SECONDS,
                series.bucket_counts,
                strict=True,
            ):
                lines.append(
                    "spike_http_request_duration_seconds_bucket"
                    f'{{{labels},le="{_format_number(upper_bound)}"}} '
                    f"{count}"
                )

            lines.append(
                f'spike_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {series.count}'
            )

            lines.append(
                "spike_http_request_duration_seconds_sum"
                f"{{{labels}}} "
                f"{_format_number(series.duration_sum_seconds)}"
            )

            lines.append(f"spike_http_request_duration_seconds_count{{{labels}}} {series.count}")

        return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()


__all__ = [
    "HTTP_DURATION_BUCKETS_SECONDS",
    "InfrastructureMetricsSnapshot",
    "MetricsRegistry",
    "PROMETHEUS_CONTENT_TYPE",
    "REPORT_PROCESSING_STATUSES",
    "ReportProcessingMetricsSnapshot",
    "metrics_registry",
    "render_infrastructure_metrics",
    "render_report_processing_metrics",
]
