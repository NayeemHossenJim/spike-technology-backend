"""add self-service profile fields

Revision ID: 0017_gap03_self_service_profile
Revises: 0016_gap02_grounded_dashboards
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_gap03_self_service_profile"
down_revision: str | Sequence[str] | None = "0016_gap02_grounded_dashboards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("username", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_number", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("street_address", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("city", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("state", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("postal_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("bio", sa.String(length=200), nullable=True),
    )

    op.create_check_constraint(
        "ck_users_profile_username",
        "users",
        "username IS NULL OR char_length(trim(username)) BETWEEN 1 AND 80",
    )
    op.create_check_constraint(
        "ck_users_profile_phone_number",
        "users",
        "phone_number IS NULL OR char_length(trim(phone_number)) BETWEEN 7 AND 32",
    )
    op.create_check_constraint(
        "ck_users_profile_street_address",
        "users",
        "street_address IS NULL OR char_length(trim(street_address)) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_users_profile_city",
        "users",
        "city IS NULL OR char_length(trim(city)) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        "ck_users_profile_state",
        "users",
        "state IS NULL OR char_length(trim(state)) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        "ck_users_profile_postal_code",
        "users",
        "postal_code IS NULL OR char_length(trim(postal_code)) BETWEEN 1 AND 32",
    )
    op.create_check_constraint(
        "ck_users_profile_bio",
        "users",
        "bio IS NULL OR char_length(trim(bio)) BETWEEN 1 AND 200",
    )


def downgrade() -> None:
    for constraint_name in (
        "ck_users_profile_bio",
        "ck_users_profile_postal_code",
        "ck_users_profile_state",
        "ck_users_profile_city",
        "ck_users_profile_street_address",
        "ck_users_profile_phone_number",
        "ck_users_profile_username",
    ):
        op.drop_constraint(
            constraint_name,
            "users",
            type_="check",
        )

    for column_name in (
        "bio",
        "postal_code",
        "state",
        "city",
        "street_address",
        "phone_number",
        "username",
    ):
        op.drop_column("users", column_name)
