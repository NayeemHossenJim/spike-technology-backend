from __future__ import annotations

from datetime import UTC, datetime

from app.services.processing_metrics import load_report_processing_metrics


class _FakeResult:
    def __init__(
        self,
        *,
        rows: list[tuple[object, int]] | None = None,
        scalar: int | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list[tuple[object, int]]:
        return list(self._rows)

    def scalar_one(self) -> int:
        if self._scalar is None:
            raise AssertionError("No scalar result configured.")
        return self._scalar


class _FakeSession:
    def __init__(self) -> None:
        self.results = [
            _FakeResult(
                rows=[
                    ("queued", 2),
                    ("processing", 1),
                    ("completed", 7),
                    ("failed", 3),
                ]
            ),
            _FakeResult(
                rows=[
                    ("processing_internal_error", 1),
                    ("source_state_invalid", 2),
                ]
            ),
            _FakeResult(scalar=1),
            _FakeResult(scalar=1),
        ]
        self.execute_count = 0

    async def execute(self, _statement: object) -> _FakeResult:
        result = self.results[self.execute_count]
        self.execute_count += 1
        return result


async def test_processing_metrics_snapshot_uses_durable_database_state() -> None:
    session = _FakeSession()

    snapshot = await load_report_processing_metrics(
        session=session,  # type: ignore[arg-type]
        now=datetime(2026, 8, 15, 6, 0, tzinfo=UTC),
    )

    assert session.execute_count == 4

    assert snapshot.status_counts == {
        "queued": 2,
        "processing": 1,
        "completed": 7,
        "failed": 3,
    }

    assert snapshot.failed_by_error_code == {
        "processing_internal_error": 1,
        "source_state_invalid": 2,
    }

    assert snapshot.retrying_jobs == 1
    assert snapshot.stale_leases == 1
