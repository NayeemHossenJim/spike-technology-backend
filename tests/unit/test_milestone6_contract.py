from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.models.dashboard import (
    DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
    Dashboard,
    DashboardSnapshot,
    DashboardSnapshotSource,
    DashboardType,
)
from app.schemas.dashboard import DashboardCreate, DashboardUpdate
from app.services.tenant import tenant_select


def _named(table, constraint_type) -> set[str]:
    return {
        item.name
        for item in table.constraints
        if isinstance(item, constraint_type) and item.name is not None
    }


def _index_names(table) -> set[str]:
    return {
        item.name for item in table.indexes if isinstance(item, Index) and item.name is not None
    }


def test_dashboard_type_contract_is_closed() -> None:
    assert [item.value for item in DashboardType] == [
        "executive_summary",
        "financial_performance",
        "operational_kpi",
    ]


def test_dashboard_create_is_server_tenant_owned_and_bounded() -> None:
    payload = DashboardCreate(
        dashboard_type="executive_summary",
        title="  Executive Overview  ",
        configuration={
            "currency": "USD",
            "filters": {"period": "monthly"},
        },
    )

    assert payload.dashboard_type is DashboardType.EXECUTIVE_SUMMARY
    assert payload.title == "Executive Overview"
    assert payload.configuration["currency"] == "USD"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DashboardCreate.model_validate(
            {
                "dashboard_type": "executive_summary",
                "title": "Forbidden tenant override",
                "business_id": str(uuid4()),
            }
        )

    with pytest.raises(ValidationError):
        DashboardCreate(
            dashboard_type="executive_summary",
            title="   ",
        )

    with pytest.raises(ValidationError):
        DashboardCreate(
            dashboard_type="executive_summary",
            title="Bad number",
            configuration={"value": float("nan")},
        )


def test_dashboard_update_requires_a_real_change() -> None:
    with pytest.raises(ValidationError, match="At least one dashboard field"):
        DashboardUpdate()

    update = DashboardUpdate(title="  Updated Dashboard  ")
    assert update.title == "Updated Dashboard"


def test_dashboard_definition_metadata_enforces_tenant_invariants() -> None:
    table = Dashboard.__table__

    assert table.name == "dashboards"
    assert {
        "fk_dashboards_business_creator",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_dashboards_id_business",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_dashboards_type",
        "ck_dashboards_title",
        "ck_dashboards_configuration_object",
    } <= _named(table, CheckConstraint)
    assert {
        "ix_dashboards_business_id",
        "ix_dashboards_created_by_user_id",
        "ix_dashboards_business_updated",
    } <= _index_names(table)

    business_id = uuid4()
    statement = tenant_select(Dashboard, business_id)
    assert business_id in statement.compile().params.values()


def test_dashboard_snapshot_is_versioned_and_structurally_immutable() -> None:
    table = DashboardSnapshot.__table__

    assert "updated_at" not in table.columns
    assert {
        "fk_dashboard_snapshots_dashboard_business",
        "fk_dashboard_snapshots_business_creator",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_dashboard_snapshots_id_business",
        "uq_dashboard_snapshots_dashboard_version",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_dashboard_snapshots_version",
        "ck_dashboard_snapshots_schema_version",
        "ck_dashboard_snapshots_payload_object",
        "ck_dashboard_snapshots_content_hash",
    } <= _named(table, CheckConstraint)

    snapshot = DashboardSnapshot(
        business_id=uuid4(),
        dashboard_id=uuid4(),
        version=1,
        payload={"metrics": []},
        content_hash="a" * 64,
        created_by_user_id=uuid4(),
    )
    assert snapshot.schema_version == DASHBOARD_SNAPSHOT_SCHEMA_VERSION
    assert snapshot.created_at is not None


def test_dashboard_snapshot_source_pins_exact_processing_artifacts() -> None:
    table = DashboardSnapshotSource.__table__

    assert "updated_at" not in table.columns
    assert {
        "fk_dashboard_snapshot_sources_snapshot_business",
        "fk_dashboard_snapshot_sources_processing_business",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_dashboard_snapshot_sources_position",
        "uq_dashboard_snapshot_sources_processing_job",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_dashboard_snapshot_sources_position",
        "ck_dashboard_snapshot_sources_artifact_versions",
    } <= _named(table, CheckConstraint)

    processing_fk = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_dashboard_snapshot_sources_processing_business"
    )
    assert [column.name for column in processing_fk.columns] == [
        "processing_job_id",
        "business_id",
    ]
    assert [element.target_fullname for element in processing_fk.elements] == [
        "report_processing_jobs.id",
        "report_processing_jobs.business_id",
    ]


def test_milestone6_migration_is_linear_and_contains_dashboard_contract() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (
        project_root / "alembic" / "versions" / "0012_m6_dashboard_foundation.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0012_m6_dashboard_foundation"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0011_m5_ai_execution"' in migration
    assert '"dashboards"' in migration
    assert '"dashboard_snapshots"' in migration
    assert '"dashboard_snapshot_sources"' in migration
    assert "fk_dashboard_snapshots_dashboard_business" in migration
    assert "fk_dashboard_snapshot_sources_processing_business" in migration
    assert "uq_dashboard_snapshots_dashboard_version" in migration
    assert "ck_dashboard_snapshots_content_hash" in migration
