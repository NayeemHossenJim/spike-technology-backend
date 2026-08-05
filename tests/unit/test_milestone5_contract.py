from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.models.ai import AICreditAccount, AICreditLedgerEntry, AICreditLedgerStatus


def _named(table, constraint_type):
    return {
        item.name
        for item in table.constraints
        if isinstance(item, constraint_type) and item.name is not None
    }


def _index_names(table) -> set[str]:
    return {item.name for item in table.indexes if isinstance(item, Index)}


def test_ai_credit_ledger_status_contract_is_stable() -> None:
    assert [item.value for item in AICreditLedgerStatus] == [
        "reserved",
        "consumed",
        "released",
    ]


def test_ai_credit_account_schema_contract_is_registered() -> None:
    table = AICreditAccount.__table__

    assert table.name == "ai_credit_accounts"
    assert {
        "business_id",
        "id",
        "created_at",
        "updated_at",
        "subscription_id",
        "entitlement_key",
        "period_started_at",
        "period_ends_at",
        "limit_value",
        "reserved_count",
        "consumed_count",
    } == set(table.columns.keys())
    assert {
        "fk_ai_credit_accounts_subscription_business",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_ai_credit_accounts_id_business_subscription",
        "uq_ai_credit_accounts_subscription_period",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_ai_credit_accounts_entitlement_key",
        "ck_ai_credit_accounts_period",
        "ck_ai_credit_accounts_nonnegative_limit",
        "ck_ai_credit_accounts_balance",
    } <= _named(table, CheckConstraint)
    assert {
        "ix_ai_credit_accounts_business_id",
        "ix_ai_credit_accounts_subscription_id",
        "ix_ai_credit_accounts_business_period",
    } <= _index_names(table)


def test_ai_credit_ledger_entry_schema_contract_is_registered() -> None:
    table = AICreditLedgerEntry.__table__

    assert table.name == "ai_credit_ledger_entries"
    assert {
        "business_id",
        "id",
        "created_at",
        "updated_at",
        "account_id",
        "subscription_id",
        "entitlement_key",
        "idempotency_key_digest",
        "status",
        "quantity",
        "reserved_at",
        "consumed_at",
        "released_at",
        "release_reason",
    } == set(table.columns.keys())
    assert {
        "fk_ai_credit_ledger_entries_account_business_subscription",
    } <= _named(table, ForeignKeyConstraint)
    assert {
        "uq_ai_credit_ledger_entries_id_business",
        "uq_ai_credit_ledger_entries_business_idempotency",
    } <= _named(table, UniqueConstraint)
    assert {
        "ck_ai_credit_ledger_entries_entitlement_key",
        "ck_ai_credit_ledger_entries_status",
        "ck_ai_credit_ledger_entries_unit_quantity",
        "ck_ai_credit_ledger_entries_idempotency_digest",
        "ck_ai_credit_ledger_entries_status_fields",
        "ck_ai_credit_ledger_entries_timestamps",
    } <= _named(table, CheckConstraint)
    assert {
        "ix_ai_credit_ledger_entries_business_id",
        "ix_ai_credit_ledger_entries_account_id",
        "ix_ai_credit_ledger_entries_subscription_id",
        "ix_ai_credit_ledger_entries_account_status",
        "ix_ai_credit_ledger_entries_business_status_created",
    } <= _index_names(table)


def test_milestone5_migration_contract_is_linear_and_complete() -> None:
    project_root = Path(__file__).resolve().parents[2]
    migration = (project_root / "alembic" / "versions" / "0009_m5_ai_credit_ledger.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0009_m5_ai_credit_ledger"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0008_m4_data_processing"' in migration
    assert '"ai_credit_accounts"' in migration
    assert '"ai_credit_ledger_entries"' in migration
    assert "uq_ai_credit_accounts_subscription_period" in migration
    assert "uq_ai_credit_ledger_entries_business_idempotency" in migration
    assert "ck_ai_credit_accounts_balance" in migration
    assert "ck_ai_credit_ledger_entries_status_fields" in migration
