"""add email two-step verification challenges

Revision ID: 0019_gap05_email_two_step
Revises: 0018_gap04_account_security
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_gap05_email_two_step"
down_revision: str | Sequence[str] | None = "0018_gap04_account_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_two_step_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_two_step_enabled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_users_email_two_step_state",
        "users",
        "(email_two_step_enabled AND email_two_step_enabled_at IS NOT NULL) OR "
        "(NOT email_two_step_enabled AND email_two_step_enabled_at IS NULL)",
    )

    op.create_table(
        "email_two_step_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("otp_hash", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remember_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('enable', 'disable', 'login')",
            name="ck_email_two_step_challenges_purpose",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_email_two_step_challenges_attempt_count",
        ),
        sa.CheckConstraint(
            "session_version >= 0",
            name="ck_email_two_step_challenges_session_version",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_two_step_challenges_user_id",
        "email_two_step_challenges",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_two_step_challenges_user_purpose_created",
        "email_two_step_challenges",
        ["user_id", "purpose", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_two_step_challenges_user_purpose_created",
        table_name="email_two_step_challenges",
    )
    op.drop_index(
        "ix_email_two_step_challenges_user_id",
        table_name="email_two_step_challenges",
    )
    op.drop_table("email_two_step_challenges")
    op.drop_constraint(
        "ck_users_email_two_step_state",
        "users",
        type_="check",
    )
    op.drop_column("users", "email_two_step_enabled_at")
    op.drop_column("users", "email_two_step_enabled")
