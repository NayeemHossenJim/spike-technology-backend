from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index

import app.services.dashboard_insights as dashboard_insights_module
from app.main import create_app
from app.models.ai import AIConversation, AIConversationPurpose
from app.models.dashboard import (
    Dashboard,
    DashboardGoal,
    DashboardInsightField,
    DashboardSnapshot,
    DashboardType,
)
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardInsightConfiguration,
    DashboardInsightRead,
    DashboardSnapshotRead,
)
from app.services.ai_execution import (
    AI_DASHBOARD_ANALYSIS_HEADINGS,
    AI_GROUNDING_MAX_CHARACTERS,
    AIExecutionIntegrityError,
    _compact_calculated,
    _dashboard_grounding_context,
    _valid_dashboard_analysis,
)
from app.services.dashboard_insights import (
    DashboardInsightAccumulator,
    DashboardInsightDataError,
    dashboard_insight_contract_payload,
)
from app.services.dashboard_snapshots import (
    DashboardSnapshotService,
    dashboard_snapshot_content_hash,
)


def _profile(*columns: str) -> dict:
    return {
        "sheets": [
            {
                "index": 0,
                "name": "data",
                "columns": [{"name": name} for name in columns],
            }
        ]
    }


def _configuration(
    dashboard_type: DashboardType,
    job_id: UUID,
) -> DashboardInsightConfiguration:
    fields = {
        DashboardType.EXECUTIVE_SUMMARY: {
            "period": "Period",
            "location": "Location",
            "expenses": "Expenses",
            "revenue": "Revenue",
            "net_cash": "Net Cash",
            "category": "Category",
            "gross_margin": "Gross Margin",
        },
        DashboardType.FINANCIAL_PERFORMANCE: {
            "period": "Period",
            "location": "Location",
            "revenue": "Revenue",
            "spend": "Spend",
            "cash_balance": "Cash Balance",
            "budget": "Budget",
            "category": "Category",
        },
        DashboardType.OPERATIONAL_KPI: {
            "period": "Period",
            "department": "Department",
            "vendor": "Vendor",
            "expenses": "Expenses",
            "status_title": "Status Title",
            "status_detail": "Status Detail",
            "status": "Status",
            "progress": "Progress",
        },
    }[dashboard_type]
    return DashboardInsightConfiguration.model_validate(
        {
            "version": 1,
            "goals": ["reduce_expenses", "improve_forecasting"],
            "datasets": [
                {
                    "processing_job_id": str(job_id),
                    "sheet_index": 0,
                    "sheet_name": "data",
                    "columns": fields,
                }
            ],
        }
    )


def _accumulator(
    dashboard_type: DashboardType,
    rows: list[dict[str, object]],
    *,
    goals: list[str] | None = None,
) -> tuple[UUID, DashboardInsightConfiguration, dict[str, object]]:
    job_id = uuid4()
    configuration = _configuration(dashboard_type, job_id)
    if goals is not None:
        configuration_payload = configuration.model_dump(mode="json")
        configuration_payload["goals"] = goals
        configuration = DashboardInsightConfiguration.model_validate(configuration_payload)
    accumulator = DashboardInsightAccumulator(
        dashboard_type=dashboard_type,
        configuration=configuration,
    )
    column_names = tuple(configuration.datasets[0].columns.values())
    accumulator.validate_profile(
        processing_job_id=job_id,
        profile=_profile(*column_names),
    )
    for row in rows:
        accumulator.observe(
            processing_job_id=job_id,
            sheet_index=0,
            sheet_name="data",
            values=row,
        )
    insight = accumulator.render()
    validated = DashboardInsightRead.model_validate(insight)
    assert validated.model_dump(mode="json", exclude_unset=True) == insight
    return job_id, configuration, insight


def _kpis(insight: dict[str, object]) -> dict[str, dict[str, object]]:
    calculated = insight["calculated"]
    assert isinstance(calculated, dict)
    items = calculated["kpis"]
    assert isinstance(items, list)
    return {str(item["key"]): item for item in items if isinstance(item, dict)}


