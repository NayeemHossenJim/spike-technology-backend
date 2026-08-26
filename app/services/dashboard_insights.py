from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from uuid import UUID

from app.models.dashboard import (
    DASHBOARD_INSIGHT_CALCULATION_VERSION,
    DASHBOARD_INSIGHT_CONFIGURATION_VERSION,
    DashboardGoal,
    DashboardInsightField,
    DashboardType,
)
from app.schemas.dashboard import (
    DASHBOARD_INSIGHT_FIELDS,
    DASHBOARD_OUTFLOW_DIMENSIONS,
    DASHBOARD_REQUIRED_DATASET_FIELD_GROUPS,
    DASHBOARD_REQUIRED_INSIGHT_FIELDS,
    MAX_DASHBOARD_INSIGHT_DATASETS,
    MAX_DASHBOARD_INSIGHT_GOALS,
    DashboardInsightConfiguration,
)

DASHBOARD_INSIGHT_MAX_PERIODS = 600
DASHBOARD_INSIGHT_MAX_DIMENSION_VALUES = 1_000
DASHBOARD_INSIGHT_MAX_STATUS_UPDATES = 200
DASHBOARD_INSIGHT_MAX_PERIOD_DIMENSION_POINTS = 20_000
DASHBOARD_INSIGHT_MAX_LABEL_LENGTH = 255
DASHBOARD_INSIGHT_MAX_STATUS_DETAIL_LENGTH = 2_000
DASHBOARD_INSIGHT_DECIMAL_PRECISION = 80

_STRICT_DECIMAL_TEXT = re.compile(r"^[+-]?(?:\d{1,40})(?:\.\d{1,20})?$")
_GROUPED_DECIMAL_TEXT = re.compile(r"^[+-]?(?:\d{1,3})(?:,\d{3})+(?:\.\d{1,20})?$")
_CURRENCY_SYMBOLS = frozenset("$£€¥৳₹")

_NUMERIC_FIELDS = frozenset(
    {
        DashboardInsightField.EXPENSES,
        DashboardInsightField.REVENUE,
        DashboardInsightField.NET_CASH,
        DashboardInsightField.GROSS_MARGIN,
        DashboardInsightField.SPEND,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.BUDGET,
    }
)

_CURRENCY_FIELDS = frozenset(
    {
        DashboardInsightField.EXPENSES,
        DashboardInsightField.REVENUE,
        DashboardInsightField.NET_CASH,
        DashboardInsightField.SPEND,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.BUDGET,
    }
)

_PERCENT_FIELDS = frozenset(
    {
        DashboardInsightField.GROSS_MARGIN,
        DashboardInsightField.PROGRESS,
    }
)

_NON_ADDITIVE_FIELDS = frozenset(
    {
        DashboardInsightField.NET_CASH,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.GROSS_MARGIN,
    }
)

_REQUIRED_NUMERIC_FIELDS = {
    DashboardType.EXECUTIVE_SUMMARY: (
        DashboardInsightField.EXPENSES,
        DashboardInsightField.REVENUE,
        DashboardInsightField.NET_CASH,
        DashboardInsightField.GROSS_MARGIN,
    ),
    DashboardType.FINANCIAL_PERFORMANCE: (
        DashboardInsightField.REVENUE,
        DashboardInsightField.SPEND,
        DashboardInsightField.CASH_BALANCE,
        DashboardInsightField.BUDGET,
    ),
    DashboardType.OPERATIONAL_KPI: (DashboardInsightField.EXPENSES,),
}

_DIMENSION_FIELDS = (
    DashboardInsightField.LOCATION,
    DashboardInsightField.CATEGORY,
    DashboardInsightField.DEPARTMENT,
    DashboardInsightField.VENDOR,
)

_DIMENSION_AGGREGATIONS = {
    DashboardType.EXECUTIVE_SUMMARY: frozenset(
        {
            (
                DashboardInsightField.EXPENSES,
                DashboardInsightField.CATEGORY,
            ),
            (
                DashboardInsightField.EXPENSES,
                DashboardInsightField.LOCATION,
            ),
        }
    ),
    DashboardType.FINANCIAL_PERFORMANCE: frozenset(
        {
            (
                DashboardInsightField.SPEND,
                DashboardInsightField.CATEGORY,
            ),
            (
                DashboardInsightField.SPEND,
                DashboardInsightField.LOCATION,
            ),
        }
    ),
    DashboardType.OPERATIONAL_KPI: frozenset(
        {
            (
                DashboardInsightField.EXPENSES,
                DashboardInsightField.DEPARTMENT,
            ),
            (
                DashboardInsightField.EXPENSES,
                DashboardInsightField.VENDOR,
            ),
        }
    ),
}

_FIELD_LABELS: dict[DashboardInsightField, str] = {
    DashboardInsightField.PERIOD: "Period",
    DashboardInsightField.LOCATION: "Location",
    DashboardInsightField.EXPENSES: "Expenses",
    DashboardInsightField.REVENUE: "Revenue",
    DashboardInsightField.NET_CASH: "Cash Balance",
    DashboardInsightField.CATEGORY: "Category",
    DashboardInsightField.GROSS_MARGIN: "Gross Margin",
    DashboardInsightField.SPEND: "Spend",
    DashboardInsightField.CASH_BALANCE: "Cash Balance",
    DashboardInsightField.BUDGET: "Budgeted Spend",
    DashboardInsightField.DEPARTMENT: "Department",
    DashboardInsightField.VENDOR: "Vendor",
    DashboardInsightField.STATUS_TITLE: "Status Update",
    DashboardInsightField.STATUS_DETAIL: "Status Detail",
    DashboardInsightField.STATUS: "Status",
    DashboardInsightField.PROGRESS: "Progress",
}

_FIELD_VALUE_KINDS: dict[DashboardInsightField, str] = {
    DashboardInsightField.PERIOD: "dimension",
    DashboardInsightField.LOCATION: "dimension",
    DashboardInsightField.EXPENSES: "currency",
    DashboardInsightField.REVENUE: "currency",
    DashboardInsightField.NET_CASH: "currency",
    DashboardInsightField.CATEGORY: "dimension",
    DashboardInsightField.GROSS_MARGIN: "percent",
    DashboardInsightField.SPEND: "currency",
    DashboardInsightField.CASH_BALANCE: "currency",
    DashboardInsightField.BUDGET: "currency",
    DashboardInsightField.DEPARTMENT: "dimension",
    DashboardInsightField.VENDOR: "dimension",
    DashboardInsightField.STATUS_TITLE: "text",
    DashboardInsightField.STATUS_DETAIL: "text",
    DashboardInsightField.STATUS: "text",
    DashboardInsightField.PROGRESS: "number",
}

