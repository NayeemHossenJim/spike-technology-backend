from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.models.ai import AIConversation, AIConversationPurpose
from app.services.dashboard_snapshots import dashboard_snapshot_content_hash
from app.services.s3_storage import get_s3_upload_gateway
from tests.conftest import InMemoryEmailSender, InMemoryGeminiGateway
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import onboard_owner
from tests.integration.test_milestone6_dashboards import grant_dashboards
from tests.integration.test_milestone6_snapshots import (
    SnapshotStorage,
    completed_processing_source,
)


def _insight_configuration(job_id: UUID) -> dict:
    return {
        "version": 1,
        "goals": [
            "improve_cash_flow",
            "reduce_expenses",
            "improve_forecasting",
        ],
        "datasets": [
            {
                "processing_job_id": str(job_id),
                "sheet_index": 0,
                "sheet_name": "data",
                "columns": {
                    "period": "Period",
                    "location": "Location",
                    "expenses": "Expenses",
                    "revenue": "Revenue",
                    "net_cash": "Net Cash",
                    "category": "Category",
                    "gross_margin": "Gross Margin",
                },
            }
        ],
    }


@pytest.mark.integration
async def test_figma_generate_insight_flow_is_report_grounded_end_to_end(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    gemini_gateway: InMemoryGeminiGateway,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = SnapshotStorage()
    app.dependency_overrides[get_s3_upload_gateway] = lambda: storage

    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="gap02-grounded@example.com",
        business_name="Gap 02 Grounded Tenant",
    )
    await grant_dashboards(session_factory, onboarding, limit=2)
    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    job_id = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="executive.csv",
        content=(
            b"Period,Location,Expenses,Revenue,Net Cash,Category,Gross Margin\n"
            b"2026-01,Dhaka,400,1000,200,Rent,60\n"
            b"2026-02,Dhaka,700,900,100,Operations,40\n"
        ),
        storage=storage,
    )

    contract = await client.get(
        "/api/v1/dashboards/contracts",
        headers=bearer(token),
    )
    assert contract.status_code == 200
    assert contract.json()["max_goals"] == 3
    assert [item["dashboard_type"] for item in contract.json()["dashboard_types"]] == [
        "executive_summary",
        "financial_performance",
        "operational_kpi",
    ]

    source_schema = await client.get(
        f"/api/v1/dashboards/sources/{job_id}/schema",
        headers=bearer(token),
    )
    assert source_schema.status_code == 200
    source_payload = source_schema.json()
    assert source_payload["filename"] == "executive.csv"
    assert [item["name"] for item in source_payload["sheets"][0]["columns"]] == [
        "Period",
        "Location",
        "Expenses",
        "Revenue",
        "Net Cash",
        "Category",
        "Gross Margin",
    ]
    assert "values" not in source_schema.text

    created = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token),
        json={
            "dashboard_type": "executive_summary",
            "title": "Executive Insight",
            "configuration": {"currency": "USD", "period": "monthly"},
            "insight_configuration": _insight_configuration(job_id),
        },
    )
    assert created.status_code == 201
    dashboard = created.json()
    assert dashboard["insight_configuration"]["version"] == 1

    materialized = await client.post(
        f"/api/v1/dashboards/{dashboard['id']}/snapshots",
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )
    assert materialized.status_code == 201
    snapshot = materialized.json()
    assert dashboard_snapshot_content_hash(snapshot["payload"]) == snapshot["content_hash"]
    insight = snapshot["payload"]["insight"]
    assert insight["dashboard_type"] == "executive_summary"
    assert insight["mapped_row_count"] == 2
    assert {item["key"]: item["value_decimal"] for item in insight["calculated"]["kpis"]} == {
        "total_revenue": "1900",
        "net_profit": "800",
        "gross_margin": "50",
        "burn_rate": "550",
    }
    assert insight["explainability"]["ai_generated_sections"] == [
        "summary",
        "opportunity",
        "recommendation",
        "disclaimer",
    ]
    assert snapshot["ai_analysis"] is None

    analysis = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots/{snapshot['id']}/analysis"),
        headers={
            **bearer(token),
            "Idempotency-Key": "gap02-analysis-0001",
        },
    )
    assert analysis.status_code == 201
    analysis_payload = analysis.json()
    assert analysis_payload["conversation"]["purpose"] == "dashboard_analysis"
    assert analysis_payload["conversation"]["dashboard_snapshot_id"] == snapshot["id"]
    assert analysis_payload["assistant_message"]["content"].startswith("## Summary")
    assert "## Disclaimer" in analysis_payload["assistant_message"]["content"]
    assert analysis_payload["usage"]["remaining"] == 14
    assert len(gemini_gateway.calls) == 1
    prompt, system_instruction = gemini_gateway.calls[0]
    assert "Immutable dashboard snapshot context (JSON)" in prompt
    assert snapshot["id"] in prompt
    assert snapshot["content_hash"] in prompt
    assert "dashboard snapshot context as the only authority" in system_instruction

    replay = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots/{snapshot['id']}/analysis"),
        headers={
            **bearer(token),
            "Idempotency-Key": "gap02-analysis-0001",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(gemini_gateway.calls) == 1

    detail = await client.get(
        f"/api/v1/dashboards/{dashboard['id']}/snapshots/{snapshot['id']}",
        headers=bearer(token),
    )
    assert detail.status_code == 200
    assert detail.json()["ai_analysis"]["status"] == "completed"
    assert detail.json()["ai_analysis"]["content"].startswith("## Summary")

    async with session_factory() as session:
        conversation = (
            await session.execute(
                select(AIConversation).where(
                    AIConversation.dashboard_snapshot_id == UUID(snapshot["id"])
                )
            )
        ).scalar_one()
        assert AIConversationPurpose(conversation.purpose) is (
            AIConversationPurpose.DASHBOARD_ANALYSIS
        )

    gemini_gateway.response_text_override = "Ungrounded response without required sections."
    invalid_provider_response = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots/{snapshot['id']}/analysis"),
        headers={
            **bearer(token),
            "Idempotency-Key": "gap02-analysis-invalid-provider",
        },
    )
    assert invalid_provider_response.status_code == 502
    assert invalid_provider_response.json() == {
        "detail": "The AI provider returned an invalid response."
    }
    invalid_prompt, _ = gemini_gateway.calls[1]
    assert "Calculated dashboard evidence was reviewed." not in invalid_prompt

    usage = await client.get("/api/v1/ai/usage", headers=bearer(token))
    assert usage.status_code == 200
    assert usage.json()["current"]["consumed_count"] == 1
    assert usage.json()["current"]["reserved_count"] == 0
    assert usage.json()["current"]["remaining"] == 14

    failed_detail = await client.get(
        f"/api/v1/dashboards/{dashboard['id']}/snapshots/{snapshot['id']}",
        headers=bearer(token),
    )
    assert failed_detail.status_code == 200
    assert failed_detail.json()["ai_analysis"]["status"] == "failed"
    assert failed_detail.json()["ai_analysis"]["error_code"] == "provider_invalid"


