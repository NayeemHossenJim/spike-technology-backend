from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey, utc_now

DASHBOARD_TITLE_MAX_LENGTH = 160
DASHBOARD_SNAPSHOT_SCHEMA_VERSION = 1


class DashboardType(StrEnum):
    EXECUTIVE_SUMMARY = "executive_summary"
    FINANCIAL_PERFORMANCE = "financial_performance"
    OPERATIONAL_KPI = "operational_kpi"


class Dashboard(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    """Tenant-owned dashboard definition.

    Dashboard limits are deliberately enforced through the subscription
    entitlement service rather than duplicated in this table.
    """

    __tablename__ = "dashboards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_dashboards_business_creator",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_dashboards_id_business",
        ),
        CheckConstraint(
            "dashboard_type IN ('executive_summary', 'financial_performance', 'operational_kpi')",
            name="ck_dashboards_type",
        ),
        CheckConstraint(
            f"char_length(trim(title)) BETWEEN 1 AND {DASHBOARD_TITLE_MAX_LENGTH}",
            name="ck_dashboards_title",
        ),
        CheckConstraint(
            "jsonb_typeof(configuration) = 'object'",
            name="ck_dashboards_configuration_object",
        ),
        Index(
            "ix_dashboards_business_updated",
            "business_id",
            "updated_at",
            "id",
        ),
    )

    created_by_user_id: UUID = Field(nullable=False, index=True)
    dashboard_type: DashboardType = Field(
        sa_column=Column(String(64), nullable=False),
    )
    title: str = Field(
        sa_column=Column(
            String(DASHBOARD_TITLE_MAX_LENGTH),
            nullable=False,
        ),
    )
    configuration: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(
            JSONB,
            nullable=False,
            default=dict,
        ),
    )


class DashboardSnapshot(
    TenantOwnedModel,
    UUIDPrimaryKey,
    table=True,
):
    """Immutable, versioned materialization of one dashboard.

    Service code creates new versions instead of updating existing snapshots.
    The payload is the canonical server-side representation later used by
    dashboard reads and PDF rendering.
    """

    __tablename__ = "dashboard_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dashboard_id", "business_id"],
            ["dashboards.id", "dashboards.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshots_dashboard_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshots_business_creator",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_dashboard_snapshots_id_business",
        ),
        UniqueConstraint(
            "dashboard_id",
            "version",
            name="uq_dashboard_snapshots_dashboard_version",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_dashboard_snapshots_version",
        ),
        CheckConstraint(
            f"schema_version = {DASHBOARD_SNAPSHOT_SCHEMA_VERSION}",
            name="ck_dashboard_snapshots_schema_version",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_dashboard_snapshots_payload_object",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dashboard_snapshots_content_hash",
        ),
        Index(
            "ix_dashboard_snapshots_business_dashboard_version",
            "business_id",
            "dashboard_id",
            "version",
        ),
    )

    dashboard_id: UUID = Field(nullable=False, index=True)
    version: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    schema_version: int = Field(
        default=DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
        sa_column=Column(
            Integer,
            nullable=False,
            default=DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
        ),
    )
    payload: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(
            JSONB,
            nullable=False,
            default=dict,
        ),
    )
    content_hash: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    created_by_user_id: UUID = Field(nullable=False, index=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class DashboardSnapshotSource(
    TenantOwnedModel,
    UUIDPrimaryKey,
    table=True,
):
    """Exact M4 artifact provenance for an immutable dashboard snapshot.

    Stage 2 must only create these rows from completed report-processing jobs
    and must copy the exact immutable S3 artifact version identifiers/ETags
    stored on those jobs.
    """

    __tablename__ = "dashboard_snapshot_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "business_id"],
            ["dashboard_snapshots.id", "dashboard_snapshots.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshot_sources_snapshot_business",
        ),
        ForeignKeyConstraint(
            ["processing_job_id", "business_id"],
            ["report_processing_jobs.id", "report_processing_jobs.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshot_sources_processing_business",
        ),
        UniqueConstraint(
            "snapshot_id",
            "source_position",
            name="uq_dashboard_snapshot_sources_position",
        ),
        UniqueConstraint(
            "snapshot_id",
            "processing_job_id",
            name="uq_dashboard_snapshot_sources_processing_job",
        ),
        CheckConstraint(
            "source_position >= 0",
            name="ck_dashboard_snapshot_sources_position",
        ),
        CheckConstraint(
            "char_length(normalized_storage_version_id) > 0 "
            "AND char_length(normalized_etag) > 0 "
            "AND char_length(profile_storage_version_id) > 0 "
            "AND char_length(profile_etag) > 0",
            name="ck_dashboard_snapshot_sources_artifact_versions",
        ),
        Index(
            "ix_dashboard_snapshot_sources_snapshot_position",
            "snapshot_id",
            "source_position",
        ),
    )

    snapshot_id: UUID = Field(nullable=False, index=True)
    processing_job_id: UUID = Field(nullable=False, index=True)
    source_position: int = Field(
        sa_column=Column(Integer, nullable=False),
    )
    normalized_storage_version_id: str = Field(
        sa_column=Column(String(1024), nullable=False),
    )
    normalized_etag: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    profile_storage_version_id: str = Field(
        sa_column=Column(String(1024), nullable=False),
    )
    profile_etag: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
