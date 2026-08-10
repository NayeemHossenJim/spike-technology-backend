from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models.ai import (
    AICreditAdjustmentLedgerEntry,
    AICreditAdjustmentReason,
    AICreditLedgerStatus,
)


def _named(table, constraint_type):
    return {
        item.name
        for item in table.constraints
        if isinstance(item, constraint_type) and item.name is not None
    }


def _indexes(table):
    return {item.name for item in table.indexes if isinstance(item, Index)}


def test_reservation_ledger_status_machine_remains_unchanged() -> None:
    assert [item.value for item in AICreditLedgerStatus] == [
        "reserved",
        "consumed",
        "released",
    ]


def test_admin_credit_adjustment_reason_contract_is_closed() -> None:
    assert [item.value for item in AICreditAdjustmentReason] == [
        "support_credit",
        "service_recovery",
        "billing_correction",
        "administrative_correction",
        "fraud_reversal",
    ]


def test_admin_credit_adjustment_ledger_schema_is_registered() -> None:
    table = AICreditAdjustmentLedgerEntry.__table__

    assert table.name == "ai_credit_adjustment_ledger_entries"

    assert {
        "business_id",
        "id",
        "created_at",
        "updated_at",
        "account_id",
        "subscription_id",
        "entitlement_key",
        "actor_user_id",
        "idempotency_key_digest",
        "delta",
        "reason_code",
        "request_id",
        "base_limit_value",
        "effective_limit_before",
        "effective_limit_after",
        "reserved_count",
        "consumed_count",
        "adjusted_at",
    } == set(table.columns.keys())

    assert {
        "fk_ai_credit_adjustments_account_business_subscription",
    } <= _named(table, ForeignKeyConstraint)

    assert {
        "uq_ai_credit_adjustments_business_idempotency",
    } <= _named(table, UniqueConstraint)

    assert {
        "ck_ai_credit_adjustments_entitlement_key",
        "ck_ai_credit_adjustments_delta",
        "ck_ai_credit_adjustments_reason",
        "ck_ai_credit_adjustments_idempotency_digest",
        "ck_ai_credit_adjustments_nonnegative_limits",
        "ck_ai_credit_adjustments_limit_math",
        "ck_ai_credit_adjustments_usage_floor",
    } <= _named(table, CheckConstraint)

    assert {
        "ix_ai_credit_adjustment_ledger_entries_business_id",
        "ix_ai_credit_adjustment_ledger_entries_account_id",
        "ix_ai_credit_adjustment_ledger_entries_subscription_id",
        "ix_ai_credit_adjustments_account_adjusted",
        "ix_ai_credit_adjustments_business_adjusted",
        "ix_ai_credit_adjustments_actor_adjusted",
    } == _indexes(table)


def test_stage5_adjustment_migration_is_linear_and_append_only() -> None:
    project_root = Path(__file__).resolve().parents[2]

    migration = (
        project_root / "alembic" / "versions" / "0015_m7_ai_credit_adjustments.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0015_m7_ai_credit_adjustments"' in migration
    assert ('down_revision: str | Sequence[str] | None = "0014_m7_account_lifecycle"') in migration

    assert "ai_credit_adjustment_ledger_entries" in migration
    assert "trg_ai_credit_adjustments_immutable" in migration
    assert "prevent_ai_credit_adjustment_mutation" in migration
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "ERRCODE = '55000'" in migration
