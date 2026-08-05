from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field

from app.models.base import TenantOwnedModel, TimestampedModel, UUIDPrimaryKey
from app.models.subscription import EntitlementKey


class AICreditLedgerStatus(StrEnum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class AICreditAccount(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_credit_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["subscription_id", "business_id"],
            ["subscriptions.id", "subscriptions.business_id"],
            ondelete="CASCADE",
            name="fk_ai_credit_accounts_subscription_business",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            "subscription_id",
            name="uq_ai_credit_accounts_id_business_subscription",
        ),
        UniqueConstraint(
            "business_id",
            "subscription_id",
            "entitlement_key",
            "period_started_at",
            "period_ends_at",
            name="uq_ai_credit_accounts_subscription_period",
        ),
        CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_accounts_entitlement_key",
        ),
        CheckConstraint(
            "period_ends_at > period_started_at",
            name="ck_ai_credit_accounts_period",
        ),
        CheckConstraint(
            "limit_value IS NULL OR limit_value >= 0",
            name="ck_ai_credit_accounts_nonnegative_limit",
        ),
        CheckConstraint(
            "reserved_count >= 0 AND consumed_count >= 0 "
            "AND (limit_value IS NULL OR reserved_count + consumed_count <= limit_value)",
            name="ck_ai_credit_accounts_balance",
        ),
        Index(
            "ix_ai_credit_accounts_business_period",
            "business_id",
            "period_started_at",
            "period_ends_at",
        ),
    )

    subscription_id: UUID = Field(nullable=False, index=True)
    entitlement_key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    period_started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    period_ends_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    limit_value: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    reserved_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )
    consumed_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, default=0),
    )


class AICreditLedgerEntry(
    TenantOwnedModel,
    UUIDPrimaryKey,
    TimestampedModel,
    table=True,
):
    __tablename__ = "ai_credit_ledger_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "business_id", "subscription_id"],
            [
                "ai_credit_accounts.id",
                "ai_credit_accounts.business_id",
                "ai_credit_accounts.subscription_id",
            ],
            ondelete="CASCADE",
            name="fk_ai_credit_ledger_entries_account_business_subscription",
        ),
        UniqueConstraint(
            "id",
            "business_id",
            name="uq_ai_credit_ledger_entries_id_business",
        ),
        UniqueConstraint(
            "business_id",
            "idempotency_key_digest",
            name="uq_ai_credit_ledger_entries_business_idempotency",
        ),
        CheckConstraint(
            "entitlement_key = 'ai_full_responses'",
            name="ck_ai_credit_ledger_entries_entitlement_key",
        ),
        CheckConstraint(
            "status IN ('reserved', 'consumed', 'released')",
            name="ck_ai_credit_ledger_entries_status",
        ),
        CheckConstraint(
            "quantity = 1",
            name="ck_ai_credit_ledger_entries_unit_quantity",
        ),
        CheckConstraint(
            "char_length(idempotency_key_digest) = 64",
            name="ck_ai_credit_ledger_entries_idempotency_digest",
        ),
        CheckConstraint(
            "(status = 'reserved' "
            "AND consumed_at IS NULL "
            "AND released_at IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'consumed' "
            "AND consumed_at IS NOT NULL "
            "AND released_at IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'released' "
            "AND consumed_at IS NULL "
            "AND released_at IS NOT NULL "
            "AND release_reason IS NOT NULL "
            "AND char_length(trim(release_reason)) BETWEEN 1 AND 255)",
            name="ck_ai_credit_ledger_entries_status_fields",
        ),
        CheckConstraint(
            "(consumed_at IS NULL OR consumed_at >= reserved_at) "
            "AND (released_at IS NULL OR released_at >= reserved_at)",
            name="ck_ai_credit_ledger_entries_timestamps",
        ),
        Index(
            "ix_ai_credit_ledger_entries_account_status",
            "account_id",
            "status",
        ),
        Index(
            "ix_ai_credit_ledger_entries_business_status_created",
            "business_id",
            "status",
            "created_at",
        ),
    )

    account_id: UUID = Field(nullable=False, index=True)
    subscription_id: UUID = Field(nullable=False, index=True)
    entitlement_key: EntitlementKey = Field(
        sa_column=Column(String(64), nullable=False),
    )
    idempotency_key_digest: str = Field(
        sa_column=Column(String(64), nullable=False),
    )
    status: AICreditLedgerStatus = Field(
        default=AICreditLedgerStatus.RESERVED,
        sa_column=Column(
            String(32),
            nullable=False,
            default=AICreditLedgerStatus.RESERVED,
        ),
    )
    quantity: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1),
    )
    reserved_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    consumed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    released_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    release_reason: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
