"""preserve report upload request order

Revision ID: 0007_m3_upload_positions
Revises: 0006_m3_secure_uploads
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0007_m3_upload_positions"
down_revision: str | Sequence[str] | None = "0006_m3_secure_uploads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "report_uploads",
        sa.Column("batch_position", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ranked_uploads AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY batch_id
                    ORDER BY created_at, id
                ) - 1 AS batch_position
            FROM report_uploads
        )
        UPDATE report_uploads AS upload
        SET batch_position = ranked_uploads.batch_position
        FROM ranked_uploads
        WHERE upload.id = ranked_uploads.id
        """
    )
    op.alter_column(
        "report_uploads",
        "batch_position",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_report_uploads_batch_position",
        "report_uploads",
        "batch_position BETWEEN 0 AND 4",
    )
    op.create_unique_constraint(
        "uq_report_uploads_batch_position",
        "report_uploads",
        ["batch_id", "batch_position"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_report_uploads_batch_position",
        "report_uploads",
        type_="unique",
    )
    op.drop_constraint(
        "ck_report_uploads_batch_position",
        "report_uploads",
        type_="check",
    )
    op.drop_column("report_uploads", "batch_position")