_GOAL_LABELS: dict[DashboardGoal, str] = {
    DashboardGoal.IMPROVE_CASH_FLOW: "Improve cash flow",
    DashboardGoal.INCREASE_PROFITABILITY: "Increase profitability",
    DashboardGoal.REDUCE_EXPENSES: "Reduce expenses",
    DashboardGoal.GROW_REVENUE: "Grow revenue",
    DashboardGoal.IMPROVE_COLLECTIONS_AR: "Improve collections (AR)",
    DashboardGoal.STAY_ON_BUDGET: "Stay on budget",
    DashboardGoal.IMPROVE_FORECASTING: "Improve forecasting",
    DashboardGoal.BENCHMARK_AGAINST_PEERS: "Benchmark against peers",
    DashboardGoal.IMPROVE_OPERATIONAL_EFFICIENCY: ("Improve operational efficiency"),
}

_TYPE_COPY: dict[DashboardType, dict[str, object]] = {
    DashboardType.EXECUTIVE_SUMMARY: {
        "title": "Executive Summary",
        "description": (
            "Your AI CFO overview of business performance, featuring cash flow, "
            "cash flow forecast, burn rate, profit margin, net profit and top "
            "spend drivers."
        ),
        "recommended_reports": [
            "Profit & Loss",
            "Balance Sheet",
            "General Ledger",
        ],
    },
    DashboardType.FINANCIAL_PERFORMANCE: {
        "title": "Financial Performance",
        "description": (
            "Understand your financial health through revenue, spend, cash "
            "balance, financial health evidence, category spend variance, and "
            "budget versus spend trends."
        ),
        "recommended_reports": [
            "Profit & Loss",
            "Monthly Budget",
            "Monthly Spend",
            "Bank Statement",
            "Balance Sheet",
        ],
    },
    DashboardType.OPERATIONAL_KPI: {
        "title": "Operational KPI",
        "description": (
            "Understand monthly operating performance through expenses by "
            "department and vendor, with custom status updates for leadership."
        ),
        "recommended_reports": [
            "Custom KPI Tracker",
            "Status Update",
            "Monthly Trend",
        ],
    },
}


