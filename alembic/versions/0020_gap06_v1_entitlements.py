"""align approved v1 plan and trial entitlements

Revision ID: 0020_gap06_v1_entitlements
Revises: 0019_gap05_email_two_step
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_gap06_v1_entitlements"
down_revision: str | Sequence[str] | None = "0019_gap05_email_two_step"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The approved v1 contract is: trial 15 AI responses / 1 dashboard;
    # Premium 250 / 20; Pro 2,000 / unlimited; Enterprise unlimited / unlimited.
    # Effective entitlements merge plan_entitlements dynamically, so only
    # plan_entitlements plus trial-source subscription rows need reconciliation.
    op.execute(
        sa.text(
            """
            UPDATE plan_entitlements AS entitlement
            SET limit_value = CASE plan.code
                WHEN 'premium' THEN 250
                WHEN 'pro' THEN 2000
                WHEN 'enterprise' THEN NULL
            END,
                updated_at = timezone('utc', now())
            FROM plans AS plan
            WHERE entitlement.plan_id = plan.id
              AND entitlement.key = 'ai_full_responses'
              AND plan.code IN ('premium', 'pro', 'enterprise')
            """
        )
    )
    op.execute(
        sa.text(
            r"""
            INSERT INTO plan_entitlements (
                id, created_at, updated_at, plan_id, key, is_enabled, limit_value
            )
            SELECT
                md5(plan.id::text || '\:dashboards')::uuid,
                timezone('utc', now()),
                timezone('utc', now()),
                plan.id,
                'dashboards',
                true,
                NULL
            FROM plans AS plan
            WHERE plan.code IN ('pro', 'enterprise')
            ON CONFLICT (plan_id, key) DO UPDATE
            SET is_enabled = EXCLUDED.is_enabled,
                limit_value = EXCLUDED.limit_value,
                updated_at = EXCLUDED.updated_at
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_entitlements AS entitlement
            SET is_enabled = true,
                limit_value = 1,
                updated_at = timezone('utc', now())
            FROM subscriptions AS subscription
            WHERE entitlement.subscription_id = subscription.id
              AND entitlement.business_id = subscription.business_id
              AND entitlement.key = 'dashboards'
              AND entitlement.source = 'trial'
              AND subscription.status = 'trialing'
            """
        )
    )
    op.execute(
        sa.text(
            r"""
            INSERT INTO subscription_entitlements (
                id, created_at, updated_at, business_id, subscription_id,
                key, source, is_enabled, limit_value
            )
            SELECT
                md5(subscription.id::text || '\:trial\:dashboards')::uuid,
                timezone('utc', now()),
                timezone('utc', now()),
                subscription.business_id,
                subscription.id,
                'dashboards',
                'trial',
                true,
                1
            FROM subscriptions AS subscription
            WHERE subscription.status = 'trialing'
            ON CONFLICT (subscription_id, key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM subscription_entitlements WHERE key = 'dashboards' AND source = 'trial'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM plan_entitlements WHERE key = 'dashboards' AND plan_id IN ("
            "SELECT id FROM plans WHERE code IN ('pro', 'enterprise'))"
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE plan_entitlements AS entitlement
            SET limit_value = 15, updated_at = timezone('utc', now())
            FROM plans AS plan
            WHERE entitlement.plan_id = plan.id
              AND entitlement.key = 'ai_full_responses'
              AND plan.code IN ('premium', 'pro', 'enterprise')
            """
        )
    )
