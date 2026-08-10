from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.business import TenantRole
from app.models.subscription import (
    EntitlementKey,
    EntitlementSource,
    SubscriptionStatus,
)
from app.models.user import Industry, UserRole


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserPageRead(BaseModel):
    items: list[AdminUserRead]
    total: int
    limit: int
    offset: int


class AdminBusinessRead(BaseModel):
    id: UUID
    name: str
    industry: Industry | None
    owner_user_id: UUID
    owner: AdminUserRead
    role_assignment_role: TenantRole
    role_assignment_is_active: bool
    created_at: datetime
    updated_at: datetime


class AdminBusinessPageRead(BaseModel):
    items: list[AdminBusinessRead]
    total: int
    limit: int
    offset: int


class AdminAccountActionReason(StrEnum):
    SECURITY_REVIEW = "security_review"
    TERMS_VIOLATION = "terms_violation"
    ABUSE_PREVENTION = "abuse_prevention"
    ADMINISTRATIVE_HOLD = "administrative_hold"
    SUPPORT_RESOLUTION = "support_resolution"


class AdminAccountActionRequest(BaseModel):
    reason_code: AdminAccountActionReason


class AdminAccountActionRead(BaseModel):
    user: AdminUserRead
    changed: bool
    sessions_revoked: int


class AdminPlanRead(BaseModel):
    id: UUID
    code: str
    name: str
    monthly_price_cents: int | None
    currency: str
    is_custom_pricing: bool


class AdminSubscriptionAccessRead(BaseModel):
    active: bool
    reason: str
    period_started_at: datetime | None
    period_ends_at: datetime | None


class AdminEffectiveEntitlementRead(BaseModel):
    key: EntitlementKey
    is_enabled: bool
    limit_value: int | None
    source: EntitlementSource


class AdminSubscriptionRead(BaseModel):
    id: UUID
    business_id: UUID
    plan: AdminPlanRead | None
    status: SubscriptionStatus

    trial_started_at: datetime | None
    trial_ends_at: datetime | None

    current_period_started_at: datetime | None
    current_period_ends_at: datetime | None

    cancel_at_period_end: bool
    canceled_at: datetime | None
    ended_at: datetime | None

    stripe_managed: bool
    last_stripe_synced_at: datetime | None

    access: AdminSubscriptionAccessRead
    entitlements: list[AdminEffectiveEntitlementRead]

    created_at: datetime
    updated_at: datetime
