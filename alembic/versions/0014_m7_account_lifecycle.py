"""add account lifecycle session generation

Revision ID: 0014_m7_account_lifecycle
Revises: 0013_m7_admin_audit
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_m7_account_lifecycle"
down_revision: str | Sequence[str] | None = "0013_m7_admin_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_session_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_users_auth_session_version",
        "users",
        "auth_session_version >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_users_auth_session_version",
        "users",
        type_="check",
    )
    op.drop_column(
        "users",
        "auth_session_version",
    )
