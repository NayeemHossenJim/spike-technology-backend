"""add durable tenant-owned report processing jobs

Revision ID: 0008_m4_data_processing
Revises: 0007_m3_upload_positions
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0008_m4_data_processing"
down_revision: str | Sequence[str] | None = "0007_m3_upload_positions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_report_uploads_id_business",
        "report_uploads",
        ["id", "business_id"],
    )

    op.create_table(
        "report_processing_jobs",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_upload_id", sa.Uuid(), nullable=False),
        sa.Column("source_storage_version_id", sa.String(length=1024), nullable=False),
        sa.Column("source_etag", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "dispatch_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("result_storage_bucket", sa.String(length=63), nullable=True),
        sa.Column("normalized_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("normalized_storage_version_id", sa.String(length=1024), nullable=True),
        sa.Column("normalized_etag", sa.String(length=255), nullable=True),
        sa.Column("normalized_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("profile_storage_key", sa.String(length=1024), nullable=True),
        sa.Column("profile_storage_version_id", sa.String(length=1024), nullable=True),
        sa.Column("profile_etag", sa.String(length=255), nullable=True),
        sa.Column("record_count", sa.BigInteger(), nullable=True),
        sa.Column("sheet_count", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_report_processing_jobs_status",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 10 "
            "AND attempt_count BETWEEN 0 AND max_attempts "
            "AND ((attempt_count = 0 AND started_at IS NULL AND error_code IS NULL) "
            "OR (attempt_count > 0 AND started_at IS NOT NULL))",
            name="ck_report_processing_jobs_attempts",
        ),
        sa.CheckConstraint(
            "dispatch_attempt_count >= 0 AND ("
            "(dispatch_attempt_count = 0 AND last_dispatched_at IS NULL) OR "
            "(dispatch_attempt_count > 0 AND last_dispatched_at IS NOT NULL)"
            ")",
            name="ck_report_processing_jobs_dispatch",
        ),
        sa.CheckConstraint(
            "(status = 'queued' "
            "AND available_at IS NOT NULL "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'processing' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'completed' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND error_code IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' "
            "AND available_at IS NULL "
            "AND attempt_count > 0 "
            "AND lease_token IS NULL "
            "AND lease_expires_at IS NULL "
            "AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL)",
            name="ck_report_processing_jobs_status_fields",
        ),
        sa.CheckConstraint(
            "(status = 'completed' "
            "AND result_storage_bucket IS NOT NULL "
            "AND normalized_storage_key IS NOT NULL "
            "AND normalized_storage_version_id IS NOT NULL "
            "AND normalized_etag IS NOT NULL "
            "AND normalized_size_bytes IS NOT NULL "
            "AND profile_storage_key IS NOT NULL "
            "AND profile_storage_version_id IS NOT NULL "
            "AND profile_etag IS NOT NULL "
            "AND record_count IS NOT NULL "
            "AND sheet_count IS NOT NULL) OR "
            "(status <> 'completed' "
            "AND result_storage_bucket IS NULL "
            "AND normalized_storage_key IS NULL "
            "AND normalized_storage_version_id IS NULL "
            "AND normalized_etag IS NULL "
            "AND normalized_size_bytes IS NULL "
            "AND profile_storage_key IS NULL "
            "AND profile_storage_version_id IS NULL "
            "AND profile_etag IS NULL "
            "AND record_count IS NULL "
            "AND sheet_count IS NULL)",
            name="ck_report_processing_jobs_output_fields",
        ),
        sa.CheckConstraint(
            "char_length(source_storage_version_id) > 0 "
            "AND char_length(source_etag) > 0",
            name="ck_report_processing_jobs_source",
        ),
        sa.CheckConstraint(
            "(result_storage_bucket IS NULL OR "
            "char_length(result_storage_bucket) BETWEEN 3 AND 63) "
            "AND (normalized_storage_key IS NULL OR "
            "char_length(normalized_storage_key) > 0) "
            "AND (normalized_storage_version_id IS NULL OR "
            "char_length(normalized_storage_version_id) > 0) "
            "AND (normalized_etag IS NULL OR char_length(normalized_etag) > 0) "
            "AND (profile_storage_key IS NULL OR char_length(profile_storage_key) > 0) "
            "AND (profile_storage_version_id IS NULL OR "
            "char_length(profile_storage_version_id) > 0) "
            "AND (profile_etag IS NULL OR char_length(profile_etag) > 0) "
            "AND (normalized_size_bytes IS NULL OR normalized_size_bytes > 0) "
            "AND (record_count IS NULL OR record_count >= 0) "
            "AND (sheet_count IS NULL OR sheet_count > 0)",
            name="ck_report_processing_jobs_output_values",
        ),
        sa.CheckConstraint(
            "(available_at IS NULL OR available_at >= created_at) "
            "AND (last_dispatched_at IS NULL OR last_dispatched_at >= created_at) "
            "AND (started_at IS NULL OR started_at >= created_at) "
            "AND (finished_at IS NULL OR finished_at >= started_at) "
            "AND (lease_expires_at IS NULL OR lease_expires_at > started_at)",
            name="ck_report_processing_jobs_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["report_upload_id", "business_id"],
            ["report_uploads.id", "report_uploads.business_id"],
            ondelete="CASCADE",
            name="fk_report_processing_jobs_upload_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_upload_id",
            name="uq_report_processing_jobs_report_upload",
        ),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_report_processing_jobs_id_business",
        ),
        sa.UniqueConstraint(
            "normalized_storage_key",
            name="uq_report_processing_jobs_normalized_key",
        ),
        sa.UniqueConstraint(
            "profile_storage_key",
            name="uq_report_processing_jobs_profile_key",
        ),
    )
    op.create_index(
        "ix_report_processing_jobs_business_id",
        "report_processing_jobs",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_report_processing_jobs_dispatchable",
        "report_processing_jobs",
        ["available_at", "last_dispatched_at"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "ix_report_processing_jobs_stale_lease",
        "report_processing_jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_processing_jobs_stale_lease",
        table_name="report_processing_jobs",
    )
    op.drop_index(
        "ix_report_processing_jobs_dispatchable",
        table_name="report_processing_jobs",
    )
    op.drop_index(
        "ix_report_processing_jobs_business_id",
        table_name="report_processing_jobs",
    )
    op.drop_table("report_processing_jobs")
    op.drop_constraint(
        "uq_report_uploads_id_business",
        "report_uploads",
        type_="unique",
    )
