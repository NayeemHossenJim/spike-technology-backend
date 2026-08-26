from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.ai import AIConversationPurpose, AIMessageStatus
from app.models.dashboard import (
    DASHBOARD_INSIGHT_CALCULATION_VERSION,
    DASHBOARD_INSIGHT_CONFIGURATION_VERSION,
    DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
    DASHBOARD_TITLE_MAX_LENGTH,
    DashboardGoal,
    DashboardInsightField,
    DashboardType,
)

MAX_DASHBOARD_CONFIGURATION_BYTES = 16 * 1024
MAX_DASHBOARD_CONFIGURATION_DEPTH = 6
MAX_DASHBOARD_CONFIGURATION_ITEMS = 512
MAX_DASHBOARD_CONFIGURATION_KEY_LENGTH = 120
MAX_DASHBOARD_INSIGHT_DATASETS = 20
MAX_DASHBOARD_INSIGHT_GOALS = 3
MAX_DASHBOARD_SOURCE_COLUMN_LENGTH = 300

_NUMERIC_INSIGHT_FIELDS = frozenset(
    {
        DashboardInsightField.EXPENSES,
        DashboardInsightField.REVENUE,
        DashboardInsightField.NET_CASH,
        DashboardInsightField.GROSS_MARGIN,
        DashboardInsightField.SPEND,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.BUDGET,
        DashboardInsightField.PROGRESS,
    }
)

DASHBOARD_INSIGHT_FIELDS: dict[DashboardType, tuple[DashboardInsightField, ...]] = {
    DashboardType.EXECUTIVE_SUMMARY: (
        DashboardInsightField.PERIOD,
        DashboardInsightField.LOCATION,
        DashboardInsightField.EXPENSES,
        DashboardInsightField.REVENUE,
        DashboardInsightField.NET_CASH,
        DashboardInsightField.CATEGORY,
        DashboardInsightField.GROSS_MARGIN,
    ),
    DashboardType.FINANCIAL_PERFORMANCE: (
        DashboardInsightField.PERIOD,
        DashboardInsightField.LOCATION,
        DashboardInsightField.REVENUE,
        DashboardInsightField.SPEND,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.BUDGET,
        DashboardInsightField.CATEGORY,
    ),
    DashboardType.OPERATIONAL_KPI: (
        DashboardInsightField.PERIOD,
        DashboardInsightField.DEPARTMENT,
        DashboardInsightField.VENDOR,
        DashboardInsightField.EXPENSES,
        DashboardInsightField.STATUS_TITLE,
        DashboardInsightField.STATUS_DETAIL,
        DashboardInsightField.STATUS,
        DashboardInsightField.PROGRESS,
    ),
}

DASHBOARD_REQUIRED_INSIGHT_FIELDS: dict[
    DashboardType,
    frozenset[DashboardInsightField],
] = {
    DashboardType.EXECUTIVE_SUMMARY: frozenset(
        DASHBOARD_INSIGHT_FIELDS[DashboardType.EXECUTIVE_SUMMARY]
    ),
    DashboardType.FINANCIAL_PERFORMANCE: frozenset(
        DASHBOARD_INSIGHT_FIELDS[DashboardType.FINANCIAL_PERFORMANCE]
    ),
    DashboardType.OPERATIONAL_KPI: frozenset(
        {
            DashboardInsightField.PERIOD,
            DashboardInsightField.DEPARTMENT,
            DashboardInsightField.VENDOR,
            DashboardInsightField.EXPENSES,
        }
    ),
}

DASHBOARD_REQUIRED_DATASET_FIELD_GROUPS: dict[
    DashboardType,
    tuple[frozenset[DashboardInsightField], ...],
] = {
    DashboardType.EXECUTIVE_SUMMARY: (
        frozenset(
            {
                DashboardInsightField.PERIOD,
                DashboardInsightField.EXPENSES,
                DashboardInsightField.CATEGORY,
                DashboardInsightField.LOCATION,
            }
        ),
    ),
    DashboardType.FINANCIAL_PERFORMANCE: (
        frozenset(
            {
                DashboardInsightField.PERIOD,
                DashboardInsightField.SPEND,
                DashboardInsightField.CATEGORY,
                DashboardInsightField.LOCATION,
            }
        ),
    ),
    DashboardType.OPERATIONAL_KPI: (
        frozenset(
            {
                DashboardInsightField.PERIOD,
                DashboardInsightField.EXPENSES,
                DashboardInsightField.DEPARTMENT,
                DashboardInsightField.VENDOR,
            }
        ),
    ),
}

