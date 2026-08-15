from __future__ import annotations

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
                "# HELP spike_http_request_duration_seconds HTTP request duration in seconds.",
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
    "MetricsRegistry",
    "PROMETHEUS_CONTENT_TYPE",
    "metrics_registry",
]
