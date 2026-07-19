"""harden phase 1 signup and browser sessions

Revision ID: 0003_phase1_hardening
Revises: 0002_six_digit_otp_auth
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003_phase1_hardening"
down_revision: Union[str, Sequence[str], None] = "0002_six_digit_otp_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "job_title",
        new_column_name="job_role",
        existing_type=sa.String(length=120),
        existing_nullable=True,
    )
    op.add_column(
        "users",
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("terms_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "is_persistent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "is_persistent")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
    op.alter_column(
        "users",
        "job_role",
        new_column_name="job_title",
        existing_type=sa.String(length=120),
        existing_nullable=True,
    )