def test_figma_contract_has_exact_types_goals_and_mapping_fields() -> None:
    contract = dashboard_insight_contract_payload()

    assert contract["version"] == 1
    assert contract["max_goals"] == 3
    assert contract["max_datasets"] == 20
    assert contract["numeric_fields_require_period"] is True
    assert contract["required_numeric_fields_share_periods"] is True
    assert contract["outflow_rows_require_dimensions"] is True
    assert contract["reject_overlapping_measure_periods"] is True
    assert [item["key"] for item in contract["goals"]] == [goal.value for goal in DashboardGoal]
    assert [item["dashboard_type"] for item in contract["dashboard_types"]] == [
        dashboard_type.value for dashboard_type in DashboardType
    ]
    executive = contract["dashboard_types"][0]
    assert [item["key"] for item in executive["fields"]] == [
        "period",
        "location",
        "expenses",
        "revenue",
        "net_cash",
        "category",
        "gross_margin",
    ]
    assert executive["recommended_reports"] == [
        "Profit & Loss",
        "Balance Sheet",
        "General Ledger",
    ]
    assert executive["required_dataset_field_groups"] == [
        ["period", "location", "expenses", "category"],
    ]


def test_mapping_contract_rejects_missing_wrong_and_ambiguous_fields() -> None:
    job_id = uuid4()
    valid = _configuration(DashboardType.EXECUTIVE_SUMMARY, job_id)
    payload = {
        "dashboard_type": "executive_summary",
        "title": "Mapped dashboard",
        "insight_configuration": valid.model_dump(mode="json"),
    }
    assert DashboardCreate.model_validate(payload).insight_configuration == valid

    spaced_sheet = valid.model_dump(mode="json")
    spaced_sheet["datasets"][0]["sheet_name"] = " data "
    preserved = DashboardInsightConfiguration.model_validate(spaced_sheet)
    assert preserved.datasets[0].sheet_name == " data "

    missing = valid.model_dump(mode="json")
    del missing["datasets"][0]["columns"]["revenue"]
    with pytest.raises(ValidationError, match="missing required fields: revenue"):
        DashboardCreate.model_validate({**payload, "insight_configuration": missing})

    wrong_type = valid.model_dump(mode="json")
    wrong_type["datasets"][0]["columns"]["spend"] = "Spend"
    with pytest.raises(ValidationError, match="not valid for executive_summary"):
        DashboardCreate.model_validate({**payload, "insight_configuration": wrong_type})

    duplicate_source = valid.model_dump(mode="json")
    duplicate_source["datasets"][0]["columns"]["expenses"] = "Revenue"
    with pytest.raises(ValidationError, match="cannot be mapped to multiple"):
        DashboardCreate.model_validate({**payload, "insight_configuration": duplicate_source})

    split_calculation = valid.model_dump(mode="json")
    category_column = split_calculation["datasets"][0]["columns"].pop("category")
    split_calculation["datasets"].append(
        {
            "processing_job_id": str(job_id),
            "sheet_index": 1,
            "sheet_name": "categories",
            "columns": {"category": category_column},
        }
    )
    with pytest.raises(ValidationError, match="must be mapped from the same sheet"):
        DashboardCreate.model_validate({**payload, "insight_configuration": split_calculation})