class DashboardInsightDataError(Exception):
    """A mapped artifact cannot safely satisfy the configured insight contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _NumericStats:
    count: int = 0
    total: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def observe(self, value: Decimal) -> None:
        self.count += 1
        with localcontext() as context:
            context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
            self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def average(self) -> Decimal | None:
        if self.count == 0:
            return None
        with localcontext() as context:
            context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
            return self.total / Decimal(self.count)


def dashboard_insight_contract_payload() -> dict[str, object]:
    return {
        "version": DASHBOARD_INSIGHT_CONFIGURATION_VERSION,
        "max_goals": MAX_DASHBOARD_INSIGHT_GOALS,
        "max_datasets": MAX_DASHBOARD_INSIGHT_DATASETS,
        "numeric_fields_require_period": True,
        "required_numeric_fields_share_periods": True,
        "outflow_rows_require_dimensions": True,
        "reject_overlapping_measure_periods": True,
        "goals": [
            {
                "key": goal.value,
                "label": _GOAL_LABELS[goal],
            }
            for goal in DashboardGoal
        ],
        "dashboard_types": [
            {
                "dashboard_type": dashboard_type.value,
                **_TYPE_COPY[dashboard_type],
                "fields": [
                    {
                        "key": field.value,
                        "label": _FIELD_LABELS[field],
                        "value_kind": _FIELD_VALUE_KINDS[field],
                        "required": (field in DASHBOARD_REQUIRED_INSIGHT_FIELDS[dashboard_type]),
                    }
                    for field in DASHBOARD_INSIGHT_FIELDS[dashboard_type]
                ],
                "required_dataset_field_groups": [
                    [
                        field.value
                        for field in DASHBOARD_INSIGHT_FIELDS[dashboard_type]
                        if field in group
                    ]
                    for group in DASHBOARD_REQUIRED_DATASET_FIELD_GROUPS[dashboard_type]
                ],
            }
            for dashboard_type in DashboardType
        ],
    }


def _strict_decimal(
    value: object,
    *,
    allow_currency: bool = False,
    allow_percent: bool = False,
) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("−", "-")
    negative_parentheses = candidate.startswith("(") and candidate.endswith(")")
    if negative_parentheses:
        candidate = candidate[1:-1].strip()
    if candidate.endswith("%"):
        if not allow_percent:
            return None
        candidate = candidate[:-1].strip()
    if candidate and candidate[0] in _CURRENCY_SYMBOLS:
        if not allow_currency:
            return None
        candidate = candidate[1:].strip()
    if candidate and candidate[-1] in _CURRENCY_SYMBOLS:
        if not allow_currency:
            return None
        candidate = candidate[:-1].strip()
    candidate = candidate.replace(" ", "")
    if "," in candidate:
        if not _GROUPED_DECIMAL_TEXT.fullmatch(candidate):
            return None
        candidate = candidate.replace(",", "")
    if negative_parentheses:
        if candidate.startswith(("+", "-")):
            return None
        candidate = f"-{candidate}"
    if not _STRICT_DECIMAL_TEXT.fullmatch(candidate):
        return None
    try:
        parsed = Decimal(candidate)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered == "-0" else rendered


def _ratio_percent(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
        return (numerator / denominator) * Decimal(100)


def _decimal_sum(values: Iterable[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
        return sum(values, Decimal(0))


def _label(value: object, *, maximum: int = DASHBOARD_INSIGHT_MAX_LABEL_LENGTH) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = " ".join(str(value).split()).strip()
    if not rendered:
        return None
    if len(rendered) > maximum:
        raise DashboardInsightDataError("mapped_text_value_too_large")
    return rendered


def _period_sort_key(value: str) -> tuple[object, ...]:
    candidate = value.strip()
    iso_candidate = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
    if parsed is not None:
        return (0, parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute)

    for pattern in (
        "%Y-%m",
        "%b-%y",
        "%b %Y",
        "%B %Y",
        "%b",
        "%B",
        "%Y",
    ):
        try:
            parsed_period = datetime.strptime(candidate, pattern)
        except ValueError:
            continue
        return (
            0,
            parsed_period.year if "%Y" in pattern or "%y" in pattern else 0,
            parsed_period.month,
            parsed_period.day,
            0,
            0,
        )

    numeric = _strict_decimal(candidate)
    if numeric is not None:
        return (1, numeric)
    return (2, candidate.casefold())


def _sorted_periods(values: dict[str, object]) -> list[str]:
    return sorted(values, key=lambda item: (_period_sort_key(item), item.casefold()))


class DashboardInsightAccumulator:
    """Bounded, deterministic calculations over explicitly mapped report rows."""

    def __init__(
        self,
        *,
        dashboard_type: DashboardType,
        configuration: DashboardInsightConfiguration,
    ) -> None:
        self.dashboard_type = DashboardType(dashboard_type)
        self.configuration = configuration
        self._mappings = {
            (
                dataset.processing_job_id,
                dataset.sheet_index,
                dataset.sheet_name,
            ): dataset.columns
            for dataset in configuration.datasets
        }
        self._validated_mappings: set[tuple[UUID, int, str]] = set()
        self._totals: dict[DashboardInsightField, _NumericStats] = {}
        self._by_period: dict[
            DashboardInsightField,
            dict[str, Decimal],
        ] = {}
        self._by_dimension: dict[
            tuple[DashboardInsightField, DashboardInsightField],
            dict[str, Decimal],
        ] = {}
        self._by_period_dimension: dict[
            tuple[DashboardInsightField, DashboardInsightField],
            dict[str, dict[str, Decimal]],
        ] = {}
        self._unique_values: dict[DashboardInsightField, set[str]] = {}
        self._status_updates: list[dict[str, object]] = []
        self._point_in_time_values: dict[
            tuple[DashboardInsightField, str],
            Decimal,
        ] = {}
        self._measure_period_dataset: dict[
            tuple[DashboardInsightField, str],
            tuple[UUID, int, str],
        ] = {}
        self._period_dimension_point_count = 0
        self._mapped_row_count = 0

    @property
    def mapped_processing_job_ids(self) -> frozenset[UUID]:
        return frozenset(key[0] for key in self._mappings)

    def validate_profile(
        self,
        *,
        processing_job_id: UUID,
        profile: dict[str, Any],
    ) -> None:
        sheets = profile.get("sheets")
        if not isinstance(sheets, list):
            raise DashboardInsightDataError("mapped_profile_invalid")

        indexed: dict[tuple[int, str], set[str]] = {}
        for sheet in sheets:
            if not isinstance(sheet, dict):
                raise DashboardInsightDataError("mapped_profile_invalid")
            index = sheet.get("index")
            name = sheet.get("name")
            columns = sheet.get("columns")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not isinstance(name, str)
                or not isinstance(columns, list)
            ):
                raise DashboardInsightDataError("mapped_profile_invalid")
            column_names = {
                column.get("name")
                for column in columns
                if isinstance(column, dict) and isinstance(column.get("name"), str)
            }
            if len(column_names) != len(columns):
                raise DashboardInsightDataError("mapped_profile_invalid")
            indexed[(index, name)] = column_names

        for key, mapping in self._mappings.items():
            job_id, sheet_index, sheet_name = key
            if job_id != processing_job_id:
                continue
            available = indexed.get((sheet_index, sheet_name))
            if available is None or not set(mapping.values()) <= available:
                raise DashboardInsightDataError("mapped_source_column_not_found")
            self._validated_mappings.add(key)

    def _observe_unique(
        self,
        field: DashboardInsightField,
        value: str,
    ) -> None:
        values = self._unique_values.setdefault(field, set())
        values.add(value)
        if len(values) > DASHBOARD_INSIGHT_MAX_DIMENSION_VALUES:
            raise DashboardInsightDataError("mapped_dimension_cardinality_exceeded")

    def _observe_period_value(
        self,
        field: DashboardInsightField,
        period: str,
        value: Decimal,
    ) -> None:
        values = self._by_period.setdefault(field, {})
        with localcontext() as context:
            context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
            values[period] = values.get(period, Decimal(0)) + value
        if len(values) > DASHBOARD_INSIGHT_MAX_PERIODS:
            raise DashboardInsightDataError("mapped_period_cardinality_exceeded")

    def observe(
        self,
        *,
        processing_job_id: UUID,
        sheet_index: int,
        sheet_name: str,
        values: dict[str, object],
    ) -> None:
        dataset_key = (processing_job_id, sheet_index, sheet_name)
        mapping = self._mappings.get(dataset_key)
        if mapping is None:
            return

        semantic = {field: values[column] for field, column in mapping.items()}
        period = _label(semantic.get(DashboardInsightField.PERIOD))
        dimensions: dict[DashboardInsightField, str] = {}
        for field in _DIMENSION_FIELDS:
            if field not in semantic:
                continue
            rendered = _label(semantic[field])
            if rendered is not None:
                dimensions[field] = rendered
                self._observe_unique(field, rendered)

        observed_numeric = False
        for field in _NUMERIC_FIELDS:
            if field not in semantic or semantic[field] is None:
                continue
            numeric = _strict_decimal(
                semantic[field],
                allow_currency=field in _CURRENCY_FIELDS,
                allow_percent=field in _PERCENT_FIELDS,
            )
            if numeric is None:
                raise DashboardInsightDataError("mapped_numeric_value_invalid")
            if period is None:
                raise DashboardInsightDataError("mapped_period_value_missing")

            measure_period = (field, period)
            prior_dataset = self._measure_period_dataset.get(measure_period)
            if prior_dataset is not None and prior_dataset != dataset_key:
                raise DashboardInsightDataError("mapped_measure_period_overlap")
            self._measure_period_dataset[measure_period] = dataset_key

            observed_numeric = True
            outflow_field, required_dimensions = DASHBOARD_OUTFLOW_DIMENSIONS[self.dashboard_type]
            if field is outflow_field and any(
                dimension not in dimensions for dimension in required_dimensions
            ):
                raise DashboardInsightDataError("mapped_required_dimension_value_missing")
            if field in _NON_ADDITIVE_FIELDS:
                prior_value = self._point_in_time_values.get(measure_period)
                if prior_value is not None:
                    if prior_value != numeric:
                        raise DashboardInsightDataError("mapped_point_in_time_value_ambiguous")
                    continue
                self._point_in_time_values[measure_period] = numeric

            self._totals.setdefault(field, _NumericStats()).observe(numeric)
            self._observe_period_value(field, period, numeric)

            for dimension_field, dimension_value in dimensions.items():
                dimension_key = (field, dimension_field)
                if dimension_key not in _DIMENSION_AGGREGATIONS[self.dashboard_type]:
                    continue
                dimension_totals = self._by_dimension.setdefault(
                    dimension_key,
                    {},
                )
                with localcontext() as context:
                    context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
                    dimension_totals[dimension_value] = (
                        dimension_totals.get(dimension_value, Decimal(0)) + numeric
                    )
                period_dimensions = self._by_period_dimension.setdefault(
                    dimension_key,
                    {},
                )
                period_values = period_dimensions.setdefault(period, {})
                if dimension_value not in period_values:
                    self._period_dimension_point_count += 1
                    if (
                        self._period_dimension_point_count
                        > DASHBOARD_INSIGHT_MAX_PERIOD_DIMENSION_POINTS
                    ):
                        raise DashboardInsightDataError("mapped_series_cardinality_exceeded")
                with localcontext() as context:
                    context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
                    period_values[dimension_value] = (
                        period_values.get(dimension_value, Decimal(0)) + numeric
                    )

        if observed_numeric:
            self._mapped_row_count += 1

        status_fields = {
            field: semantic.get(field)
            for field in (
                DashboardInsightField.STATUS_TITLE,
                DashboardInsightField.STATUS_DETAIL,
                DashboardInsightField.STATUS,
                DashboardInsightField.PROGRESS,
            )
            if field in semantic
        }
        if status_fields and any(value is not None for value in status_fields.values()):
            if len(self._status_updates) >= DASHBOARD_INSIGHT_MAX_STATUS_UPDATES:
                raise DashboardInsightDataError("mapped_status_update_limit_exceeded")
            progress = status_fields.get(DashboardInsightField.PROGRESS)
            progress_decimal = (
                _strict_decimal(progress, allow_percent=True) if progress is not None else None
            )
            if progress is not None and progress_decimal is None:
                raise DashboardInsightDataError("mapped_numeric_value_invalid")
            self._status_updates.append(
                {
                    "period": period,
                    "title": _label(status_fields.get(DashboardInsightField.STATUS_TITLE)),
                    "detail": _label(
                        status_fields.get(DashboardInsightField.STATUS_DETAIL),
                        maximum=DASHBOARD_INSIGHT_MAX_STATUS_DETAIL_LENGTH,
                    ),
                    "status": _label(status_fields.get(DashboardInsightField.STATUS)),
                    "progress_decimal": _decimal_text(progress_decimal),
                }
            )

    def _metric(
        self,
        *,
        key: str,
        label: str,
        unit: str,
        calculation: str,
        source_fields: tuple[DashboardInsightField, ...],
        value: Decimal | None,
    ) -> dict[str, object]:
        return {
            "key": key,
            "label": label,
            "unit": unit,
            "value_decimal": _decimal_text(value),
            "availability": "available" if value is not None else "unavailable",
            "calculation": calculation,
            "source_fields": [field.value for field in source_fields],
        }

    def _validate_required_period_coverage(self) -> None:
        required = _REQUIRED_NUMERIC_FIELDS[self.dashboard_type]
        period_sets = {field: frozenset(self._by_period.get(field, {})) for field in required}
        if any(not periods for periods in period_sets.values()):
            raise DashboardInsightDataError("mapped_required_numeric_values_missing")
        baseline = period_sets[required[0]]
        if any(periods != baseline for periods in period_sets.values()):
            raise DashboardInsightDataError("mapped_period_coverage_mismatch")

    def _sum(self, field: DashboardInsightField) -> Decimal | None:
        stats = self._totals.get(field)
        return None if stats is None or stats.count == 0 else stats.total

    def _average(self, field: DashboardInsightField) -> Decimal | None:
        stats = self._totals.get(field)
        return None if stats is None else stats.average

    def _latest(self, field: DashboardInsightField) -> Decimal | None:
        values = self._by_period.get(field, {})
        periods = _sorted_periods(values)
        return values[periods[-1]] if periods else None

    def _single_series(
        self,
        *,
        key: str,
        label: str,
        field: DashboardInsightField,
        unit: str,
        calculation: str,
    ) -> dict[str, object]:
        values = self._by_period.get(field, {})
        return {
            "key": key,
            "label": label,
            "unit": unit,
            "dimension": DashboardInsightField.PERIOD.value,
            "calculation": calculation,
            "source_fields": [DashboardInsightField.PERIOD.value, field.value],
            "points": [
                {
                    "label": period,
                    "value_decimal": _decimal_text(values[period]),
                }
                for period in _sorted_periods(values)
            ],
        }

    def _cash_flow_forecast_series(self) -> dict[str, object]:
        values = self._by_period.get(DashboardInsightField.NET_CASH, {})
        periods = _sorted_periods(values)
        actual_points = [
            {
                "label": period,
                "value_decimal": _decimal_text(values[period]),
            }
            for period in periods
        ]

        forecast_points: list[dict[str, str | None]] = []
        if periods:
            latest_period = periods[-1]
            latest_value = values[latest_period]
            changes = [
                values[current] - values[prior]
                for prior, current in zip(periods, periods[1:], strict=False)
            ]
            recent_changes = changes[-3:]
            if recent_changes:
                with localcontext() as context:
                    context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
                    trend = _decimal_sum(recent_changes) / Decimal(len(recent_changes))
            else:
                trend = Decimal(0)

            # Include the last observed point as the visual hand-off between
            # actual and projected values, then project three report periods.
            forecast_points.append(
                {
                    "label": latest_period,
                    "value_decimal": _decimal_text(latest_value),
                }
            )
            forecast_points.extend(
                {
                    "label": f"Forecast +{step}",
                    "value_decimal": _decimal_text(latest_value + trend * step),
                }
                for step in range(1, 4)
            )

        return {
            "key": "cash_flow_forecast",
            "label": "Cash Flow Forecast",
            "unit": "currency",
            "dimension": DashboardInsightField.PERIOD.value,
            "calculation": (
                "three-period linear projection from the average of up to the "
                "three latest mapped net_cash period changes; one observed "
                "period uses carry-forward"
            ),
            "source_fields": [
                DashboardInsightField.PERIOD.value,
                DashboardInsightField.NET_CASH.value,
            ],
            "series": [
                {
                    "key": "actual",
                    "label": "Actual",
                    "points": actual_points,
                },
                {
                    "key": "forecast",
                    "label": "Forecast",
                    "points": forecast_points,
                },
            ],
        }

    def _comparison_series(
        self,
        *,
        key: str,
        label: str,
        fields: tuple[DashboardInsightField, ...],
        unit: str,
    ) -> dict[str, object]:
        return {
            "key": key,
            "label": label,
            "unit": unit,
            "dimension": DashboardInsightField.PERIOD.value,
            "calculation": "sum each mapped measure by period",
            "source_fields": [
                DashboardInsightField.PERIOD.value,
                *(field.value for field in fields),
            ],
            "series": [
                {
                    "key": field.value,
                    "label": _FIELD_LABELS[field],
                    "points": [
                        {
                            "label": period,
                            "value_decimal": _decimal_text(value),
                        }
                        for period, value in (
                            (period, self._by_period.get(field, {})[period])
                            for period in _sorted_periods(self._by_period.get(field, {}))
                        )
                    ],
                }
                for field in fields
            ],
        }

    def _dimension_series(
        self,
        *,
        key: str,
        label: str,
        field: DashboardInsightField,
        dimension: DashboardInsightField,
    ) -> dict[str, object]:
        period_values = self._by_period_dimension.get((field, dimension), {})
        totals = self._by_dimension.get((field, dimension), {})
        dimension_values = sorted(
            {item for values in period_values.values() for item in values},
            key=lambda item: (-abs(totals.get(item, Decimal(0))), item.casefold()),
        )
        periods = _sorted_periods(period_values)
        return {
            "key": key,
            "label": label,
            "unit": "currency",
            "dimension": DashboardInsightField.PERIOD.value,
            "series_dimension": dimension.value,
            "calculation": f"sum({field.value}) by period and {dimension.value}",
            "source_fields": [
                DashboardInsightField.PERIOD.value,
                dimension.value,
                field.value,
            ],
            "series": [
                {
                    "key": item,
                    "label": item,
                    "points": [
                        {
                            "label": period,
                            "value_decimal": _decimal_text(period_values[period][item]),
                        }
                        for period in periods
                        if item in period_values[period]
                    ],
                }
                for item in dimension_values
            ],
        }

    def _breakdown(
        self,
        *,
        key: str,
        label: str,
        field: DashboardInsightField,
        dimension: DashboardInsightField,
    ) -> dict[str, object]:
        values = self._by_dimension.get((field, dimension), {})
        total = _decimal_sum(values.values())
        items = sorted(values.items(), key=lambda item: (-item[1], item[0].casefold()))
        return {
            "key": key,
            "label": label,
            "unit": "currency",
            "dimension": dimension.value,
            "calculation": f"sum({field.value}) by {dimension.value}",
            "source_fields": [dimension.value, field.value],
            "items": [
                {
                    "label": item_label,
                    "value_decimal": _decimal_text(value),
                    "share_percent_decimal": _decimal_text(_ratio_percent(value, total)),
                }
                for item_label, value in items
            ],
        }

    def _variance_table(
        self,
        *,
        field: DashboardInsightField,
        dimension: DashboardInsightField,
    ) -> dict[str, object]:
        period_values = self._by_period_dimension.get((field, dimension), {})
        periods = _sorted_periods(period_values)
        current_period = periods[-1] if periods else None
        prior_period = periods[-2] if len(periods) >= 2 else None
        dimensions = sorted(
            {item for values in period_values.values() for item in values},
            key=str.casefold,
        )
        rows: list[dict[str, object]] = []
        for item in dimensions:
            current = (
                period_values[current_period].get(item, Decimal(0))
                if current_period is not None
                else Decimal(0)
            )
            prior = (
                period_values[prior_period].get(item, Decimal(0))
                if prior_period is not None
                else Decimal(0)
            )
            rows.append(
                {
                    "label": item,
                    "current_value_decimal": _decimal_text(current),
                    "prior_value_decimal": (
                        _decimal_text(prior) if prior_period is not None else None
                    ),
                    "variance_decimal": (
                        _decimal_text(current - prior) if prior_period is not None else None
                    ),
                    "variance_percent_decimal": (
                        _decimal_text(_ratio_percent(current - prior, prior))
                        if prior_period is not None
                        else None
                    ),
                }
            )
        rows.sort(
            key=lambda item: (
                -abs(Decimal(str(item["variance_decimal"] or "0"))),
                str(item["label"]).casefold(),
            )
        )
        return {
            "key": "spend_variance_by_category",
            "label": "Spend Variance by Category",
            "unit": "currency",
            "current_period": current_period,
            "prior_period": prior_period,
            "calculation": "current period spend minus prior period spend by category",
            "source_fields": [
                DashboardInsightField.PERIOD.value,
                dimension.value,
                field.value,
            ],
            "rows": rows,
        }

    def _anomalies(self) -> list[dict[str, object]]:
        primary_fields = {
            DashboardType.EXECUTIVE_SUMMARY: (
                DashboardInsightField.REVENUE,
                DashboardInsightField.EXPENSES,
                DashboardInsightField.NET_CASH,
            ),
            DashboardType.FINANCIAL_PERFORMANCE: (
                DashboardInsightField.REVENUE,
                DashboardInsightField.SPEND,
            ),
            DashboardType.OPERATIONAL_KPI: (DashboardInsightField.EXPENSES,),
        }[self.dashboard_type]
        results: list[dict[str, object]] = []
        for field in primary_fields:
            values = self._by_period.get(field, {})
            periods = _sorted_periods(values)
            for prior_period, current_period in zip(
                periods,
                periods[1:],
                strict=False,
            ):
                prior = values[prior_period]
                current = values[current_period]
                change = _ratio_percent(current - prior, abs(prior))
                if change is None or abs(change) < Decimal(25):
                    continue
                results.append(
                    {
                        "key": "period_over_period_change",
                        "severity": "attention",
                        "field": field.value,
                        "prior_period": prior_period,
                        "current_period": current_period,
                        "change_percent_decimal": _decimal_text(change),
                        "message": (
                            f"{_FIELD_LABELS[field]} changed by "
                            f"{_decimal_text(change)}% from {prior_period} to "
                            f"{current_period}."
                        ),
                    }
                )
        results.sort(
            key=lambda item: _period_sort_key(str(item["current_period"])),
            reverse=True,
        )
        return results[:20]

    def _risks(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        revenue = self._sum(DashboardInsightField.REVENUE)
        outflow_field = (
            DashboardInsightField.SPEND
            if self.dashboard_type is DashboardType.FINANCIAL_PERFORMANCE
            else DashboardInsightField.EXPENSES
        )
        outflow = self._sum(outflow_field)
        if revenue is not None and outflow is not None and outflow > revenue:
            results.append(
                {
                    "key": "outflow_exceeds_revenue",
                    "severity": "high",
                    "message": "Mapped outflow exceeds mapped revenue.",
                    "evidence": {
                        "revenue_decimal": _decimal_text(revenue),
                        "outflow_decimal": _decimal_text(outflow),
                    },
                }
            )

        budget = self._sum(DashboardInsightField.BUDGET)
        spend = self._sum(DashboardInsightField.SPEND)
        if budget is not None and spend is not None and spend > budget:
            results.append(
                {
                    "key": "spend_above_budget",
                    "severity": "high",
                    "message": "Mapped spend is above mapped budget.",
                    "evidence": {
                        "spend_decimal": _decimal_text(spend),
                        "budget_decimal": _decimal_text(budget),
                        "variance_decimal": _decimal_text(spend - budget),
                    },
                }
            )

        concentration_dimension = (
            DashboardInsightField.VENDOR
            if self.dashboard_type is DashboardType.OPERATIONAL_KPI
            else DashboardInsightField.CATEGORY
        )
        concentration_values = self._by_dimension.get(
            (outflow_field, concentration_dimension),
            {},
        )
        concentration_total = _decimal_sum(concentration_values.values())
        if concentration_values and concentration_total > 0:
            top_label, top_value = max(
                concentration_values.items(),
                key=lambda item: item[1],
            )
            share = _ratio_percent(top_value, concentration_total)
            if share is not None and share >= Decimal(50):
                results.append(
                    {
                        "key": "outflow_concentration",
                        "severity": "attention",
                        "message": (
                            f"{top_label} represents {_decimal_text(share)}% of mapped outflow."
                        ),
                        "evidence": {
                            "dimension": concentration_dimension.value,
                            "label": top_label,
                            "share_percent_decimal": _decimal_text(share),
                        },
                    }
                )
        return results

    def _recommendations(self) -> list[dict[str, object]]:
        outflow_field = (
            DashboardInsightField.SPEND
            if self.dashboard_type is DashboardType.FINANCIAL_PERFORMANCE
            else DashboardInsightField.EXPENSES
        )
        dimension = (
            DashboardInsightField.VENDOR
            if self.dashboard_type is DashboardType.OPERATIONAL_KPI
            else DashboardInsightField.CATEGORY
        )
        values = self._by_dimension.get((outflow_field, dimension), {})
        total = _decimal_sum(values.values())
        top = max(values.items(), key=lambda item: item[1]) if values else None
        top_evidence: dict[str, object] = {}
        if top is not None:
            top_label, top_value = top
            top_evidence = {
                "dimension": dimension.value,
                "label": top_label,
                "value_decimal": _decimal_text(top_value),
                "share_percent_decimal": _decimal_text(_ratio_percent(top_value, total)),
            }
        results: list[dict[str, object]] = []

        for goal in self.configuration.goals:
            evidence: dict[str, object] = {}
            if goal is DashboardGoal.BENCHMARK_AGAINST_PEERS:
                message = (
                    "No approved peer benchmark is present in the mapped reports; "
                    "add a governed benchmark source before making comparisons."
                )
            elif goal is DashboardGoal.IMPROVE_COLLECTIONS_AR:
                message = (
                    "Accounts-receivable fields are not part of this dashboard "
                    "mapping; add an approved AR source before evaluating collections."
                )
            elif goal is DashboardGoal.IMPROVE_CASH_FLOW:
                cash_field = {
                    DashboardType.EXECUTIVE_SUMMARY: DashboardInsightField.NET_CASH,
                    DashboardType.FINANCIAL_PERFORMANCE: (DashboardInsightField.CASH_BALANCE),
                }.get(self.dashboard_type)
                cash_value = self._latest(cash_field) if cash_field is not None else None
                evidence = {
                    "cash_field": cash_field.value if cash_field is not None else None,
                    "latest_cash_decimal": _decimal_text(cash_value),
                    "outflow_decimal": _decimal_text(self._sum(outflow_field)),
                    **top_evidence,
                }
                if cash_value is None:
                    message = (
                        "Cash-balance evidence is not available in this dashboard; "
                        "add an approved cash source before evaluating cash flow."
                    )
                else:
                    message = (
                        "Review the latest mapped cash evidence and largest outflow "
                        "concentration before setting a cash-flow action."
                    )
            elif (
                goal
                in {
                    DashboardGoal.REDUCE_EXPENSES,
                    DashboardGoal.IMPROVE_OPERATIONAL_EFFICIENCY,
                }
                and top is not None
            ):
                evidence = top_evidence
                action = (
                    "an efficiency action"
                    if goal is DashboardGoal.IMPROVE_OPERATIONAL_EFFICIENCY
                    else "a reduction action"
                )
                message = (
                    f"Review {top[0]}, the largest mapped {dimension.value} "
                    f"outflow, before setting {action}."
                )
            elif goal in {
                DashboardGoal.REDUCE_EXPENSES,
                DashboardGoal.IMPROVE_OPERATIONAL_EFFICIENCY,
            }:
                message = (
                    "Mapped outflow concentration is not available; add an approved "
                    "category or vendor source before setting a reduction action."
                )
            elif goal is DashboardGoal.STAY_ON_BUDGET:
                budget = self._sum(DashboardInsightField.BUDGET)
                spend = self._sum(DashboardInsightField.SPEND)
                evidence = {
                    "budget_decimal": _decimal_text(budget),
                    "spend_decimal": _decimal_text(spend),
                }
                if budget is None or spend is None:
                    message = (
                        "Budget and spend evidence is not available in this dashboard; "
                        "add approved budget and spend sources before evaluating variance."
                    )
                else:
                    message = (
                        "Review mapped budget versus spend variance and assign owners "
                        "to the largest category differences."
                    )
            elif goal is DashboardGoal.GROW_REVENUE:
                revenue = self._sum(DashboardInsightField.REVENUE)
                evidence = {"revenue_decimal": _decimal_text(revenue)}
                if revenue is None:
                    message = (
                        "Revenue evidence is not available in this dashboard; add an "
                        "approved revenue source before setting a growth target."
                    )
                else:
                    message = (
                        "Review period revenue changes and validate the drivers behind "
                        "declines before setting a growth target."
                    )
            elif goal is DashboardGoal.INCREASE_PROFITABILITY:
                revenue = self._sum(DashboardInsightField.REVENUE)
                outflow = self._sum(outflow_field)
                evidence = {
                    "revenue_decimal": _decimal_text(revenue),
                    "outflow_decimal": _decimal_text(outflow),
                    "difference_decimal": (
                        _decimal_text(revenue - outflow)
                        if revenue is not None and outflow is not None
                        else None
                    ),
                }
                if revenue is None or outflow is None:
                    message = (
                        "Revenue and outflow evidence is not available in this dashboard; "
                        "add approved sources before evaluating profitability."
                    )
                else:
                    message = (
                        "Review the mapped revenue-to-outflow difference and the "
                        "largest cost concentration before choosing a margin action."
                    )
            elif goal is DashboardGoal.IMPROVE_FORECASTING:
                periods = {
                    period
                    for values_by_period in self._by_period.values()
                    for period in values_by_period
                }
                evidence = {"mapped_period_count": len(periods)}
                if len(periods) < 2:
                    message = (
                        "At least two mapped periods are required before using the "
                        "dashboard as a forecast baseline."
                    )
                else:
                    message = (
                        "Review period coverage and investigate flagged changes before "
                        "using the mapped trend as a forecast baseline."
                    )
            else:
                message = (
                    "Review the calculated dashboard evidence before assigning a "
                    "goal-specific action."
                )

            results.append(
                {
                    "key": f"goal_{goal.value}",
                    "goal": goal.value,
                    "message": message,
                    "evidence": evidence,
                }
            )
        return results

    def _executive_calculated(self) -> dict[str, object]:
        revenue = self._sum(DashboardInsightField.REVENUE)
        expenses = self._sum(DashboardInsightField.EXPENSES)
        net_profit = revenue - expenses if revenue is not None and expenses is not None else None
        periods = self._by_period.get(DashboardInsightField.EXPENSES, {})
        burn_rate = expenses / Decimal(len(periods)) if expenses is not None and periods else None
        category = self._breakdown(
            key="expense_categories",
            label="Expense Categories",
            field=DashboardInsightField.EXPENSES,
            dimension=DashboardInsightField.CATEGORY,
        )
        location = self._breakdown(
            key="expenses_by_location",
            label="Expenses by Location",
            field=DashboardInsightField.EXPENSES,
            dimension=DashboardInsightField.LOCATION,
        )
        return {
            "kpis": [
                self._metric(
                    key="total_revenue",
                    label="Total Revenue",
                    unit="currency",
                    calculation="sum(revenue)",
                    source_fields=(DashboardInsightField.REVENUE,),
                    value=revenue,
                ),
                self._metric(
                    key="net_profit",
                    label="Net Profit",
                    unit="currency",
                    calculation="sum(revenue) - sum(expenses)",
                    source_fields=(
                        DashboardInsightField.REVENUE,
                        DashboardInsightField.EXPENSES,
                    ),
                    value=net_profit,
                ),
                self._metric(
                    key="gross_margin",
                    label="Gross Margin",
                    unit="percent",
                    calculation="average(gross_margin) across distinct mapped periods",
                    source_fields=(DashboardInsightField.GROSS_MARGIN,),
                    value=self._average(DashboardInsightField.GROSS_MARGIN),
                ),
                self._metric(
                    key="burn_rate",
                    label="Burn Rate",
                    unit="currency_per_period",
                    calculation="sum(expenses) / distinct mapped periods",
                    source_fields=(
                        DashboardInsightField.PERIOD,
                        DashboardInsightField.EXPENSES,
                    ),
                    value=burn_rate,
                ),
            ],
            "series": [
                self._single_series(
                    key="monthly_spend",
                    label="Monthly Spend",
                    field=DashboardInsightField.EXPENSES,
                    unit="currency",
                    calculation="sum(expenses) by period",
                ),
                self._cash_flow_forecast_series(),
            ],
            "breakdowns": [category, location],
            "tables": [
                {
                    "key": "top_spend_drivers",
                    "label": "Top Spend Drivers by Category",
                    "unit": "currency",
                    "calculation": "expense categories sorted by mapped spend",
                    "source_fields": [
                        DashboardInsightField.CATEGORY.value,
                        DashboardInsightField.EXPENSES.value,
                    ],
                    "rows": category["items"],
                }
            ],
        }

    def _financial_calculated(self) -> dict[str, object]:
        revenue = self._sum(DashboardInsightField.REVENUE)
        spend = self._sum(DashboardInsightField.SPEND)
        budget = self._sum(DashboardInsightField.BUDGET)
        health_ratio = (
            _ratio_percent(spend, revenue) if spend is not None and revenue is not None else None
        )
        location = self._breakdown(
            key="spend_by_location",
            label="Spend by Location",
            field=DashboardInsightField.SPEND,
            dimension=DashboardInsightField.LOCATION,
        )
        return {
            "kpis": [
                self._metric(
                    key="total_revenue",
                    label="Total Revenue",
                    unit="currency",
                    calculation="sum(revenue)",
                    source_fields=(DashboardInsightField.REVENUE,),
                    value=revenue,
                ),
                self._metric(
                    key="total_spend",
                    label="Total Spend",
                    unit="currency",
                    calculation="sum(spend)",
                    source_fields=(DashboardInsightField.SPEND,),
                    value=spend,
                ),
                self._metric(
                    key="cash_balance",
                    label="Cash Balance",
                    unit="currency",
                    calculation="latest(cash_balance) by mapped period",
                    source_fields=(
                        DashboardInsightField.PERIOD,
                        DashboardInsightField.CASH_BALANCE,
                    ),
                    value=self._latest(DashboardInsightField.CASH_BALANCE),
                ),
                self._metric(
                    key="total_budget",
                    label="Total Budget",
                    unit="currency",
                    calculation="sum(budget)",
                    source_fields=(DashboardInsightField.BUDGET,),
                    value=budget,
                ),
                self._metric(
                    key="spend_to_revenue",
                    label="Spend to Revenue",
                    unit="percent",
                    calculation="sum(spend) / sum(revenue) * 100",
                    source_fields=(
                        DashboardInsightField.SPEND,
                        DashboardInsightField.REVENUE,
                    ),
                    value=health_ratio,
                ),
            ],
            "series": [
                self._comparison_series(
                    key="monthly_spend_vs_revenue",
                    label="Monthly Spend vs Revenue",
                    fields=(
                        DashboardInsightField.SPEND,
                        DashboardInsightField.REVENUE,
                    ),
                    unit="currency",
                ),
                self._dimension_series(
                    key="monthly_trend_by_category",
                    label="Monthly Trend by Category",
                    field=DashboardInsightField.SPEND,
                    dimension=DashboardInsightField.CATEGORY,
                ),
            ],
            "breakdowns": [
                {
                    "key": "financial_health",
                    "label": "Financial Health",
                    "unit": "currency",
                    "calculation": "mapped revenue compared with mapped spend",
                    "source_fields": [
                        DashboardInsightField.REVENUE.value,
                        DashboardInsightField.SPEND.value,
                    ],
                    "items": [
                        {
                            "label": "Revenue",
                            "value_decimal": _decimal_text(revenue),
                        },
                        {
                            "label": "Spend",
                            "value_decimal": _decimal_text(spend),
                        },
                    ],
                },
                location,
            ],
            "tables": [
                self._variance_table(
                    field=DashboardInsightField.SPEND,
                    dimension=DashboardInsightField.CATEGORY,
                )
            ],
        }

    def _operational_calculated(self) -> dict[str, object]:
        departments = len(self._unique_values.get(DashboardInsightField.DEPARTMENT, set()))
        vendors = len(self._unique_values.get(DashboardInsightField.VENDOR, set()))
        expenses = self._sum(DashboardInsightField.EXPENSES)
        average_department = (
            expenses / Decimal(departments) if expenses is not None and departments > 0 else None
        )
        status_updates = sorted(
            self._status_updates,
            key=lambda item: (
                1 if item["period"] is None else 0,
                _period_sort_key(str(item["period"] or "")),
            ),
        )
        return {
            "kpis": [
                self._metric(
                    key="number_of_departments",
                    label="Number of Departments",
                    unit="count",
                    calculation="count(distinct department)",
                    source_fields=(DashboardInsightField.DEPARTMENT,),
                    value=Decimal(departments) if departments else None,
                ),
                self._metric(
                    key="average_spend_per_department",
                    label="Avg Spend Per Department",
                    unit="currency",
                    calculation="sum(expenses) / count(distinct department)",
                    source_fields=(
                        DashboardInsightField.EXPENSES,
                        DashboardInsightField.DEPARTMENT,
                    ),
                    value=average_department,
                ),
                self._metric(
                    key="number_of_vendors",
                    label="Number of Vendors",
                    unit="count",
                    calculation="count(distinct vendor)",
                    source_fields=(DashboardInsightField.VENDOR,),
                    value=Decimal(vendors) if vendors else None,
                ),
                self._metric(
                    key="total_expenses",
                    label="Total Expenses",
                    unit="currency",
                    calculation="sum(expenses)",
                    source_fields=(DashboardInsightField.EXPENSES,),
                    value=expenses,
                ),
            ],
            "series": [
                self._dimension_series(
                    key="monthly_trend_by_department",
                    label="Monthly Trend by Department",
                    field=DashboardInsightField.EXPENSES,
                    dimension=DashboardInsightField.DEPARTMENT,
                ),
                self._dimension_series(
                    key="monthly_trend_by_vendor",
                    label="Monthly Trend by Vendor",
                    field=DashboardInsightField.EXPENSES,
                    dimension=DashboardInsightField.VENDOR,
                ),
            ],
            "breakdowns": [],
            "tables": [],
            "status_updates": status_updates,
        }

    def render(self) -> dict[str, object]:
        missing_profiles = set(self._mappings) - self._validated_mappings
        if missing_profiles:
            raise DashboardInsightDataError("mapped_source_sheet_not_found")
        if self._mapped_row_count == 0:
            raise DashboardInsightDataError("mapped_rows_not_found")
        self._validate_required_period_coverage()

        with localcontext() as context:
            context.prec = DASHBOARD_INSIGHT_DECIMAL_PRECISION
            if self.dashboard_type is DashboardType.EXECUTIVE_SUMMARY:
                calculated = self._executive_calculated()
            elif self.dashboard_type is DashboardType.FINANCIAL_PERFORMANCE:
                calculated = self._financial_calculated()
            else:
                calculated = self._operational_calculated()
            anomalies = self._anomalies()
            risks = self._risks()
            recommendations = self._recommendations()

        return {
            "contract_version": DASHBOARD_INSIGHT_CONFIGURATION_VERSION,
            "calculation_version": DASHBOARD_INSIGHT_CALCULATION_VERSION,
            "dashboard_type": self.dashboard_type.value,
            "goals": [goal.value for goal in self.configuration.goals],
            "mapped_row_count": self._mapped_row_count,
            "calculated": calculated,
            "signals": {
                "anomalies": anomalies,
                "risks": risks,
                "recommendations": recommendations,
            },
            "explainability": {
                "calculated_sections": [
                    "kpis",
                    "series",
                    "breakdowns",
                    "tables",
                    "signals",
                ],
                "ai_generated_sections": [
                    "summary",
                    "opportunity",
                    "recommendation",
                    "disclaimer",
                ],
                "rule": (
                    "Calculated values come only from explicitly mapped immutable "
                    "report rows. AI narrative is stored separately and grounded "
                    "to this snapshot."
                ),
            },
        }


__all__ = [
    "DASHBOARD_INSIGHT_DECIMAL_PRECISION",
    "DASHBOARD_INSIGHT_MAX_DIMENSION_VALUES",
    "DASHBOARD_INSIGHT_MAX_PERIODS",
    "DASHBOARD_INSIGHT_MAX_PERIOD_DIMENSION_POINTS",
    "DASHBOARD_INSIGHT_MAX_STATUS_UPDATES",
    "DashboardInsightAccumulator",
    "DashboardInsightDataError",
    "dashboard_insight_contract_payload",
]
