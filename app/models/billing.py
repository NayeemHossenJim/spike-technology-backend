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
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey


class BillingCheckoutStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    COMPLETE = "complete"
    EXPIRED = "expired"


class BillingInvoiceStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    UNCOLLECTIBLE = "uncollectible"
    VOID = "void"


class StripeWebhookResult(StrEnum):
    PROCESSED = "processed"
    IGNORED = "ignored"


class BillingCheckoutSession(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "billing_checkout_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_billing_checkout_subscription_business",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_billing_checkout_business_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending', 'open', 'complete', 'expired')",
            name="ck_billing_checkout_status",
        ),
        Index(
            "uq_billing_checkout_one_open_per_business",
            "business_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'open')"),
        ),
    )

    subscription_id: UUID = Field(nullable=False, index=True)
    plan_id: UUID = Field(
        foreign_key="plans.id",
        ondelete="RESTRICT",
        nullable=False,
        index=True,
    )
    status: BillingCheckoutStatus = Field(
        default=BillingCheckoutStatus.PENDING,
        sa_column=Column(
            String(32),
            nullable=False,
            default=BillingCheckoutStatus.PENDING.value,
        ),
    )
    idempotency_key_digest: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    stripe_checkout_session_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True, unique=True),
    )
    checkout_url: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    stripe_customer_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    stripe_subscription_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )


class BillingInvoice(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "billing_invoices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_billing_invoices_subscription_business",
        ),
        CheckConstraint(
            "status IN ('draft', 'open', 'paid', 'uncollectible', 'void')",
            name="ck_billing_invoices_status",
        ),
        CheckConstraint(
            "char_length(currency) = 3",
            name="ck_billing_invoices_currency_length",
        ),
    )

    subscription_id: UUID = Field(nullable=False, index=True)
    stripe_invoice_id: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True),
    )
    stripe_customer_id: str = Field(
        sa_column=Column(String(255), nullable=False, index=True),
    )
    invoice_number: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    status: BillingInvoiceStatus = Field(
        sa_column=Column(String(32), nullable=False),
    )
    currency: str = Field(
        sa_column=Column(String(3), nullable=False),
    )
    subtotal_cents: int = Field(sa_column=Column(Integer, nullable=False))
    total_cents: int = Field(sa_column=Column(Integer, nullable=False))
    amount_due_cents: int = Field(sa_column=Column(Integer, nullable=False))
    amount_paid_cents: int = Field(sa_column=Column(Integer, nullable=False))
    amount_remaining_cents: int = Field(sa_column=Column(Integer, nullable=False))
    stripe_created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    period_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    period_ends_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    paid_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    hosted_invoice_url: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    invoice_pdf_url: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    last_stripe_event_created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_stripe_event_id: str = Field(
        sa_column=Column(String(255), nullable=False),
    )


class StripeWebhookEvent(UUIDPrimaryKey, TimestampedModel, table=True):
    __tablename__ = "stripe_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "result IN ('processed', 'ignored')",
            name="ck_stripe_webhook_events_result",
        ),
    )

    stripe_event_id: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True),
    )
    event_type: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    object_id: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
    stripe_created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    livemode: bool = Field(sa_column=Column(Boolean, nullable=False))
    result: StripeWebhookResult = Field(
        sa_column=Column(String(32), nullable=False),
    )
    processed_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
