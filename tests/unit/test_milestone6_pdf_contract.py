from __future__ import annotations

from uuid import uuid4

from app.main import create_app
from app.models.base import utc_now
from app.models.dashboard import DashboardSnapshot
from app.services.dashboard_pdf import (
    build_dashboard_pdf_html,
    dashboard_pdf_filename,
)


def snapshot_with_payload() -> DashboardSnapshot:
    business_id = uuid4()
    dashboard_id = uuid4()

    return DashboardSnapshot(
        business_id=business_id,
        dashboard_id=dashboard_id,
        version=3,
        schema_version=1,
        payload={
            "format": "spike.dashboard-snapshot",
            "schema_version": 1,
            "dashboard": {
                "id": str(dashboard_id),
                "type": "executive_summary",
                "title": "<script>alert('x')</script>",
                "configuration": {
                    "currency": "<b>USD</b>",
                },
            },
            "source_count": 1,
            "record_count": 2,
            "sheet_count": 1,
            "sources": [
                {
                    "processing_job_id": str(uuid4()),
                    "report_upload_id": str(uuid4()),
                    "filename": "<img src=x onerror=alert(1)>.csv",
                    "record_count": 2,
                    "sheet_count": 1,
                    "aggregate": {
                        "record_count": 2,
                        "sheet_count": 1,
                        "sheets": [
                            {
                                "sheet_index": 0,
                                "sheet_name": "Sheet1",
                                "record_count": 2,
                                "columns": [
                                    {
                                        "name": "revenue",
                                        "non_null_count": 2,
                                        "null_count": 0,
                                        "numeric": {
                                            "count": 2,
                                            "sum_decimal": "2500",
                                            "min_decimal": "1200",
                                            "max_decimal": "1300",
                                            "average_decimal": "1250",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                }
            ],
        },
        content_hash="a" * 64,
        created_by_user_id=uuid4(),
        created_at=utc_now(),
    )


def test_pdf_html_escapes_snapshot_and_business_text() -> None:
    rendered = build_dashboard_pdf_html(
        snapshot=snapshot_with_payload(),
        business_name="<script>tenant</script>",
        business_industry="Technology & Analytics",
    )

    assert "<script>alert('x')</script>" not in rendered
    assert "<script>tenant</script>" not in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered

    assert "&lt;script&gt;" in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;.csv" in rendered
    assert "Technology &amp; Analytics" in rendered
    assert "2500" in rendered
    assert "1250" in rendered


def test_pdf_filename_is_attachment_safe_and_versioned() -> None:
    dashboard_id = uuid4()

    filename = dashboard_pdf_filename(
        title='  Revenue / CFO: "Quarter"  ',
        dashboard_id=dashboard_id,
        version=7,
    )

    assert filename == "revenue-cfo-quarter-v7.pdf"
    assert '"' not in filename
    assert "/" not in filename
    assert "\\" not in filename


def test_pdf_export_openapi_contract() -> None:
    schema = create_app().openapi()

    path = "/api/v1/dashboards/{dashboard_id}/export/pdf"

    assert path in schema["paths"]
    assert "get" in schema["paths"][path]

    response = schema["paths"][path]["get"]["responses"]["200"]

    assert "application/pdf" in response["content"]
