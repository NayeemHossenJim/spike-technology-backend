from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.main import create_app
from app.schemas.dashboard import DashboardSnapshotCreate
from app.services.dashboard_snapshots import (
    DASHBOARD_MAX_SNAPSHOT_SOURCES,
    dashboard_snapshot_content_hash,
)


def test_snapshot_content_hash_is_canonical() -> None:
    first = {
        "format": "spike.dashboard-snapshot",
        "dashboard": {
            "configuration": {
                "currency": "USD",
                "period": "monthly",
            },
            "title": "Executive",
        },
        "sources": [
            {
                "processing_job_id": "job-1",
                "record_count": 2,
            }
        ],
    }
    second = {
        "sources": [
            {
                "record_count": 2,
                "processing_job_id": "job-1",
            }
        ],
        "dashboard": {
            "title": "Executive",
            "configuration": {
                "period": "monthly",
                "currency": "USD",
            },
        },
        "format": "spike.dashboard-snapshot",
    }

    assert dashboard_snapshot_content_hash(first) == (dashboard_snapshot_content_hash(second))


def test_snapshot_create_requires_unique_bounded_sources() -> None:
    source_id = uuid4()

    with pytest.raises(ValidationError, match="must be unique"):
        DashboardSnapshotCreate(
            source_processing_job_ids=[
                source_id,
                source_id,
            ]
        )

    with pytest.raises(ValidationError):
        DashboardSnapshotCreate(source_processing_job_ids=[])

    with pytest.raises(ValidationError):
        DashboardSnapshotCreate(
            source_processing_job_ids=[uuid4() for _ in range(DASHBOARD_MAX_SNAPSHOT_SOURCES + 1)]
        )


def test_snapshot_openapi_contract_is_tenant_derived() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    collection = "/api/v1/dashboards/{dashboard_id}/snapshots"
    latest = "/api/v1/dashboards/{dashboard_id}/snapshots/latest"

    assert collection in paths
    assert latest in paths
    assert {"get", "post"} <= set(paths[collection])
    assert "get" in paths[latest]

    properties = schema["components"]["schemas"]["DashboardSnapshotCreate"]["properties"]

    assert set(properties) == {
        "source_processing_job_ids",
    }
    assert "business_id" not in properties
    assert "created_by_user_id" not in properties
