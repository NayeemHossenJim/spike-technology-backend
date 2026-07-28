from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.subscription import (
    EntitlementKey,
    EntitlementSource,
    SubscriptionStatus,
)


class PlanEntitlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: EntitlementKey
    is_enabled: bool
    limit_value: int | None


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None
    monthly_price_cents: int | None
    currency: str
    is_custom_pricing: bool
    entitlements: list[PlanEntitlementRead]


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    plan_id: UUID | None
    status: SubscriptionStatus
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    current_period_started_at: datetime | None
    current_period_ends_at: datetime | None
    cancel_at_period_end: bool
    canceled_at: datetime | None
    ended_at: datetime | None


class EffectiveEntitlementRead(BaseModel):
    key: EntitlementKey
    is_enabled: bool
    limit_value: int | None
    source: EntitlementSource


class EntitlementSummaryRead(BaseModel):
    subscription_id: UUID
    subscription_status: SubscriptionStatus
    access_active: bool
    access_reason: str
    period_started_at: datetime | None
    period_ends_at: datetime | None
    entitlements: list[EffectiveEntitlementRead]
