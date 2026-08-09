"""add tenant-owned dashboard foundation

Revision ID: 0012_m6_dashboard_foundation
Revises: 0011_m5_ai_execution
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_m6_dashboard_foundation"
down_revision: str | Sequence[str] | None = "0011_m5_ai_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("dashboard_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "dashboard_type IN ('executive_summary', 'financial_performance', 'operational_kpi')",
            name="ck_dashboards_type",
        ),
        sa.CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name="ck_dashboards_title",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(configuration) = 'object'",
            name="ck_dashboards_configuration_object",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_dashboards_business_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_dashboards_id_business",
        ),
    )
    op.create_index(
        "ix_dashboards_business_id",
        "dashboards",
        ["business_id"],
    )
    op.create_index(
        "ix_dashboards_created_by_user_id",
        "dashboards",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_dashboards_business_updated",
        "dashboards",
        ["business_id", "updated_at", "id"],
    )

    op.create_table(
        "dashboard_snapshots",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dashboard_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_dashboard_snapshots_version",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_dashboard_snapshots_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_dashboard_snapshots_payload_object",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_dashboard_snapshots_content_hash",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id", "business_id"],
            ["dashboards.id", "dashboards.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshots_dashboard_business",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "created_by_user_id"],
            ["role_assignments.business_id", "role_assignments.user_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshots_business_creator",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_dashboard_snapshots_id_business",
        ),
        sa.UniqueConstraint(
            "dashboard_id",
            "version",
            name="uq_dashboard_snapshots_dashboard_version",
        ),
    )
    op.create_index(
        "ix_dashboard_snapshots_business_id",
        "dashboard_snapshots",
        ["business_id"],
    )
    op.create_index(
        "ix_dashboard_snapshots_dashboard_id",
        "dashboard_snapshots",
        ["dashboard_id"],
    )
    op.create_index(
        "ix_dashboard_snapshots_created_by_user_id",
        "dashboard_snapshots",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_dashboard_snapshots_business_dashboard_version",
        "dashboard_snapshots",
        ["business_id", "dashboard_id", "version"],
    )

    op.create_table(
        "dashboard_snapshot_sources",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("processing_job_id", sa.Uuid(), nullable=False),
        sa.Column("source_position", sa.Integer(), nullable=False),
        sa.Column(
            "normalized_storage_version_id",
            sa.String(length=1024),
            nullable=False,
        ),
        sa.Column(
            "normalized_etag",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "profile_storage_version_id",
            sa.String(length=1024),
            nullable=False,
        ),
        sa.Column(
            "profile_etag",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_position >= 0",
            name="ck_dashboard_snapshot_sources_position",
        ),
        sa.CheckConstraint(
            "char_length(normalized_storage_version_id) > 0 "
            "AND char_length(normalized_etag) > 0 "
            "AND char_length(profile_storage_version_id) > 0 "
            "AND char_length(profile_etag) > 0",
            name="ck_dashboard_snapshot_sources_artifact_versions",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "business_id"],
            ["dashboard_snapshots.id", "dashboard_snapshots.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshot_sources_snapshot_business",
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id", "business_id"],
            ["report_processing_jobs.id", "report_processing_jobs.business_id"],
            ondelete="CASCADE",
            name="fk_dashboard_snapshot_sources_processing_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_position",
            name="uq_dashboard_snapshot_sources_position",
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "processing_job_id",
            name="uq_dashboard_snapshot_sources_processing_job",
        ),
    )
    op.create_index(
        "ix_dashboard_snapshot_sources_business_id",
        "dashboard_snapshot_sources",
        ["business_id"],
    )
    op.create_index(
        "ix_dashboard_snapshot_sources_snapshot_id",
        "dashboard_snapshot_sources",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_dashboard_snapshot_sources_processing_job_id",
        "dashboard_snapshot_sources",
        ["processing_job_id"],
    )
    op.create_index(
        "ix_dashboard_snapshot_sources_snapshot_position",
        "dashboard_snapshot_sources",
        ["snapshot_id", "source_position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dashboard_snapshot_sources_snapshot_position",
        table_name="dashboard_snapshot_sources",
    )
    op.drop_index(
        "ix_dashboard_snapshot_sources_processing_job_id",
        table_name="dashboard_snapshot_sources",
    )
    op.drop_index(
        "ix_dashboard_snapshot_sources_snapshot_id",
        table_name="dashboard_snapshot_sources",
    )
    op.drop_index(
        "ix_dashboard_snapshot_sources_business_id",
        table_name="dashboard_snapshot_sources",
    )
    op.drop_table("dashboard_snapshot_sources")

    op.drop_index(
        "ix_dashboard_snapshots_business_dashboard_version",
        table_name="dashboard_snapshots",
    )
    op.drop_index(
        "ix_dashboard_snapshots_created_by_user_id",
        table_name="dashboard_snapshots",
    )
    op.drop_index(
        "ix_dashboard_snapshots_dashboard_id",
        table_name="dashboard_snapshots",
    )
    op.drop_index(
        "ix_dashboard_snapshots_business_id",
        table_name="dashboard_snapshots",
    )
    op.drop_table("dashboard_snapshots")

    op.drop_index(
        "ix_dashboards_business_updated",
        table_name="dashboards",
    )
    op.drop_index(
        "ix_dashboards_created_by_user_id",
        table_name="dashboards",
    )
    op.drop_index(
        "ix_dashboards_business_id",
        table_name="dashboards",
    )
    op.drop_table("dashboards")
