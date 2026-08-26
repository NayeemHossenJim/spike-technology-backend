"""add report-grounded dashboard insight and AI analysis contracts

Revision ID: 0016_gap02_grounded_dashboards
Revises: 0015_m7_ai_credit_adjustments
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_gap02_grounded_dashboards"
down_revision: str | Sequence[str] | None = "0015_m7_ai_credit_adjustments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dashboards",
        sa.Column(
            "insight_configuration",
            postgresql.JSONB(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_dashboards_insight_configuration_object",
        "dashboards",
        "insight_configuration IS NULL "
        "OR jsonb_typeof(insight_configuration) = 'object'",
    )

    op.add_column(
        "ai_conversations",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default="chat",
        ),
    )
    op.add_column(
        "ai_conversations",
        sa.Column(
            "dashboard_snapshot_id",
            sa.Uuid(),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_ai_conversations_purpose",
        "ai_conversations",
        "purpose IN ('chat', 'dashboard_analysis')",
    )
    op.create_check_constraint(
        "ck_ai_conversations_analysis_snapshot",
        "ai_conversations",
        "purpose <> 'dashboard_analysis' OR dashboard_snapshot_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_ai_conversations_dashboard_snapshot_business",
        "ai_conversations",
        "dashboard_snapshots",
        ["dashboard_snapshot_id", "business_id"],
        ["id", "business_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_ai_conversations_dashboard_snapshot_id",
        "ai_conversations",
        ["dashboard_snapshot_id"],
    )
    op.create_index(
        "ux_ai_conversations_dashboard_analysis",
        "ai_conversations",
        ["business_id", "dashboard_snapshot_id"],
        unique=True,
        postgresql_where=sa.text("purpose = 'dashboard_analysis'"),
    )
    op.alter_column(
        "ai_conversations",
        "purpose",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_ai_conversations_dashboard_analysis",
        table_name="ai_conversations",
    )
    op.drop_index(
        "ix_ai_conversations_dashboard_snapshot_id",
        table_name="ai_conversations",
    )
    op.drop_constraint(
        "fk_ai_conversations_dashboard_snapshot_business",
        "ai_conversations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_ai_conversations_analysis_snapshot",
        "ai_conversations",
        type_="check",
    )
    op.drop_constraint(
        "ck_ai_conversations_purpose",
        "ai_conversations",
        type_="check",
    )
    op.drop_column("ai_conversations", "dashboard_snapshot_id")
    op.drop_column("ai_conversations", "purpose")

    op.drop_constraint(
        "ck_dashboards_insight_configuration_object",
        "dashboards",
        type_="check",
    )
    op.drop_column("dashboards", "insight_configuration")
