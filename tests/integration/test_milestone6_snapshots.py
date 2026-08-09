from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import func, select

from app.models.dashboard import (
    DashboardSnapshot,
    DashboardSnapshotSource,
)
from app.models.processing import ReportProcessingJob
from app.services.processing import (
    ReportProcessingOutput,
    ReportProcessingService,
)
from app.services.report_parser import (
    ReportParsingContext,
    parse_report_content,
)
from app.services.s3_storage import (
    S3ObjectNotFoundError,
    get_s3_upload_gateway,
)
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import bearer
from tests.integration.test_milestone3_uploads import onboard_owner
from tests.integration.test_milestone4_processing import (
    create_processing_job,
    create_verified_upload,
)
from tests.integration.test_milestone6_dashboards import (
    dashboard_payload,
    grant_dashboards,
)


@dataclass
class SnapshotStorage:
    objects: dict[tuple[str, str, str], bytes] = field(default_factory=dict)
    reads: list[tuple[str, str, str]] = field(default_factory=list)

    async def read_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        max_bytes: int,
    ) -> bytes:
        self.reads.append((bucket, key, version_id))
        try:
            content = self.objects[(bucket, key, version_id)]
        except KeyError as exc:
            raise S3ObjectNotFoundError from exc

        if len(content) > max_bytes:
            raise AssertionError("Fixture artifact exceeded requested max_bytes.")
        return content


async def completed_processing_source(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    business_id: UUID,
    user_id: UUID,
    filename: str,
    content: bytes,
    storage: SnapshotStorage,
) -> UUID:
    upload = await create_verified_upload(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename=filename,
        content_size=len(content),
    )

    job_id = await create_processing_job(
        session_factory,
        business_id=business_id,
        upload=upload,
    )

    parsed = parse_report_content(
        context=ReportParsingContext(
            job_id=job_id,
            business_id=business_id,
            report_upload_id=upload.id,
            original_filename=filename,
            file_extension="csv",
            content_type="text/csv",
        ),
        content=content,
    )

    bucket = "spike-private-test-results"
    normalized_key = f"report-uploads/{business_id}/processed/{job_id}/normalized.v1.jsonl.gz"
    profile_key = f"report-uploads/{business_id}/processed/{job_id}/profile.v1.json"
    normalized_version = f"normalized-{uuid4()}"
    profile_version = f"profile-{uuid4()}"

    storage.objects[(bucket, normalized_key, normalized_version)] = parsed.normalized_content
    storage.objects[(bucket, profile_key, profile_version)] = parsed.profile_content

    async with session_factory() as session:
        service = ReportProcessingService(session=session)
        claim = await service.claim_job(job_id)
        assert claim is not None

        completed = await service.complete_job(
            job_id,
            lease_token=claim.lease_token,
            output=ReportProcessingOutput(
                storage_bucket=bucket,
                normalized_storage_key=normalized_key,
                normalized_storage_version_id=(normalized_version),
                normalized_etag=(f"normalized-etag-{uuid4()}"),
                normalized_size_bytes=len(parsed.normalized_content),
                profile_storage_key=profile_key,
                profile_storage_version_id=profile_version,
                profile_etag=f"profile-etag-{uuid4()}",
                record_count=parsed.record_count,
                sheet_count=parsed.sheet_count,
            ),
        )
        assert completed

    return job_id


async def create_dashboard(
    client: AsyncClient,
    *,
    token: str,
    title: str,
) -> dict:
    response = await client.post(
        "/api/v1/dashboards",
        headers=bearer(token),
        json=dashboard_payload(title),
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.integration
async def test_snapshot_materializes_immutable_m4_artifacts_and_replays(
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
        email="m6-snapshot@example.com",
        business_name="M6 Snapshot Tenant",
    )
    await grant_dashboards(
        session_factory,
        onboarding,
        limit=2,
    )

    dashboard = await create_dashboard(
        client,
        token=token,
        title="Executive Snapshot",
    )

    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])

    first_job = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="january.csv",
        content=(b"month,revenue,opex\nJan,1200,500\nFeb,1300,550\n"),
        storage=storage,
    )
    second_job = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="march.csv",
        content=(b"month,revenue,opex\nMar,1500,600\n"),
        storage=storage,
    )

    created = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
        json={
            # Intentionally reverse IDs; service canonicalizes
            # the logical source set.
            "source_processing_job_ids": [
                str(second_job),
                str(first_job),
            ]
        },
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["version"] == 1
    assert payload["schema_version"] == 1
    assert payload["payload"]["format"] == ("spike.dashboard-snapshot")
    assert payload["payload"]["record_count"] == 3
    assert payload["payload"]["sheet_count"] == 2
    assert len(payload["sources"]) == 2

    sources = payload["payload"]["sources"]
    assert [item["processing_job_id"] for item in sources] == sorted(
        [str(first_job), str(second_job)]
    )

    revenue_stats = [
        column["numeric"]
        for source in sources
        for sheet in source["aggregate"]["sheets"]
        for column in sheet["columns"]
        if column["name"] == "revenue"
    ]
    assert {item["sum_decimal"] for item in revenue_stats if item is not None} == {"2500", "1500"}

    replay = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
        json={
            "source_processing_job_ids": [
                str(first_job),
                str(second_job),
            ]
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == payload["id"]
    assert replay.json()["content_hash"] == (payload["content_hash"])

    latest = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots/latest"),
        headers=bearer(token),
    )
    history = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
    )

    assert latest.status_code == 200
    assert latest.json()["id"] == payload["id"]
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["version"] == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DashboardSnapshot)) == 1
        assert await session.scalar(select(func.count()).select_from(DashboardSnapshotSource)) == 2