def test_calculation_rejects_overlapping_measure_periods_from_different_sheets() -> None:
    job_id = uuid4()
    configuration = DashboardInsightConfiguration.model_validate(
        {
            "version": 1,
            "goals": ["reduce_expenses"],
            "datasets": [
                {
                    "processing_job_id": str(job_id),
                    "sheet_index": 0,
                    "sheet_name": "categories",
                    "columns": {
                        "period": "Period",
                        "expenses": "Expenses",
                        "location": "Location",
                        "revenue": "Revenue",
                        "net_cash": "Net Cash",
                        "category": "Category",
                        "gross_margin": "Gross Margin",
                    },
                },
                {
                    "processing_job_id": str(job_id),
                    "sheet_index": 1,
                    "sheet_name": "locations",
                    "columns": {
                        "period": "Period",
                        "expenses": "Expenses",
                        "category": "Category",
                        "location": "Location",
                    },
                },
            ],
        }
    )
    DashboardCreate.model_validate(
        {
            "dashboard_type": "executive_summary",
            "title": "Ambiguous overlap",
            "insight_configuration": configuration.model_dump(mode="json"),
        }
    )
    accumulator = DashboardInsightAccumulator(
        dashboard_type=DashboardType.EXECUTIVE_SUMMARY,
        configuration=configuration,
    )
    accumulator.validate_profile(
        processing_job_id=job_id,
        profile={
            "sheets": [
                {
                    "index": dataset.sheet_index,
                    "name": dataset.sheet_name,
                    "columns": [{"name": column_name} for column_name in dataset.columns.values()],
                }
                for dataset in configuration.datasets
            ]
        },
    )
    accumulator.observe(
        processing_job_id=job_id,
        sheet_index=0,
        sheet_name="categories",
        values={
            "Period": "2026-01",
            "Expenses": 100,
            "Location": "Dhaka",
            "Revenue": 200,
            "Net Cash": 50,
            "Category": "Rent",
            "Gross Margin": 50,
        },
    )

    with pytest.raises(DashboardInsightDataError, match="mapped_measure_period_overlap"):
        accumulator.observe(
            processing_job_id=job_id,
            sheet_index=1,
            sheet_name="locations",
            values={
                "Period": "2026-01",
                "Expenses": 100,
                "Category": "Rent",
                "Location": "Dhaka",
            },
        )


def test_executive_snapshot_is_calculated_only_from_explicit_mappings() -> None:
    _, _, insight = _accumulator(
        DashboardType.EXECUTIVE_SUMMARY,
        [
            {
                "Period": "Jan",
                "Location": "Dhaka",
                "Expenses": "$400",
                "Revenue": "$1,000",
                "Net Cash": "200",
                "Category": "Rent",
                "Gross Margin": "60%",
            },
            {
                "Period": "Feb",
                "Location": "Dhaka",
                "Expenses": "$700",
                "Revenue": "$900",
                "Net Cash": "100",
                "Category": "Operations",
                "Gross Margin": "40%",
            },
        ],
    )

    kpis = _kpis(insight)
    assert kpis["total_revenue"]["value_decimal"] == "1900"
    assert kpis["net_profit"]["value_decimal"] == "800"
    assert kpis["gross_margin"]["value_decimal"] == "50"
    assert kpis["burn_rate"]["value_decimal"] == "550"
    calculated = insight["calculated"]
    forecast = calculated["series"][1]
    assert forecast["key"] == "cash_flow_forecast"
    assert [series["key"] for series in forecast["series"]] == ["actual", "forecast"]
    assert forecast["series"][1]["points"] == [
        {"label": "Feb", "value_decimal": "100"},
        {"label": "Forecast +1", "value_decimal": "0"},
        {"label": "Forecast +2", "value_decimal": "-100"},
        {"label": "Forecast +3", "value_decimal": "-200"},
    ]
    assert calculated["breakdowns"][1]["key"] == "expenses_by_location"
    assert calculated["tables"][0]["key"] == "top_spend_drivers"
    assert insight["signals"]["anomalies"][0]["field"] == "expenses"
    assert insight["signals"]["risks"][0]["key"] == "outflow_concentration"


