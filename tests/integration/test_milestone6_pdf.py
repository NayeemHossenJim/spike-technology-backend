from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.models.dashboard import DashboardSnapshot
from app.services.dashboard_pdf import (
    DashboardPDFRendererError,
    get_dashboard_pdf_renderer,
)
from app.services.s3_storage import (
    get_s3_upload_gateway,
)
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import onboard_owner
from tests.integration.test_milestone6_dashboards import (
    grant_dashboards,
)
from tests.integration.test_milestone6_snapshots import (
    SnapshotStorage,
    completed_processing_source,
    create_dashboard,
)


@dataclass
class FakePDFRenderer:
    fail: bool = False
    calls: list[
        tuple[
            DashboardSnapshot,
            str,
            object | None,
        ]
    ] = field(default_factory=list)

    def render(
        self,
        *,
        snapshot: DashboardSnapshot,
        business_name: str,
        business_industry: object | None,
    ) -> bytes:
        self.calls.append(
            (
                snapshot,
                business_name,
                business_industry,
            )
        )

        if self.fail:
            raise DashboardPDFRendererError("Synthetic PDF failure.")

        return b"%PDF-1.7\nSpike deterministic integration test PDF\n%%EOF"


async def prepare_snapshot(
    *,
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
    storage: SnapshotStorage,
    email: str,
    business_name: str,
) -> tuple[str, dict, dict]:
    app.dependency_overrides[get_s3_upload_gateway] = lambda: storage

    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email=email,
        business_name=business_name,
    )

    await grant_dashboards(
        session_factory,
        onboarding,
        limit=1,
    )

    dashboard = await create_dashboard(
        client,
        token=token,
        title="Executive Export",
    )

    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])

    job_id = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="financial-report.csv",
        content=(b"month,revenue,opex\nJan,1200,500\nFeb,1300,550\n"),
        storage=storage,
    )

    materialized = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )

    assert materialized.status_code == 201

    return token, onboarding, dashboard


@pytest.mark.integration
async def test_pdf_export_uses_latest_snapshot_without_rereading_s3(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = SnapshotStorage()
    renderer = FakePDFRenderer()

    token, onboarding, dashboard = await prepare_snapshot(
        app=app,
        client=client,
        email_sender=email_sender,
        session_factory=session_factory,
        storage=storage,
        email="m6-pdf@example.com",
        business_name="M6 PDF Tenant",
    )

    reads_before_export = list(storage.reads)

    app.dependency_overrides[get_dashboard_pdf_renderer] = lambda: renderer

    response = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/export/pdf"),
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == ("application/pdf")
    assert response.content.startswith(b"%PDF-")
    assert (
        response.headers["content-disposition"] == 'attachment; filename="executive-export-v1.pdf"'
    )
    assert response.headers["x-dashboard-snapshot-version"] == "1"
    assert len(response.headers["x-dashboard-snapshot-hash"]) == 64

    assert storage.reads == reads_before_export
    assert len(renderer.calls) == 1

    snapshot, business_name, _ = renderer.calls[0]

    assert snapshot.version == 1
    assert business_name == "M6 PDF Tenant"
    assert snapshot.business_id == UUID(onboarding["business"]["id"])


@pytest.mark.integration
async def test_pdf_export_hides_foreign_dashboard_and_requires_snapshot(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    renderer = FakePDFRenderer()

    app.dependency_overrides[get_dashboard_pdf_renderer] = lambda: renderer

    token_a, onboarding_a = await onboard_owner(
        client,
        email_sender,
        email="m6-pdf-empty-a@example.com",
        business_name="M6 PDF Empty A",
    )
    token_b, _ = await onboard_owner(
        client,
        email_sender,
        email="m6-pdf-empty-b@example.com",
        business_name="M6 PDF Empty B",
    )

    await grant_dashboards(
        session_factory,
        onboarding_a,
        limit=1,
    )

    dashboard = await create_dashboard(
        client,
        token=token_a,
        title="No Snapshot Yet",
    )

    missing_snapshot = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/export/pdf"),
        headers=bearer(token_a),
    )

    foreign = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/export/pdf"),
        headers=bearer(token_b),
    )

    assert missing_snapshot.status_code == 404
    assert missing_snapshot.json() == {"detail": "Dashboard snapshot not found."}

    assert foreign.status_code == 404
    assert foreign.json() == {"detail": "Dashboard not found."}

    assert renderer.calls == []


@pytest.mark.integration
async def test_pdf_renderer_failure_is_safe_503(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = SnapshotStorage()
    renderer = FakePDFRenderer(fail=True)

    token, _, dashboard = await prepare_snapshot(
        app=app,
        client=client,
        email_sender=email_sender,
        session_factory=session_factory,
        storage=storage,
        email="m6-pdf-failure@example.com",
        business_name="M6 PDF Failure",
    )

    app.dependency_overrides[get_dashboard_pdf_renderer] = lambda: renderer

    response = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/export/pdf"),
        headers=bearer(token),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": ("Dashboard PDF export is temporarily unavailable.")}
