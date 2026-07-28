"""add business, subscription, entitlement, and tenant foundations

Revision ID: 0004_m1_foundation
Revises: 0003_phase1_hardening
Create Date: 2026-07-26
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_m1_foundation"
down_revision: str | Sequence[str] | None = "0003_phase1_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREMIUM_PLAN_ID = UUID("10000000-0000-4000-8000-000000000001")
PRO_PLAN_ID = UUID("10000000-0000-4000-8000-000000000002")
ENTERPRISE_PLAN_ID = UUID("10000000-0000-4000-8000-000000000003")


def upgrade() -> None:
    op.create_table(
        "role_assignments",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("role = 'owner'", name="ck_role_assignments_v1_role"),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "user_id",
            name="uq_role_assignments_business_user",
        ),
    )
    op.create_index(
        "ix_role_assignments_business_id",
        "role_assignments",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_role_assignments_user_id",
        "role_assignments",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("monthly_price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column(
            "is_custom_pricing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "monthly_price_cents IS NULL OR monthly_price_cents >= 0",
            name="ck_plans_nonnegative_monthly_price",
        ),
        sa.CheckConstraint(
            "char_length(currency) = 3",
            name="ck_plans_currency_length",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)

    op.create_table(
        "plan_entitlements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_plan_entitlements_nonnegative_limit",
        ),
        sa.CheckConstraint(
            "key IN ('ai_full_responses', 'dashboards')",
            name="ck_plan_entitlements_key",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "key",
            name="uq_plan_entitlements_plan_key",
        ),
    )
    op.create_index(
        "ix_plan_entitlements_plan_id",
        "plan_entitlements",
        ["plan_id"],
        unique=False,
    )

    op.create_table(
        "subscriptions",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ("
            "'trialing', 'active', 'past_due', 'unpaid', 'paused', 'incomplete', "
            "'incomplete_expired', 'canceled'"
            ")",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint(
            "trial_ends_at IS NULL OR trial_started_at IS NOT NULL",
            name="ck_subscriptions_trial_dates",
        ),
        sa.CheckConstraint(
            "current_period_ends_at IS NULL OR current_period_started_at IS NOT NULL",
            name="ck_subscriptions_period_dates",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "business_id",
            name="uq_subscriptions_id_business",
        ),
        sa.UniqueConstraint("stripe_subscription_id"),
    )
    op.create_index(
        "ix_subscriptions_business_id",
        "subscriptions",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_plan_id",
        "subscriptions",
        ["plan_id"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_stripe_customer_id",
        "subscriptions",
        ["stripe_customer_id"],
        unique=False,
    )
    op.create_index(
        "uq_subscriptions_one_current_per_business",
        "subscriptions",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ("
            "'trialing', 'active', 'past_due', 'unpaid', 'paused', 'incomplete'"
            ")"
        ),
    )

    op.create_table(
        "subscription_entitlements",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_subscription_entitlements_nonnegative_limit",
        ),
        sa.CheckConstraint(
            "key IN ('ai_full_responses', 'dashboards')",
            name="ck_subscription_entitlements_key",
        ),
        sa.CheckConstraint(
            "source IN ('trial', 'plan', 'override')",
            name="ck_subscription_entitlements_source",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_subscription_entitlements_subscription_business",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "key",
            name="uq_subscription_entitlements_subscription_key",
        ),
    )
    op.create_index(
        "ix_subscription_entitlements_business_id",
        "subscription_entitlements",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_subscription_entitlements_subscription_id",
        "subscription_entitlements",
        ["subscription_id"],
        unique=False,
    )

    seeded_at = datetime.now(UTC)
    plans = sa.table(
        "plans",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("monthly_price_cents", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("is_custom_pricing", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("is_public", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        plans,
        [
            {
                "id": PREMIUM_PLAN_ID,
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "code": "premium",
                "name": "Premium",
                "description": None,
                "monthly_price_cents": 5999,
                "currency": "USD",
                "is_custom_pricing": False,
                "is_active": True,
                "is_public": True,
                "sort_order": 10,
            },
            {
                "id": PRO_PLAN_ID,
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "code": "pro",
                "name": "Pro Plan",
                "description": None,
                "monthly_price_cents": 9999,
                "currency": "USD",
                "is_custom_pricing": False,
                "is_active": True,
                "is_public": True,
                "sort_order": 20,
            },
            {
                "id": ENTERPRISE_PLAN_ID,
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "code": "enterprise",
                "name": "Enterprise",
                "description": None,
                "monthly_price_cents": None,
                "currency": "USD",
                "is_custom_pricing": True,
                "is_active": True,
                "is_public": True,
                "sort_order": 30,
            },
        ],
    )

    plan_entitlements = sa.table(
        "plan_entitlements",
        sa.column("id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("plan_id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("limit_value", sa.Integer()),
    )
    op.bulk_insert(
        plan_entitlements,
        [
            {
                "id": UUID("20000000-0000-4000-8000-000000000001"),
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "plan_id": PREMIUM_PLAN_ID,
                "key": "ai_full_responses",
                "is_enabled": True,
                "limit_value": 15,
            },
            {
                "id": UUID("20000000-0000-4000-8000-000000000002"),
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "plan_id": PREMIUM_PLAN_ID,
                "key": "dashboards",
                "is_enabled": True,
                "limit_value": 20,
            },
            {
                "id": UUID("20000000-0000-4000-8000-000000000003"),
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "plan_id": PRO_PLAN_ID,
                "key": "ai_full_responses",
                "is_enabled": True,
                "limit_value": 15,
            },
            {
                "id": UUID("20000000-0000-4000-8000-000000000004"),
                "created_at": seeded_at,
                "updated_at": seeded_at,
                "plan_id": ENTERPRISE_PLAN_ID,
                "key": "ai_full_responses",
                "is_enabled": True,
                "limit_value": 15,
            },
        ],
    )

    # Phase 1 had no public business-creation endpoint, but preserve any manually
    # inserted legacy business by creating its required owner assignment.
    op.execute(
        sa.text(
            """
            INSERT INTO role_assignments (
                id, created_at, updated_at, business_id, user_id, role, is_active
            )
            SELECT
                b.id, b.created_at, b.updated_at, b.id, b.owner_user_id, 'owner', true
            FROM businesses AS b
            ON CONFLICT (user_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscription_entitlements_subscription_id",
        table_name="subscription_entitlements",
    )
    op.drop_index(
        "ix_subscription_entitlements_business_id",
        table_name="subscription_entitlements",
    )
    op.drop_table("subscription_entitlements")

    op.drop_index(
        "uq_subscriptions_one_current_per_business",
        table_name="subscriptions",
        postgresql_where=sa.text(
            "status IN ("
            "'trialing', 'active', 'past_due', 'unpaid', 'paused', 'incomplete'"
            ")"
        ),
    )
    op.drop_index("ix_subscriptions_stripe_customer_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_business_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_plan_entitlements_plan_id", table_name="plan_entitlements")
    op.drop_table("plan_entitlements")
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_table("plans")

    op.drop_index("ix_role_assignments_user_id", table_name="role_assignments")
    op.drop_index("ix_role_assignments_business_id", table_name="role_assignments")
    op.drop_table("role_assignments")
