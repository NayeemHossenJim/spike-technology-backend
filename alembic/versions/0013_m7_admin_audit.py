"""add immutable admin audit events

Revision ID: 0013_m7_admin_audit
Revises: 0012_m6_dashboard_foundation
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_m7_admin_audit"
down_revision: str | Sequence[str] | None = "0012_m6_dashboard_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_role IN ('super_admin', 'customer_service')",
            name="ck_admin_audit_events_actor_role",
        ),
        sa.CheckConstraint(
            "action ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_admin_audit_events_action",
        ),
        sa.CheckConstraint(
            "target_type ~ '^[a-z][a-z0-9_.:-]{0,63}$'",
            name="ck_admin_audit_events_target_type",
        ),
        sa.CheckConstraint(
            "request_id IS NULL OR request_id ~ '^[A-Za-z0-9._:-]{1,128}$'",
            name="ck_admin_audit_events_request_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_admin_audit_events_metadata_object",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_admin_audit_events_actor_user_id",
        "admin_audit_events",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_admin_audit_events_target_id",
        "admin_audit_events",
        ["target_id"],
    )
    op.create_index(
        "ix_admin_audit_events_business_id",
        "admin_audit_events",
        ["business_id"],
    )
    op.create_index(
        "ix_admin_audit_events_actor_created",
        "admin_audit_events",
        ["actor_user_id", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_audit_events_business_created",
        "admin_audit_events",
        ["business_id", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_audit_events_action_created",
        "admin_audit_events",
        ["action", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_audit_events_target_created",
        "admin_audit_events",
        ["target_type", "target_id", "created_at", "id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_admin_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'admin_audit_events are append-only'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_admin_audit_events_append_only
        BEFORE UPDATE OR DELETE ON admin_audit_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_admin_audit_event_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_admin_audit_events_append_only
        ON admin_audit_events;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_admin_audit_event_mutation();")

    op.drop_index(
        "ix_admin_audit_events_target_created",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_action_created",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_business_created",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_actor_created",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_business_id",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_target_id",
        table_name="admin_audit_events",
    )
    op.drop_index(
        "ix_admin_audit_events_actor_user_id",
        table_name="admin_audit_events",
    )

    op.drop_table("admin_audit_events")
