from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlmodel import select

from app.core.config import Settings, get_settings
from app.models.base import utc_now
from app.models.upload import (
    ReportUpload,
    ReportUploadBatch,
    ReportUploadBatchStatus,
    ReportUploadStatus,
)
from app.services.s3_storage import (
    PresignedPost,
    S3ObjectNotFoundError,
    StoredS3Object,
    get_s3_upload_gateway,
)
from tests.conftest import InMemoryEmailSender
from tests.integration.test_milestone1_foundation import (
    bearer,
    register_verify_and_login,
)


@dataclass
class FakeS3UploadGateway:
    presign_calls: list[dict[str, object]] = field(default_factory=list)
    objects: dict[str, tuple[StoredS3Object, bytes]] = field(default_factory=dict)
    deleted_versions: list[tuple[str, str]] = field(default_factory=list)
    security_checks: list[str] = field(default_factory=list)

    async def ensure_bucket_security(self, bucket: str) -> None:
        self.security_checks.append(bucket)

    async def create_presigned_post(self, **kwargs) -> PresignedPost:
        self.presign_calls.append(dict(kwargs))
        return PresignedPost(
            url="https://spike-private-test-uploads.s3.amazonaws.com",
            fields={
                "key": str(kwargs["key"]),
                "Content-Type": str(kwargs["content_type"]),
                "Cache-Control": "no-store",
                "success_action_status": "201",
                "x-amz-server-side-encryption": "AES256",
                "x-amz-meta-business-id": str(kwargs["business_id"]),
                "x-amz-meta-batch-id": str(kwargs["batch_id"]),
                "x-amz-meta-upload-id": str(kwargs["upload_id"]),
                "x-amz-meta-expected-size": str(kwargs["expected_size_bytes"]),
                "policy": "fake-signed-policy",
                "x-amz-signature": "fake-signature",
            },
        )

    async def head_object(self, *, bucket: str, key: str) -> StoredS3Object:
        del bucket
        if key not in self.objects:
            raise S3ObjectNotFoundError
        return self.objects[key][0]

    async def read_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        max_bytes: int,
    ) -> bytes:
        del bucket
        if key not in self.objects or self.objects[key][0].version_id != version_id:
            raise S3ObjectNotFoundError
        content = self.objects[key][1]
        assert len(content) <= max_bytes
        return content

    async def delete_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
    ) -> None:
        del bucket
        self.deleted_versions.append((key, version_id))
        self.objects.pop(key, None)

    def add_uploaded_object(
        self,
        *,
        file_payload: dict,
        content: bytes,
        content_type: str | None = None,
        content_length: int | None = None,
    ) -> None:
        fields = file_payload["upload"]["fields"]
        key = fields["key"]
        version_id = f"version-{file_payload['id']}"
        self.objects[key] = (
            StoredS3Object(
                content_length=len(content) if content_length is None else content_length,
                content_type=content_type or file_payload["content_type"],
                cache_control="no-store",
                server_side_encryption="AES256",
                metadata={
                    "business-id": fields["x-amz-meta-business-id"],
                    "batch-id": fields["x-amz-meta-batch-id"],
                    "upload-id": fields["x-amz-meta-upload-id"],
                    "expected-size": fields["x-amz-meta-expected-size"],
                },
                etag=f"etag-{file_payload['id']}",
                version_id=version_id,
                last_modified=utc_now(),
            ),
            content,
        )


def configured_upload_settings() -> Settings:
    return get_settings().model_copy(
        update={
            "s3_uploads_enabled": True,
            "aws_region": "us-east-1",
            "s3_upload_bucket": "spike-private-test-uploads",
            "s3_upload_prefix": "report-uploads",
            "s3_presigned_post_expire_minutes": 10,
        }
    )


def configure_upload_app(app, gateway: FakeS3UploadGateway) -> None:
    settings = configured_upload_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_s3_upload_gateway] = lambda: gateway


def minimal_xlsx() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                "<Types><Override PartName='/xl/workbook.xml' "
                "ContentType='application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet.main+xml'/></Types>"
            ),
        )
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return output.getvalue()


async def onboard_owner(
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    *,
    email: str,
    business_name: str,
) -> tuple[str, dict]:
    token = await register_verify_and_login(
        client,
        email_sender,
        email=email,
    )
    onboarding = await client.post(
        "/api/v1/businesses",
        headers=bearer(token),
        json={"name": business_name},
    )
    assert onboarding.status_code == 201
    return token, onboarding.json()