def test_dashboard_calculations_preserve_large_mapped_decimals_without_rounding() -> None:
    _, _, insight = _accumulator(
        DashboardType.EXECUTIVE_SUMMARY,
        [
            {
                "Period": "2026-01",
                "Location": "Dhaka",
                "Expenses": "0",
                "Revenue": "9999999999999999999999999999999999999999",
                "Net Cash": "1.2300",
                "Category": "Other",
                "Gross Margin": "50",
            },
            {
                "Period": "2026-02",
                "Location": "Dhaka",
                "Expenses": "0",
                "Revenue": "1",
                "Net Cash": "1.2300",
                "Category": "Other",
                "Gross Margin": "50",
            },
        ],
    )

    kpis = _kpis(insight)
    assert kpis["total_revenue"]["value_decimal"] == "10000000000000000000000000000000000000000"
    assert kpis["net_profit"]["value_decimal"] == "10000000000000000000000000000000000000000"
    forecast = insight["calculated"]["series"][1]["series"][1]["points"]
    assert forecast[-1]["value_decimal"] == "1.23"


def test_point_in_time_values_are_not_summed_or_silently_conflicted() -> None:
    rows = [
        {
            "Period": "2026-01",
            "Location": "Dhaka",
            "Expenses": 10,
            "Revenue": 100,
            "Net Cash": 200,
            "Category": "Rent",
            "Gross Margin": 60,
        },
        {
            "Period": "2026-01",
            "Location": "Dhaka",
            "Expenses": 20,
            "Revenue": 200,
            "Net Cash": 200,
            "Category": "Operations",
            "Gross Margin": 60,
        },
    ]
    _, _, insight = _accumulator(DashboardType.EXECUTIVE_SUMMARY, rows)

    kpis = _kpis(insight)
    assert kpis["total_revenue"]["value_decimal"] == "300"
    assert kpis["gross_margin"]["value_decimal"] == "60"
    actual = insight["calculated"]["series"][1]["series"][0]["points"]
    assert actual == [{"label": "2026-01", "value_decimal": "200"}]

    job_id = uuid4()
    configuration = _configuration(DashboardType.EXECUTIVE_SUMMARY, job_id)
    accumulator = DashboardInsightAccumulator(
        dashboard_type=DashboardType.EXECUTIVE_SUMMARY,
        configuration=configuration,
    )
    accumulator.validate_profile(
        processing_job_id=job_id,
        profile=_profile(*configuration.datasets[0].columns.values()),
    )
    accumulator.observe(
        processing_job_id=job_id,
        sheet_index=0,
        sheet_name="data",
        values=rows[0],
    )
    conflicting = {**rows[1], "Net Cash": 201}
    with pytest.raises(DashboardInsightDataError, match="point_in_time_value_ambiguous"):
        accumulator.observe(
            processing_job_id=job_id,
            sheet_index=0,
            sheet_name="data",
            values=conflicting,
        )


def test_cross_measure_calculations_reject_mismatched_period_coverage() -> None:
    with pytest.raises(DashboardInsightDataError, match="mapped_period_coverage_mismatch"):
        _accumulator(
            DashboardType.EXECUTIVE_SUMMARY,
            [
                {
                    "Period": "2026-01",
                    "Location": "Dhaka",
                    "Expenses": 100,
                    "Revenue": 200,
                    "Net Cash": 50,
                    "Category": "Rent",
                    "Gross Margin": 50,
                },
                {
                    "Period": "2026-02",
                    "Location": "Dhaka",
                    "Expenses": 120,
                    "Revenue": 210,
                    "Net Cash": None,
                    "Category": "Rent",
                    "Gross Margin": 50,
                },
            ],
        )