@pytest.mark.integration
async def test_dashboard_change_creates_next_snapshot_version(
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
        email="m6-snapshot-version@example.com",
        business_name="M6 Snapshot Version",
    )
    await grant_dashboards(
        session_factory,
        onboarding,
        limit=1,
    )

    dashboard = await create_dashboard(
        client,
        token=token,
        title="Versioned Dashboard",
    )

    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    job_id = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="versioned.csv",
        content=b"month,revenue\nJan,100\n",
        storage=storage,
    )

    first = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )
    assert first.status_code == 201
    assert first.json()["version"] == 1

    updated = await client.patch(
        f"/api/v1/dashboards/{dashboard['id']}",
        headers=bearer(token),
        json={
            "configuration": {
                "currency": "BDT",
                "period": "monthly",
            }
        },
    )
    assert updated.status_code == 200

    second = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
        json={"source_processing_job_ids": [str(job_id)]},
    )

    assert second.status_code == 201
    assert second.json()["version"] == 2
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["content_hash"] != (first.json()["content_hash"])

    history = await client.get(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token),
    )
    assert [item["version"] for item in history.json()["items"]] == [2, 1]


@pytest.mark.integration
async def test_snapshot_sources_are_tenant_isolated_and_completed_only(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = SnapshotStorage()
    app.dependency_overrides[get_s3_upload_gateway] = lambda: storage

    token_a, onboarding_a = await onboard_owner(
        client,
        email_sender,
        email="m6-snapshot-a@example.com",
        business_name="M6 Snapshot A",
    )
    token_b, onboarding_b = await onboard_owner(
        client,
        email_sender,
        email="m6-snapshot-b@example.com",
        business_name="M6 Snapshot B",
    )

    await grant_dashboards(
        session_factory,
        onboarding_a,
        limit=1,
    )

    dashboard = await create_dashboard(
        client,
        token=token_a,
        title="Tenant Safe Snapshot",
    )

    business_b = UUID(onboarding_b["business"]["id"])
    user_b = UUID(onboarding_b["role_assignment"]["user_id"])
    foreign_job = await completed_processing_source(
        session_factory,
        business_id=business_b,
        user_id=user_b,
        filename="foreign.csv",
        content=b"month,revenue\nJan,100\n",
        storage=storage,
    )

    foreign = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token_a),
        json={"source_processing_job_ids": [str(foreign_job)]},
    )
    assert foreign.status_code == 404
    assert foreign.json() == {"detail": "Dashboard source not found."}

    business_a = UUID(onboarding_a["business"]["id"])
    user_a = UUID(onboarding_a["role_assignment"]["user_id"])
    upload = await create_verified_upload(
        session_factory,
        business_id=business_a,
        user_id=user_a,
        filename="queued.csv",
        content_size=22,
    )
    queued_job = await create_processing_job(
        session_factory,
        business_id=business_a,
        upload=upload,
    )

    not_ready = await client.post(
        (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
        headers=bearer(token_a),
        json={"source_processing_job_ids": [str(queued_job)]},
    )

    assert not_ready.status_code == 409
    assert not_ready.json() == {
        "detail": ("Dashboard sources must be completed report-processing jobs.")
    }


@pytest.mark.integration
async def test_concurrent_identical_snapshot_requests_persist_once(
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
        email="m6-snapshot-race@example.com",
        business_name="M6 Snapshot Race",
    )
    await grant_dashboards(
        session_factory,
        onboarding,
        limit=1,
    )

    dashboard = await create_dashboard(
        client,
        token=token,
        title="Concurrent Snapshot",
    )

    business_id = UUID(onboarding["business"]["id"])
    user_id = UUID(onboarding["role_assignment"]["user_id"])
    job_id = await completed_processing_source(
        session_factory,
        business_id=business_id,
        user_id=user_id,
        filename="race.csv",
        content=b"month,revenue\nJan,100\n",
        storage=storage,
    )

    async def materialize():
        return await client.post(
            (f"/api/v1/dashboards/{dashboard['id']}/snapshots"),
            headers=bearer(token),
            json={"source_processing_job_ids": [str(job_id)]},
        )

    first, second = await asyncio.gather(
        materialize(),
        materialize(),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 201]
    assert first.json()["id"] == second.json()["id"]

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DashboardSnapshot)) == 1
        assert await session.scalar(select(func.count()).select_from(DashboardSnapshotSource)) == 1

        snapshot = (await session.execute(select(DashboardSnapshot))).scalar_one()
        source = (await session.execute(select(DashboardSnapshotSource))).scalar_one()
        job = await session.get(
            ReportProcessingJob,
            job_id,
        )

        assert job is not None
        assert source.processing_job_id == job.id
        assert source.normalized_storage_version_id == job.normalized_storage_version_id
        assert source.profile_storage_version_id == job.profile_storage_version_id
        assert snapshot.version == 1
