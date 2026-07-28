from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.billing import (
    BillingCheckoutStatus,
    BillingInvoiceStatus,
    StripeWebhookResult,
)
from app.models.subscription import SeededPlanCode, SubscriptionStatus


class CheckoutSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: SeededPlanCode


class CheckoutSessionRead(BaseModel):
    id: UUID
    subscription_id: UUID
    plan_code: SeededPlanCode
    status: BillingCheckoutStatus
    checkout_url: str | None
    expires_at: datetime | None


class BillingInvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subscription_id: UUID
    invoice_number: str | None
    status: BillingInvoiceStatus
    currency: str
    subtotal_cents: int
    total_cents: int
    amount_due_cents: int
    amount_paid_cents: int
    amount_remaining_cents: int
    stripe_created_at: datetime
    period_started_at: datetime | None
    period_ends_at: datetime | None
    paid_at: datetime | None
    hosted_invoice_url: str | None
    invoice_pdf_url: str | None


class BillingHistoryRead(BaseModel):
    items: list[BillingInvoiceRead]
    total: int
    limit: int
    offset: int


class SubscriptionLifecycleRead(BaseModel):
    id: UUID
    plan_id: UUID | None
    status: SubscriptionStatus
    cancel_at_period_end: bool
    current_period_ends_at: datetime | None


class StripeWebhookRead(BaseModel):
    received: bool = True
    duplicate: bool
    result: StripeWebhookResult