def test_outflow_breakdowns_reject_missing_required_dimension_values() -> None:
    with pytest.raises(
        DashboardInsightDataError,
        match="mapped_required_dimension_value_missing",
    ):
        _accumulator(
            DashboardType.FINANCIAL_PERFORMANCE,
            [
                {
                    "Period": "2026-01",
                    "Location": "Dhaka",
                    "Revenue": 1000,
                    "Spend": 700,
                    "Cash Balance": 5000,
                    "Budget": 800,
                    "Category": None,
                }
            ],
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("Revenue", "25%"),
        ("Gross Margin", "$50"),
    ],
)
def test_mapped_numeric_fields_reject_the_wrong_unit_syntax(
    column: str,
    value: str,
) -> None:
    job_id = uuid4()
    configuration = _configuration(DashboardType.EXECUTIVE_SUMMARY, job_id)
    accumulator = DashboardInsightAccumulator(
        dashboard_type=DashboardType.EXECUTIVE_SUMMARY,
        configuration=configuration,
    )
    accumulator.validate_profile(
        processing_job_id=job_id,
        profile=_profile(*configuration.datasets[0].columns.values()),
    )
    row = {
        "Period": "2026-01",
        "Location": "Dhaka",
        "Expenses": 100,
        "Revenue": 200,
        "Net Cash": 50,
        "Category": "Rent",
        "Gross Margin": 50,
    }
    row[column] = value

    with pytest.raises(DashboardInsightDataError, match="mapped_numeric_value_invalid"):
        accumulator.observe(
            processing_job_id=job_id,
            sheet_index=0,
            sheet_name="data",
            values=row,
        )


def test_financial_snapshot_has_latest_balance_budget_variance_and_risk() -> None:
    _, _, insight = _accumulator(
        DashboardType.FINANCIAL_PERFORMANCE,
        [
            {
                "Period": "2026-01",
                "Location": "Dhaka",
                "Revenue": 1000,
                "Spend": 700,
                "Cash Balance": 5000,
                "Budget": 800,
                "Category": "Marketing",
            },
            {
                "Period": "2026-02",
                "Location": "Dhaka",
                "Revenue": 900,
                "Spend": 950,
                "Cash Balance": 4500,
                "Budget": 800,
                "Category": "Marketing",
            },
        ],
    )

    kpis = _kpis(insight)
    assert kpis["total_spend"]["value_decimal"] == "1650"
    assert kpis["cash_balance"]["value_decimal"] == "4500"
    assert kpis["total_budget"]["value_decimal"] == "1600"
    table = insight["calculated"]["tables"][0]
    assert insight["calculated"]["breakdowns"][1]["key"] == "spend_by_location"
    assert table["current_period"] == "2026-02"
    assert table["rows"][0]["variance_decimal"] == "250"
    assert {item["key"] for item in insight["signals"]["risks"]} >= {"spend_above_budget"}


def test_operational_snapshot_has_department_vendor_trends_and_statuses() -> None:
    _, _, insight = _accumulator(
        DashboardType.OPERATIONAL_KPI,
        [
            {
                "Period": "Jan",
                "Department": "Engineering",
                "Vendor": "AWS",
                "Expenses": 300,
                "Status Title": "Migration",
                "Status Detail": "Phase one complete",
                "Status": "On track",
                "Progress": "75%",
            },
            {
                "Period": "Feb",
                "Department": "Marketing",
                "Vendor": "Agency",
                "Expenses": 500,
                "Status Title": "Campaign",
                "Status Detail": "Launch complete",
                "Status": "Complete",
                "Progress": 100,
            },
        ],
    )

    kpis = _kpis(insight)
    assert kpis["number_of_departments"]["value_decimal"] == "2"
    assert kpis["number_of_vendors"]["value_decimal"] == "2"
    assert kpis["total_expenses"]["value_decimal"] == "800"
    calculated = insight["calculated"]
    assert [item["key"] for item in calculated["series"]] == [
        "monthly_trend_by_department",
        "monthly_trend_by_vendor",
    ]
    assert calculated["status_updates"][0]["progress_decimal"] == "75"


def test_goal_recommendations_state_when_required_evidence_is_unavailable() -> None:
    _, _, insight = _accumulator(
        DashboardType.OPERATIONAL_KPI,
        [
            {
                "Period": "Jan",
                "Department": "Engineering",
                "Vendor": "AWS",
                "Expenses": 300,
                "Status Title": None,
                "Status Detail": None,
                "Status": None,
                "Progress": None,
            }
        ],
        goals=["improve_cash_flow", "grow_revenue", "stay_on_budget"],
    )

    recommendations = {item["goal"]: item for item in insight["signals"]["recommendations"]}
    assert "not available" in recommendations["improve_cash_flow"]["message"]
    assert "not available" in recommendations["grow_revenue"]["message"]
    assert "not available" in recommendations["stay_on_budget"]["message"]


