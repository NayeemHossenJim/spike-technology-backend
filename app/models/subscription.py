from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey

TRIAL_DAYS = 14
AI_FULL_RESPONSES_PER_PERIOD = 15
PREMIUM_DASHBOARD_LIMIT = 20


class SeededPlanCode(StrEnum):
    PREMIUM = "premium"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"
    PAUSED = "paused"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    CANCELED = "canceled"


class EntitlementKey(StrEnum):
    AI_FULL_RESPONSES = "ai_full_responses"
    DASHBOARDS = "dashboards"


class EntitlementSource(StrEnum):
    TRIAL = "trial"
    PLAN = "plan"
    OVERRIDE = "override"


class Plan(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint(
            "monthly_price_cents IS NULL OR monthly_price_cents >= 0",
            name="ck_plans_nonnegative_monthly_price",
        ),
        CheckConstraint("char_length(currency) = 3", name="ck_plans_currency_length"),
    )

    code: str = Field(
        sa_column=Column(String(64), nullable=False, unique=True, index=True),
    )
    name: str = Field(sa_column=Column(String(120), nullable=False))
    description: str | None = Field(
        default=None,
        sa_column=Column(String(500), nullable=True),
    )
    monthly_price_cents: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    currency: str = Field(
        default="USD",
        sa_column=Column(String(3), nullable=False, default="USD"),
    )
    is_custom_pricing: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    is_public: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    sort_order: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )


class PlanEntitlement(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "plan_entitlements"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "key",
            name="uq_plan_entitlements_plan_key",
        ),
        CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_plan_entitlements_nonnegative_limit",
        ),
        CheckConstraint(
            "key IN ('ai_full_responses', 'dashboards')",
            name="ck_plan_entitlements_key",
        ),
    )

    plan_id: UUID = Field(
        foreign_key="plans.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
    )
    key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    is_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    limit_value: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )


class Subscription(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_subscriptions_id_business",
        ),
        CheckConstraint(
            "status IN ("
            "'trialing', 'active', 'past_due', 'unpaid', 'paused', 'incomplete', "
            "'incomplete_expired', 'canceled'"
            ")",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "trial_ends_at IS NULL OR trial_started_at IS NOT NULL",
            name="ck_subscriptions_trial_dates",
        ),
        CheckConstraint(
            "current_period_ends_at IS NULL OR current_period_started_at IS NOT NULL",
            name="ck_subscriptions_period_dates",
        ),
        Index(
            "uq_subscriptions_one_current_per_business",
            "business_id",
            unique=True,
            postgresql_where=text(
                "status IN ('trialing', 'active', 'past_due', 'unpaid', 'paused', 'incomplete')"
            ),
        ),
    )

    plan_id: UUID | None = Field(
        default=None,
        foreign_key="plans.id",
        ondelete="RESTRICT",
        nullable=True,
        index=True,
    )
    status: SubscriptionStatus = Field(
        sa_column=Column(String(32), nullable=False),
    )
    trial_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    trial_ends_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    current_period_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    current_period_ends_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    cancel_at_period_end: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
    canceled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    stripe_customer_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True, index=True),
    )
    stripe_subscription_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True, unique=True),
    )


class SubscriptionEntitlement(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "subscription_entitlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_subscription_entitlements_subscription_business",
        ),
        UniqueConstraint(
            "subscription_id",
            "key",
            name="uq_subscription_entitlements_subscription_key",
        ),
        CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_subscription_entitlements_nonnegative_limit",
        ),
        CheckConstraint(
            "key IN ('ai_full_responses', 'dashboards')",
            name="ck_subscription_entitlements_key",
        ),
        CheckConstraint(
            "source IN ('trial', 'plan', 'override')",
            name="ck_subscription_entitlements_source",
        ),
    )

    subscription_id: UUID = Field(nullable=False, index=True)
    key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    source: EntitlementSource = Field(
        sa_column=Column(String(32), nullable=False),
    )
    is_enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, default=True),
    )
    limit_value: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
