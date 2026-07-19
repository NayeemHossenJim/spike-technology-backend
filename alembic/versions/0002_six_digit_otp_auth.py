"""replace auth links with six-digit OTPs

Revision ID: 0002_six_digit_otp_auth
Revises: 0001_phase1_foundation
Create Date: 2026-07-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_six_digit_otp_auth"
down_revision: Union[str, Sequence[str], None] = "0001_phase1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Opaque link tokens cannot be interpreted as six-digit OTPs. Invalidate them so an
    # existing unverified user receives a fresh OTP on the next resend request.
    op.execute(
        "UPDATE email_verification_tokens SET used_at = CURRENT_TIMESTAMP "
        "WHERE used_at IS NULL"
    )
    op.execute(
        "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP "
        "WHERE used_at IS NULL"
    )

    op.alter_column(
        "email_verification_tokens",
        "token_hash",
        new_column_name="otp_hash",
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
    op.add_column(
        "email_verification_tokens",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.alter_column(
        "password_reset_tokens",
        "token_hash",
        new_column_name="otp_hash",
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_nullable=False,
    )
    op.add_column(
        "password_reset_tokens",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "password_reset_tokens",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Mark all OTPs unusable and reduce hashes to 64 characters before restoring the
    # original opaque-token schema. The downgraded records are intentionally invalid.
    op.execute(
        "UPDATE email_verification_tokens "
        "SET used_at = CURRENT_TIMESTAMP, otp_hash = md5(otp_hash) || md5(otp_hash)"
    )
    op.execute(
        "UPDATE password_reset_tokens "
        "SET used_at = CURRENT_TIMESTAMP, otp_hash = md5(otp_hash) || md5(otp_hash)"
    )

    op.drop_column("password_reset_tokens", "verified_at")
    op.drop_column("password_reset_tokens", "attempt_count")
    op.alter_column(
        "password_reset_tokens",
        "otp_hash",
        new_column_name="token_hash",
        existing_type=sa.String(length=512),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    op.drop_column("email_verification_tokens", "attempt_count")
    op.alter_column(
        "email_verification_tokens",
        "otp_hash",
        new_column_name="token_hash",
        existing_type=sa.String(length=512),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