def test_ai_grounding_is_snapshot_bound_bounded_and_excludes_raw_aggregates() -> None:
    job_id, configuration, insight = _accumulator(
        DashboardType.EXECUTIVE_SUMMARY,
        [
            {
                "Period": "Jan",
                "Location": "Dhaka",
                "Expenses": 400,
                "Revenue": 1000,
                "Net Cash": 200,
                "Category": "Rent",
                "Gross Margin": 60,
            }
        ],
    )
    snapshot_payload = {
        "format": "spike.dashboard-snapshot",
        "schema_version": 1,
        "dashboard": {
            "id": str(uuid4()),
            "type": "executive_summary",
            "title": "Grounded",
            "configuration": {"currency": "USD"},
            "insight_configuration": configuration.model_dump(mode="json"),
        },
        "source_count": 1,
        "record_count": 1,
        "sheet_count": 1,
        "sources": [
            {
                "processing_job_id": str(job_id),
                "report_upload_id": str(uuid4()),
                "filename": "finance.csv",
                "normalized_storage_version_id": "normalized-v1",
                "profile_storage_version_id": "profile-v1",
                "record_count": 1,
                "sheet_count": 1,
                "aggregate": {"raw-marker": "must-not-reach-gemini"},
            }
        ],
        "insight": insight,
    }
    snapshot = DashboardSnapshot(
        business_id=uuid4(),
        dashboard_id=uuid4(),
        version=1,
        payload=snapshot_payload,
        content_hash=dashboard_snapshot_content_hash(snapshot_payload),
        created_by_user_id=uuid4(),
    )

    rendered = _dashboard_grounding_context(snapshot)
    context = json.loads(rendered)
    public_payload = DashboardSnapshotRead.model_validate(snapshot).model_dump(
        mode="json",
        exclude_unset=True,
    )["payload"]

    assert len(rendered) <= AI_GROUNDING_MAX_CHARACTERS
    assert public_payload == snapshot_payload
    assert dashboard_snapshot_content_hash(public_payload) == snapshot.content_hash
    assert context["provenance"]["snapshot_id"] == str(snapshot.id)
    assert context["provenance"]["snapshot_content_hash"] == snapshot.content_hash
    assert context["insight"]["dashboard_type"] == "executive_summary"
    assert "must-not-reach-gemini" not in rendered
    assert context["sources"][0]["processing_job_id"] == str(job_id)

    snapshot.payload["record_count"] = 2
    with pytest.raises(AIExecutionIntegrityError):
        _dashboard_grounding_context(snapshot)


def test_ai_grounding_keeps_recent_chart_and_status_evidence() -> None:
    compact = _compact_calculated(
        {
            "kpis": [],
            "series": [
                {
                    "key": "trend",
                    "points": [
                        {"label": f"P{index}", "value_decimal": str(index)} for index in range(15)
                    ],
                }
            ],
            "status_updates": [{"title": f"Update {index}"} for index in range(8)],
        }
    )

    assert compact["series"][0]["points"][0]["label"] == "P3"
    assert compact["series"][0]["points"][-1]["label"] == "P14"
    assert compact["status_updates"][0]["title"] == "Update 3"


def test_dashboard_analysis_output_requires_exact_nonempty_sections() -> None:
    valid = """## Summary
Calculated revenue is stable.

## Opportunity
Review mapped expenses.

## Recommendation
Validate the largest calculated spend driver.

## Disclaimer
This is not an audit or a substitute for professional accounting review.
"""
    assert _valid_dashboard_analysis(valid)
    assert list(AI_DASHBOARD_ANALYSIS_HEADINGS) == [
        "## Summary",
        "## Opportunity",
        "## Recommendation",
        "## Disclaimer",
    ]
    assert not _valid_dashboard_analysis(valid.replace("## Opportunity", "## Outlook"))
    assert not _valid_dashboard_analysis(valid.replace("not an audit", "general guidance"))
    assert not _valid_dashboard_analysis(f"Preamble\n{valid}")
    assert not _valid_dashboard_analysis(valid.replace("Review mapped expenses.", "### Detail"))