DASHBOARD_OUTFLOW_DIMENSIONS: dict[
    DashboardType,
    tuple[DashboardInsightField, tuple[DashboardInsightField, ...]],
] = {
    DashboardType.EXECUTIVE_SUMMARY: (
        DashboardInsightField.EXPENSES,
        (DashboardInsightField.CATEGORY, DashboardInsightField.LOCATION),
    ),
    DashboardType.FINANCIAL_PERFORMANCE: (
        DashboardInsightField.SPEND,
        (DashboardInsightField.CATEGORY, DashboardInsightField.LOCATION),
    ),
    DashboardType.OPERATIONAL_KPI: (
        DashboardInsightField.EXPENSES,
        (DashboardInsightField.DEPARTMENT, DashboardInsightField.VENDOR),
    ),
}


def _validate_json_value(
    value: Any,
    *,
    depth: int = 0,
    item_counter: list[int] | None = None,
) -> None:
    if item_counter is None:
        item_counter = [0]

    if depth > MAX_DASHBOARD_CONFIGURATION_DEPTH:
        raise ValueError("Dashboard configuration is too deeply nested.")

    if isinstance(value, dict):
        for key, nested in value.items():
            item_counter[0] += 1
            if item_counter[0] > MAX_DASHBOARD_CONFIGURATION_ITEMS:
                raise ValueError("Dashboard configuration contains too many items.")
            if not isinstance(key, str):
                raise ValueError("Dashboard configuration keys must be strings.")
            if not 1 <= len(key) <= MAX_DASHBOARD_CONFIGURATION_KEY_LENGTH:
                raise ValueError("Dashboard configuration contains an invalid key.")
            _validate_json_value(
                nested,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    if isinstance(value, list):
        for nested in value:
            item_counter[0] += 1
            if item_counter[0] > MAX_DASHBOARD_CONFIGURATION_ITEMS:
                raise ValueError("Dashboard configuration contains too many items.")
            _validate_json_value(
                nested,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Dashboard configuration numbers must be finite.")
        return

    raise ValueError("Dashboard configuration must contain JSON-compatible values only.")


def validate_dashboard_configuration(value: dict[str, Any]) -> dict[str, Any]:
    _validate_json_value(value)

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Dashboard configuration must be valid JSON.") from exc

    if len(serialized) > MAX_DASHBOARD_CONFIGURATION_BYTES:
        raise ValueError("Dashboard configuration is too large.")

    return value


def normalize_dashboard_title(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= DASHBOARD_TITLE_MAX_LENGTH:
        raise ValueError(
            f"Dashboard title must contain between 1 and {DASHBOARD_TITLE_MAX_LENGTH} characters."
        )
    return normalized


class DashboardInsightDatasetMapping(BaseModel):
    """One explicit semantic mapping for one immutable report sheet."""

    model_config = ConfigDict(extra="forbid")

    processing_job_id: UUID
    sheet_index: int = Field(ge=0)
    sheet_name: str = Field(min_length=1, max_length=255)
    columns: dict[DashboardInsightField, str] = Field(
        min_length=1,
        max_length=len(DashboardInsightField),
    )

    @field_validator("sheet_name")
    @classmethod
    def normalize_sheet_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Mapped sheet name cannot be empty.")
        # Sheet names are immutable source identifiers. Preserve their exact
        # whitespace instead of silently changing which sheet the user mapped.
        return value

    @field_validator("columns")
    @classmethod
    def normalize_columns(
        cls,
        value: dict[DashboardInsightField, str],
    ) -> dict[DashboardInsightField, str]:
        normalized: dict[DashboardInsightField, str] = {}
        used_source_columns: set[str] = set()

        for field, column_name in value.items():
            cleaned = column_name.strip()
            if not 1 <= len(cleaned) <= MAX_DASHBOARD_SOURCE_COLUMN_LENGTH:
                raise ValueError("Mapped source column name is invalid.")
            folded = cleaned.casefold()
            if folded in used_source_columns:
                raise ValueError("A source column cannot be mapped to multiple dashboard fields.")
            used_source_columns.add(folded)
            normalized[field] = cleaned

        return normalized


class DashboardInsightConfiguration(BaseModel):
    """Versioned Figma Generate Insight setup stored on a dashboard."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = DASHBOARD_INSIGHT_CONFIGURATION_VERSION
    goals: list[DashboardGoal] = Field(
        min_length=1,
        max_length=MAX_DASHBOARD_INSIGHT_GOALS,
    )
    datasets: list[DashboardInsightDatasetMapping] = Field(
        min_length=1,
        max_length=MAX_DASHBOARD_INSIGHT_DATASETS,
    )

    @model_validator(mode="after")
    def require_unique_choices(self) -> DashboardInsightConfiguration:
        if len(set(self.goals)) != len(self.goals):
            raise ValueError("Dashboard business goals must be unique.")

        dataset_keys = [
            (
                item.processing_job_id,
                item.sheet_index,
                item.sheet_name.casefold(),
            )
            for item in self.datasets
        ]
        if len(set(dataset_keys)) != len(dataset_keys):
            raise ValueError("Dashboard mapped datasets must be unique.")
        return self


def validate_insight_configuration_for_type(
    dashboard_type: DashboardType,
    configuration: DashboardInsightConfiguration,
) -> DashboardInsightConfiguration:
    normalized_type = DashboardType(dashboard_type)
    allowed = frozenset(DASHBOARD_INSIGHT_FIELDS[normalized_type])
    required = DASHBOARD_REQUIRED_INSIGHT_FIELDS[normalized_type]
    mapped = {field for dataset in configuration.datasets for field in dataset.columns}

    unexpected = mapped - allowed
    if unexpected:
        rendered = ", ".join(sorted(item.value for item in unexpected))
        raise ValueError(
            f"Dashboard mappings are not valid for {normalized_type.value}: {rendered}."
        )

    missing = required - mapped
    if missing:
        rendered = ", ".join(sorted(item.value for item in missing))
        raise ValueError(f"Dashboard mappings are missing required fields: {rendered}.")

    for dataset in configuration.datasets:
        fields = frozenset(dataset.columns)
        if fields & _NUMERIC_INSIGHT_FIELDS and DashboardInsightField.PERIOD not in fields:
            raise ValueError(
                "Every mapped dataset with numeric dashboard fields must also map period."
            )

        outflow_field, dimensions = DASHBOARD_OUTFLOW_DIMENSIONS[normalized_type]
        required_outflow_fields = frozenset(
            {DashboardInsightField.PERIOD, outflow_field, *dimensions}
        )
        if outflow_field in fields and not required_outflow_fields <= fields:
            rendered = ", ".join(
                field.value
                for field in DASHBOARD_INSIGHT_FIELDS[normalized_type]
                if field in required_outflow_fields
            )
            raise ValueError(
                f"Dashboard outflow fields must be mapped from the same sheet: {rendered}."
            )

    dataset_fields = tuple(frozenset(dataset.columns) for dataset in configuration.datasets)
    for required_group in DASHBOARD_REQUIRED_DATASET_FIELD_GROUPS[normalized_type]:
        if not any(required_group <= fields for fields in dataset_fields):
            rendered = ", ".join(sorted(field.value for field in required_group))
            raise ValueError(
                "Dashboard fields required for one calculation must be mapped "
                f"from the same sheet: {rendered}."
            )

    return configuration


class DashboardGoalContractRead(BaseModel):
    key: DashboardGoal
    label: str


class DashboardInsightFieldContractRead(BaseModel):
    key: DashboardInsightField
    label: str
    value_kind: Literal["dimension", "currency", "percent", "text", "number"]
    required: bool


class DashboardTypeContractRead(BaseModel):
    dashboard_type: DashboardType
    title: str
    description: str
    recommended_reports: list[str]
    fields: list[DashboardInsightFieldContractRead]
    required_dataset_field_groups: list[list[DashboardInsightField]]


class DashboardInsightContractRead(BaseModel):
    version: Literal[1] = DASHBOARD_INSIGHT_CONFIGURATION_VERSION
    max_goals: int = MAX_DASHBOARD_INSIGHT_GOALS
    max_datasets: int = MAX_DASHBOARD_INSIGHT_DATASETS
    numeric_fields_require_period: bool
    required_numeric_fields_share_periods: bool
    outflow_rows_require_dimensions: bool
    reject_overlapping_measure_periods: bool
    goals: list[DashboardGoalContractRead]
    dashboard_types: list[DashboardTypeContractRead]


class DashboardSourceColumnRead(BaseModel):
    name: str
    source_name: str | None
    generated: bool
    null_count: int
    non_null_count: int
    type_counts: dict[str, int]


class DashboardSourceSheetRead(BaseModel):
    sheet_index: int
    sheet_name: str
    record_count: int
    columns: list[DashboardSourceColumnRead]


class DashboardSourceSchemaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    processing_job_id: UUID
    report_upload_id: UUID
    filename: str
    record_count: int
    sheet_count: int
    sheets: list[DashboardSourceSheetRead]


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_type: DashboardType
    title: str = Field(max_length=DASHBOARD_TITLE_MAX_LENGTH)
    configuration: dict[str, Any] = Field(default_factory=dict)
    insight_configuration: DashboardInsightConfiguration | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_dashboard_title(value)

    @field_validator("configuration")
    @classmethod
    def validate_configuration(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return validate_dashboard_configuration(value)

    @model_validator(mode="after")
    def validate_insight_contract(self) -> DashboardCreate:
        if self.insight_configuration is not None:
            validate_insight_configuration_for_type(
                self.dashboard_type,
                self.insight_configuration,
            )
        return self


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=DASHBOARD_TITLE_MAX_LENGTH,
    )
    configuration: dict[str, Any] | None = None
    insight_configuration: DashboardInsightConfiguration | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Dashboard title cannot be null.")
        return normalize_dashboard_title(value)

    @field_validator("configuration")
    @classmethod
    def validate_configuration(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if value is None:
            raise ValueError("Dashboard configuration cannot be null.")
        return validate_dashboard_configuration(value)

    @field_validator("insight_configuration")
    @classmethod
    def validate_insight_configuration(
        cls,
        value: DashboardInsightConfiguration | None,
    ) -> DashboardInsightConfiguration:
        if value is None:
            raise ValueError("Dashboard insight configuration cannot be null.")
        return value

    @model_validator(mode="after")
    def require_change(self) -> DashboardUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one dashboard field must be updated.")
        return self


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    created_by_user_id: UUID
    dashboard_type: DashboardType
    title: str
    configuration: dict[str, Any]
    insight_configuration: DashboardInsightConfiguration | None
    created_at: datetime
    updated_at: datetime


class DashboardPageRead(BaseModel):
    items: list[DashboardRead]
    total: int
    limit: int
    offset: int


class DashboardSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_processing_job_ids: list[UUID] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("source_processing_job_ids")
    @classmethod
    def validate_unique_sources(
        cls,
        value: list[UUID],
    ) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Dashboard snapshot sources must be unique.")
        return value


class DashboardInsightMetricRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    unit: str
    value_decimal: str | None
    availability: Literal["available", "unavailable"]
    calculation: str
    source_fields: list[DashboardInsightField]


class DashboardInsightPointRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value_decimal: str | None


class DashboardInsightNamedSeriesRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    points: list[DashboardInsightPointRead]


class DashboardInsightSeriesRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    unit: str
    dimension: DashboardInsightField
    series_dimension: DashboardInsightField | None = None
    calculation: str
    source_fields: list[DashboardInsightField]
    points: list[DashboardInsightPointRead] = Field(default_factory=list)
    series: list[DashboardInsightNamedSeriesRead] = Field(default_factory=list)


class DashboardInsightBreakdownItemRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value_decimal: str | None
    share_percent_decimal: str | None = None


class DashboardInsightBreakdownRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    unit: str
    dimension: DashboardInsightField | None = None
    calculation: str
    source_fields: list[DashboardInsightField]
    items: list[DashboardInsightBreakdownItemRead]


class DashboardInsightSpendDriverRowRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value_decimal: str | None
    share_percent_decimal: str | None = None


class DashboardInsightVarianceRowRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    current_value_decimal: str
    prior_value_decimal: str | None
    variance_decimal: str | None
    variance_percent_decimal: str | None


class DashboardInsightTableRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    unit: str
    calculation: str
    source_fields: list[DashboardInsightField]
    current_period: str | None = None
    prior_period: str | None = None
    rows: list[DashboardInsightSpendDriverRowRead | DashboardInsightVarianceRowRead]


class DashboardInsightStatusUpdateRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str | None
    title: str | None
    detail: str | None
    status: str | None
    progress_decimal: str | None


class DashboardCalculatedInsightRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpis: list[DashboardInsightMetricRead]
    series: list[DashboardInsightSeriesRead]
    breakdowns: list[DashboardInsightBreakdownRead]
    tables: list[DashboardInsightTableRead]
    status_updates: list[DashboardInsightStatusUpdateRead] = Field(default_factory=list)


class DashboardInsightAnomalyRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    severity: Literal["attention"]
    field: DashboardInsightField
    prior_period: str
    current_period: str
    change_percent_decimal: str
    message: str


class DashboardInsightRiskRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    severity: Literal["attention", "high"]
    message: str
    evidence: dict[str, str | None]


class DashboardInsightRecommendationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    goal: DashboardGoal
    message: str
    evidence: dict[str, str | int | None]


class DashboardInsightSignalsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anomalies: list[DashboardInsightAnomalyRead]
    risks: list[DashboardInsightRiskRead]
    recommendations: list[DashboardInsightRecommendationRead]


class DashboardInsightExplainabilityRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculated_sections: list[str]
    ai_generated_sections: list[str]
    rule: str


class DashboardInsightRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[1] = DASHBOARD_INSIGHT_CONFIGURATION_VERSION
    calculation_version: Literal[1] = DASHBOARD_INSIGHT_CALCULATION_VERSION
    dashboard_type: DashboardType
    goals: list[DashboardGoal]
    mapped_row_count: int = Field(ge=1)
    calculated: DashboardCalculatedInsightRead
    signals: DashboardInsightSignalsRead
    explainability: DashboardInsightExplainabilityRead


class DashboardSnapshotDefinitionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    type: DashboardType
    title: str
    configuration: dict[str, Any]
    insight_configuration: DashboardInsightConfiguration | None = None


class DashboardSnapshotPayloadRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["spike.dashboard-snapshot"]
    schema_version: Literal[1] = DASHBOARD_SNAPSHOT_SCHEMA_VERSION
    dashboard: DashboardSnapshotDefinitionRead
    source_count: int = Field(ge=1)
    record_count: int = Field(ge=0)
    sheet_count: int = Field(ge=1)
    sources: list[dict[str, Any]]
    insight: DashboardInsightRead | None = None


class DashboardSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    dashboard_id: UUID
    version: int
    schema_version: int
    payload: DashboardSnapshotPayloadRead
    content_hash: str
    created_by_user_id: UUID
    created_at: datetime


class DashboardSnapshotPageRead(BaseModel):
    items: list[DashboardSnapshotRead]
    total: int
    limit: int
    offset: int


class DashboardSnapshotSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    snapshot_id: UUID
    processing_job_id: UUID
    source_position: int
    normalized_storage_version_id: str
    normalized_etag: str
    profile_storage_version_id: str
    profile_etag: str
    created_at: datetime


class DashboardSnapshotAIAnalysisRead(BaseModel):
    snapshot_id: UUID
    conversation_id: UUID
    purpose: AIConversationPurpose
    status: AIMessageStatus
    user_message_id: UUID | None
    assistant_message_id: UUID | None
    content: str | None
    provider_response_id: str | None
    provider_model: str | None
    provider_finish_reason: str | None
    completed_at: datetime | None
    error_code: str | None


class DashboardSnapshotDetailRead(DashboardSnapshotRead):
    sources: list[DashboardSnapshotSourceRead]
    ai_analysis: DashboardSnapshotAIAnalysisRead | None = None