@pytest.mark.integration
async def test_presigned_batch_is_tenant_owned_and_completes_only_verified_files(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeS3UploadGateway()
    token, onboarding = await onboard_owner(
        client,
        email_sender,
        email="secure-uploads@example.com",
        business_name="Secure Upload Tenant",
    )
    csv_content = b"month,revenue\nJan,1200\n"
    xlsx_content = minimal_xlsx()

    disabled = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "sales.2026.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(csv_content),
                }
            ]
        },
    )
    assert disabled.status_code == 503
    assert disabled.json() == {"detail": "Report uploads are not configured."}

    configure_upload_app(app, gateway)
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "sales.2026.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(csv_content),
                },
                {
                    "filename": "forecast.xlsx",
                    "content_type": (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                    "size_bytes": len(xlsx_content),
                },
            ]
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["status"] == "pending"
    assert len(payload["files"]) == 2
    assert gateway.security_checks == ["spike-private-test-uploads"]

    business_id = onboarding["business"]["id"]
    uploader_user_id = onboarding["role_assignment"]["user_id"]
    for file_payload, presign_call in zip(
        payload["files"],
        gateway.presign_calls,
        strict=True,
    ):
        fields = file_payload["upload"]["fields"]
        assert file_payload["status"] == "pending"
        assert file_payload["actual_size_bytes"] is None
        assert fields["x-amz-meta-business-id"] == business_id
        assert fields["x-amz-meta-batch-id"] == payload["id"]
        assert fields["x-amz-meta-upload-id"] == file_payload["id"]
        assert fields["x-amz-server-side-encryption"] == "AES256"
        assert "acl" not in fields
        assert str(file_payload["original_filename"]) not in str(presign_call["key"])
        assert str(presign_call["key"]).startswith(f"report-uploads/{business_id}/{payload['id']}/")

    disabled_settings = configured_upload_settings().model_copy(
        update={"s3_uploads_enabled": False}
    )
    app.dependency_overrides[get_settings] = lambda: disabled_settings
    disabled_completion = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert disabled_completion.status_code == 503
    assert disabled_completion.json() == {"detail": "Report uploads are not configured."}

    configure_upload_app(app, gateway)
    gateway.add_uploaded_object(file_payload=payload["files"][0], content=csv_content)
    gateway.add_uploaded_object(file_payload=payload["files"][1], content=xlsx_content)
    completed = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert completed.status_code == 200
    completed_payload = completed.json()
    assert completed_payload["status"] == "complete"
    assert completed_payload["completed_at"] is not None
    assert [item["id"] for item in completed_payload["files"]] == [
        item["id"] for item in payload["files"]
    ]
    assert [item["status"] for item in completed_payload["files"]] == [
        "uploaded",
        "uploaded",
    ]
    assert [item["actual_size_bytes"] for item in completed_payload["files"]] == [
        len(csv_content),
        len(xlsx_content),
    ]

    repeated = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert repeated.status_code == 200
    assert repeated.json() == completed_payload

    async with session_factory() as session:
        batch = (await session.execute(select(ReportUploadBatch))).scalar_one()
        uploads = (
            (await session.execute(select(ReportUpload).order_by(ReportUpload.original_filename)))
            .scalars()
            .all()
        )
        assert batch.business_id == UUID(business_id)
        assert batch.created_by_user_id == UUID(uploader_user_id)
        assert ReportUploadBatchStatus(batch.status) is ReportUploadBatchStatus.COMPLETE
        assert all(upload.business_id == UUID(business_id) for upload in uploads)
        assert all(upload.uploaded_by_user_id == UUID(uploader_user_id) for upload in uploads)
        assert all(upload.storage_version_id for upload in uploads)


@pytest.mark.integration
async def test_mislabeled_or_tampered_object_is_deleted_and_rejected(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
) -> None:
    gateway = FakeS3UploadGateway()
    configure_upload_app(app, gateway)
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="rejected-upload@example.com",
        business_name="Rejected Upload Tenant",
    )
    executable = b"MZ" + b"\x00" * 62
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "renamed.csv",
                    "content_type": "text/csv",
                    "size_bytes": len(executable),
                }
            ]
        },
    )
    assert created.status_code == 201
    payload = created.json()
    gateway.add_uploaded_object(
        file_payload=payload["files"][0],
        content=executable,
    )

    completed = await client.post(
        f"/api/v1/report-uploads/batches/{payload['id']}/complete",
        headers=bearer(token),
    )
    assert completed.status_code == 200
    rejected = completed.json()
    assert rejected["status"] == "rejected"
    assert rejected["files"][0]["status"] == "rejected"
    assert rejected["files"][0]["rejection_code"] == "content_signature_mismatch"
    assert len(gateway.deleted_versions) == 1
    assert gateway.objects == {}