def test_chart_aggregation_fails_closed_at_the_sparse_point_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_insights_module,
        "DASHBOARD_INSIGHT_MAX_PERIOD_DIMENSION_POINTS",
        1,
    )
    job_id = uuid4()
    configuration = _configuration(DashboardType.OPERATIONAL_KPI, job_id)
    accumulator = DashboardInsightAccumulator(
        dashboard_type=DashboardType.OPERATIONAL_KPI,
        configuration=configuration,
    )
    columns = tuple(configuration.datasets[0].columns.values())
    accumulator.validate_profile(
        processing_job_id=job_id,
        profile=_profile(*columns),
    )
    values = {
        "Period": "Jan",
        "Department": "Engineering",
        "Vendor": "AWS",
        "Expenses": 100,
        "Status Title": None,
        "Status Detail": None,
        "Status": None,
        "Progress": None,
    }
    with pytest.raises(DashboardInsightDataError, match="mapped_series_cardinality"):
        accumulator.observe(
            processing_job_id=job_id,
            sheet_index=0,
            sheet_name="data",
            values=values,
        )


def test_dashboard_analysis_purpose_is_closed() -> None:
    assert [item.value for item in AIConversationPurpose] == [
        "chat",
        "dashboard_analysis",
    ]
    assert DashboardInsightField.NET_CASH.value == "net_cash"


def test_snapshot_database_reads_do_not_require_an_s3_gateway() -> None:
    service = DashboardSnapshotService(object())  # type: ignore[arg-type]

    assert service.storage is None


def test_gap02_database_and_migration_contract_is_linear() -> None:
    dashboard_checks = {
        item.name for item in Dashboard.__table__.constraints if isinstance(item, CheckConstraint)
    }
    assert "ck_dashboards_insight_configuration_object" in dashboard_checks
    assert "insight_configuration" in Dashboard.__table__.columns
    assert Dashboard.__table__.c.insight_configuration.type.none_as_null is True

    conversation_fks = {
        item.name
        for item in AIConversation.__table__.constraints
        if isinstance(item, ForeignKeyConstraint)
    }
    conversation_indexes = {
        item.name for item in AIConversation.__table__.indexes if isinstance(item, Index)
    }
    assert "fk_ai_conversations_dashboard_snapshot_business" in conversation_fks
    assert "ux_ai_conversations_dashboard_analysis" in conversation_indexes

    project_root = Path(__file__).resolve().parents[2]
    migration = (
        project_root / "alembic" / "versions" / "0016_gap02_grounded_dashboards.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0016_gap02_grounded_dashboards"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0015_m7_ai_credit_adjustments"' in (
        migration
    )
    assert "ck_dashboards_insight_configuration_object" in migration
    assert "fk_ai_conversations_dashboard_snapshot_business" in migration
    assert "ux_ai_conversations_dashboard_analysis" in migration
    assert "postgresql_where" in migration


def test_gap02_openapi_exposes_mapping_snapshot_and_grounded_analysis_paths() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    assert "/api/v1/dashboards/contracts" in paths
    assert "/api/v1/dashboards/sources/{processing_job_id}/schema" in paths
    assert "/api/v1/dashboards/{dashboard_id}/snapshots/{snapshot_id}" in paths
    analysis_path = "/api/v1/dashboards/{dashboard_id}/snapshots/{snapshot_id}/analysis"
    assert analysis_path in paths
    analysis = paths[analysis_path]["post"]
    headers = {item["name"]: item for item in analysis["parameters"] if item["in"] == "header"}
    assert headers["Idempotency-Key"]["required"] is True
    assert {"200", "201", "202", "402", "403", "404", "409", "429", "502", "503"} <= set(
        analysis["responses"]
    )