@pytest.mark.integration
async def test_analysis_rejects_legacy_snapshot_and_mapping_rejects_wrong_header(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = SnapshotStorage()
    app.dependency_overrides[get_s3_upload_gateway] = lambda: storage
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="gap02-rejections@example.com",
        business_name="Gap 02 Rejection Tenant",
    )
    await grant_dashboards(session_factory, onboarding, limit=2)
    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    job_id = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="legacy.csv",
        content=b"Period,Revenue\n2026-01,1000\n",
        storage=storage,
    )

    legacy_dashboard = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token),
        json={
            "dashboard_type": "executive_summary",
            "title": "Legacy Dashboard",
            "configuration": {},
        },
    )
    assert legacy_dashboard.status_code == 201
    legacy_snapshot = await client.post(
        f"/api/v1/dashboards/{legacy_dashboard.json()['id']}/snapshots",
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )
    assert legacy_snapshot.status_code == 201

    rejected_analysis = await client.post(
        (
            f"/api/v1/dashboards/{legacy_dashboard.json()['id']}/snapshots/"
            f"{legacy_snapshot.json()['id']}/analysis"
        ),
        headers={
            **bearer(token),
            "Idempotency-Key": "gap02-analysis-rejected",
        },
    )
    assert rejected_analysis.status_code == 409
    assert rejected_analysis.json() == {
        "detail": "Dashboard analysis requires a typed dashboard snapshot."
    }

    invalid_mapping = _insight_configuration(job_id)
    invalid_mapping["datasets"][0]["columns"]["gross_margin"] = "Missing Header"
    typed_dashboard = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token),
        json={
            "dashboard_type": "executive_summary",
            "title": "Invalid Mapping",
            "configuration": {},
            "insight_configuration": invalid_mapping,
        },
    )
    assert typed_dashboard.status_code == 201
    rejected_snapshot = await client.post(
        f"/api/v1/dashboards/{typed_dashboard.json()['id']}/snapshots",
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )
    assert rejected_snapshot.status_code == 409
    assert rejected_snapshot.json() == {
        "detail": "Processed report data is invalid or inconsistent."
    }

    unknown_source = await client.get(
        f"/api/v1/dashboards/sources/{uuid4()}/schema",
        headers=bearer(token),
    )
    assert unknown_source.status_code == 404