@pytest.mark.integration
async def test_missing_upload_stays_pending_then_expires(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeS3UploadGateway()
    configure_upload_app(app, gateway)
    token, _ = await onboard_owner(
        client,
        email_sender,
        email="expired-upload@example.com",
        business_name="Expired Upload Tenant",
    )
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(token),
        json={
            "files": [
                {
                    "filename": "missing.csv",
                    "content_type": "text/csv",
                    "size_bytes": 100,
                }
            ]
        },
    )
    batch_id = UUID(created.json()["id"])

    pending = await client.post(
        f"/api/v1/report-uploads/batches/{batch_id}/complete",
        headers=bearer(token),
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    async with session_factory() as session:
        batch = await session.get(ReportUploadBatch, batch_id)
        assert batch is not None
        batch.expires_at = utc_now() - timedelta(seconds=1)
        batch.created_at = batch.expires_at - timedelta(minutes=10)
        uploads = (
            await session.execute(select(ReportUpload).where(ReportUpload.batch_id == batch_id))
        ).scalars()
        for upload in uploads:
            upload.expires_at = batch.expires_at
        await session.commit()

    expired = await client.post(
        f"/api/v1/report-uploads/batches/{batch_id}/complete",
        headers=bearer(token),
    )
    assert expired.status_code == 200
    assert expired.json()["status"] == "expired"
    assert expired.json()["files"][0]["status"] == "expired"
    assert expired.json()["files"][0]["rejection_code"] == "upload_expired"


@pytest.mark.integration
async def test_upload_batch_ids_are_tenant_scoped_and_database_binds_creator(
    app,
    client: AsyncClient,
    email_sender: InMemoryEmailSender,
    test_engine: AsyncEngine,
) -> None:
    gateway = FakeS3UploadGateway()
    configure_upload_app(app, gateway)
    first_token, first_onboarding = await onboard_owner(
        client,
        email_sender,
        email="upload-tenant-a@example.com",
        business_name="Upload Tenant A",
    )
    second_token, second_onboarding = await onboard_owner(
        client,
        email_sender,
        email="upload-tenant-b@example.com",
        business_name="Upload Tenant B",
    )
    created = await client.post(
        "/api/v1/report-uploads/batches",
        headers=bearer(first_token),
        json={
            "files": [
                {
                    "filename": "tenant-a.csv",
                    "content_type": "text/csv",
                    "size_bytes": 10,
                }
            ]
        },
    )
    batch_id = created.json()["id"]

    cross_tenant_read = await client.get(
        f"/api/v1/report-uploads/batches/{batch_id}",
        headers=bearer(second_token),
    )
    unknown_read = await client.get(
        f"/api/v1/report-uploads/batches/{uuid4()}",
        headers=bearer(second_token),
    )
    cross_tenant_complete = await client.post(
        f"/api/v1/report-uploads/batches/{batch_id}/complete",
        headers=bearer(second_token),
    )
    assert cross_tenant_read.status_code == unknown_read.status_code == 404
    assert cross_tenant_read.json() == unknown_read.json()
    assert cross_tenant_complete.status_code == 404

    now = utc_now()
    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(ReportUploadBatch).values(
                    id=uuid4(),
                    business_id=UUID(first_onboarding["business"]["id"]),
                    created_by_user_id=UUID(second_onboarding["role_assignment"]["user_id"]),
                    status=ReportUploadBatchStatus.PENDING.value,
                    file_count=1,
                    expires_at=now + timedelta(minutes=10),
                    created_at=now,
                    updated_at=now,
                )
            )

    async with test_engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(IntegrityError):
            await connection.execute(
                insert(ReportUpload).values(
                    id=uuid4(),
                    business_id=UUID(first_onboarding["business"]["id"]),
                    batch_id=UUID(batch_id),
                    batch_position=0,
                    uploaded_by_user_id=UUID(first_onboarding["role_assignment"]["user_id"]),
                    original_filename="invalid-state.csv",
                    file_extension="csv",
                    content_type="text/csv",
                    expected_size_bytes=10,
                    storage_bucket="spike-private-test-uploads",
                    storage_key=f"report-uploads/invalid-state/{uuid4()}.csv",
                    status=ReportUploadStatus.UPLOADED.value,
                    expires_at=now + timedelta(minutes=10),
                    created_at=now,
                    updated_at=now,
                )
            )
