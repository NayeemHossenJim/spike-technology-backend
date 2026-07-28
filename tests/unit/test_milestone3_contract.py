from __future__ import annotations

from io import BytesIO
from struct import pack, pack_into
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models.upload import MAX_REPORT_UPLOAD_BYTES
from app.schemas.upload import (
    ReportUploadBatchCreate,
    ReportUploadFileCreate,
)
from app.services.s3_storage import (
    Boto3S3UploadGateway,
    S3BucketSecurityError,
)
from app.services.uploads import (
    ReportUploadRequestRejectedError,
    SuspiciousReportContentError,
    validate_report_content,
    validate_report_upload_file,
)


def upload_settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://spike:spike@localhost/spike_test",
        "redis_url": "redis://localhost:6379/15",
        "celery_broker_url": "redis://localhost:6379/15",
        "celery_result_backend": "redis://localhost:6379/14",
        "jwt_secret_key": "test-only-secret-with-at-least-thirty-two-characters",
        "email_backend": "console",
        "stripe_enabled": False,
        "s3_uploads_enabled": True,
        "aws_region": "us-east-1",
        "s3_upload_bucket": "spike-private-test-uploads",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def xlsx_bytes(
    *,
    include_macro: bool = False,
    highly_compressed: bool = False,
) -> bytes:
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
        if include_macro:
            archive.writestr("xl/vbaProject.bin", b"macro")
        if highly_compressed:
            archive.writestr("xl/worksheets/sheet1.xml", b"0" * (2 * 1024 * 1024))
    return output.getvalue()


def xls_bytes(*, active_content: bool = False) -> bytes:
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    fat_sector = 0xFFFFFFFD
    no_stream = 0xFFFFFFFF

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    pack_into("<HHHHH", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    pack_into(
        "<IIIIIIIII",
        header,
        40,
        0,
        1,
        1,
        0,
        4096,
        end_of_chain,
        0,
        end_of_chain,
        0,
    )
    pack_into("<109I", header, 76, 0, *([free_sector] * 108))

    fat = [free_sector] * 128
    fat[0] = fat_sector
    fat[1] = end_of_chain
    for sector in range(2, 9):
        fat[sector] = sector + 1
    fat[9] = end_of_chain

    def directory_entry(
        name: str,
        object_type: int,
        start_sector: int,
        stream_size: int,
        *,
        child: int = no_stream,
    ) -> bytes:
        entry = bytearray(128)
        encoded_name = name.encode("utf-16le") + b"\x00\x00"
        entry[: len(encoded_name)] = encoded_name
        pack_into(
            "<HBBIII",
            entry,
            64,
            len(encoded_name),
            object_type,
            1,
            no_stream,
            no_stream,
            child,
        )
        pack_into("<I", entry, 116, start_sector)
        pack_into("<Q", entry, 120, stream_size)
        return bytes(entry)

    workbook = bytearray(4096)
    workbook[:20] = pack(
        "<HHHHHHII",
        0x0809,
        16,
        0x0600,
        0x0005,
        0,
        0,
        0,
        0,
    )
    offset = 20
    if active_content:
        workbook[offset : offset + 4] = pack("<HH", 0x00D3, 0)
        offset += 4
    workbook[offset : offset + 4] = pack("<HH", 0x000A, 0)

    directory = (
        directory_entry("Root Entry", 5, end_of_chain, 0, child=1)
        + directory_entry("Workbook", 2, 2, len(workbook))
        + bytes(256)
    )
    return bytes(header) + pack("<128I", *fat) + directory + bytes(workbook)


def test_upload_configuration_is_optional_but_fails_closed_when_enabled() -> None:
    disabled = upload_settings(
        s3_uploads_enabled=False,
        aws_region=None,
        s3_upload_bucket=None,
    )
    assert disabled.s3_uploads_enabled is False

    with pytest.raises(ValidationError, match="AWS_REGION and S3_UPLOAD_BUCKET"):
        upload_settings(s3_upload_bucket=None)
    with pytest.raises(ValidationError, match="valid S3 bucket"):
        upload_settings(s3_upload_bucket="Invalid_Bucket")
    with pytest.raises(ValidationError, match="formatted as an IP"):
        upload_settings(s3_upload_bucket="192.168.1.20")
    with pytest.raises(ValidationError, match="AWS-reserved"):
        upload_settings(s3_upload_bucket="reports--x-s3")
    with pytest.raises(ValidationError, match="between 1 and 60"):
        upload_settings(s3_presigned_post_expire_minutes=61)
    with pytest.raises(ValidationError, match="S3_UPLOAD_PREFIX"):
        upload_settings(s3_upload_prefix="../escape")


def test_upload_batch_schema_locks_file_count_size_and_request_fields() -> None:
    valid = {
        "filename": "sales.csv",
        "content_type": "text/csv",
        "size_bytes": 128,
    }
    batch = ReportUploadBatchCreate(files=[valid])
    assert batch.files[0].size_bytes == 128

    with pytest.raises(ValidationError):
        ReportUploadBatchCreate(files=[])
    with pytest.raises(ValidationError):
        ReportUploadBatchCreate(files=[valid] * 6)
    with pytest.raises(ValidationError):
        ReportUploadFileCreate(**{**valid, "size_bytes": MAX_REPORT_UPLOAD_BYTES + 1})
    with pytest.raises(ValidationError):
        ReportUploadFileCreate(**{**valid, "size_bytes": "128"})
    with pytest.raises(ValidationError, match="duplicate filenames"):
        ReportUploadBatchCreate(files=[valid, {**valid, "filename": "SALES.CSV"}])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReportUploadBatchCreate.model_validate(
            {
                "files": [valid],
                "business_id": str(uuid4()),
                "storage_key": "attacker-controlled",
            }
        )
    for suspicious_filename in ("sales..csv", "report\u202e.csv"):
        with pytest.raises(ReportUploadRequestRejectedError):
            validate_report_upload_file(
                ReportUploadFileCreate(**{**valid, "filename": suspicious_filename})
            )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("../sales.csv", "text/csv"),
        (r"folder\sales.csv", "text/csv"),
        (".sales.csv", "text/csv"),
        ("payload.exe.csv", "text/csv"),
        ("sales.csv ", "text/csv"),
        ("sales.pdf", "application/pdf"),
        ("sales.xlsx", "application/zip"),
    ],
)
def test_suspicious_or_mismatched_filenames_are_rejected(
    filename: str,
    content_type: str,
) -> None:
    payload = ReportUploadFileCreate(
        filename=filename,
        content_type=content_type,
        size_bytes=100,
    )
    with pytest.raises(ReportUploadRequestRejectedError):
        validate_report_upload_file(payload)


def test_approved_filename_and_mime_combinations_are_canonical() -> None:
    cases = [
        ("sales.2026.csv", "text/csv", "csv"),
        ("ledger.xls", "application/vnd.ms-excel", "xls"),
        (
            "forecast.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        ),
    ]
    for filename, content_type, extension in cases:
        result = validate_report_upload_file(
            ReportUploadFileCreate(
                filename=filename,
                content_type=content_type,
                size_bytes=100,
            )
        )
        assert result.extension == extension
        assert result.content_type == content_type


def test_file_content_must_match_the_declared_report_format() -> None:
    validate_report_content("csv", b"month,revenue\nJan,1200\n")
    validate_report_content(
        "xls",
        xls_bytes(),
    )
    validate_report_content("xlsx", xlsx_bytes())

    with pytest.raises(SuspiciousReportContentError, match="content_signature_mismatch"):
        validate_report_content("csv", b"MZ" + b"\x00" * 64)
    with pytest.raises(SuspiciousReportContentError, match="content_signature_mismatch"):
        validate_report_content("xls", b"not-an-xls")
    with pytest.raises(SuspiciousReportContentError, match="xls_structure_rejected"):
        validate_report_content(
            "xls",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
        )
    with pytest.raises(SuspiciousReportContentError, match="xls_active_content_rejected"):
        validate_report_content("xls", xls_bytes(active_content=True))
    with pytest.raises(SuspiciousReportContentError, match="content_signature_mismatch"):
        validate_report_content("xlsx", b"PK\x03\x04not-a-valid-workbook")
    with pytest.raises(SuspiciousReportContentError, match="xlsx_active_content_rejected"):
        validate_report_content("xlsx", xlsx_bytes(include_macro=True))
    with pytest.raises(SuspiciousReportContentError, match="xlsx_expansion_limit_exceeded"):
        validate_report_content("xlsx", xlsx_bytes(highly_compressed=True))


class FakeS3Client:
    def __init__(self, *, public_access: bool = False) -> None:
        self.public_access = public_access
        self.presign_call = None

    def get_public_access_block(self, **_kwargs):
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": not self.public_access,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }

    def get_bucket_ownership_controls(self, **_kwargs):
        return {
            "OwnershipControls": {
                "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}],
            }
        }

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": "Enabled"}

    def get_bucket_policy_status(self, **_kwargs):
        return {"PolicyStatus": {"IsPublic": False}}

    def generate_presigned_post(self, **kwargs):
        self.presign_call = kwargs
        return {
            "url": "https://spike-private-test-uploads.s3.amazonaws.com",
            "fields": {
                **kwargs["Fields"],
                "key": kwargs["Key"],
                "policy": "signed-policy",
                "x-amz-signature": "signature",
            },
        }


@pytest.mark.asyncio
async def test_presigned_post_binds_private_metadata_type_and_exact_size() -> None:
    client = FakeS3Client()
    gateway = Boto3S3UploadGateway(upload_settings(), client=client)
    await gateway.ensure_bucket_security("spike-private-test-uploads")

    business_id = uuid4()
    batch_id = uuid4()
    upload_id = uuid4()
    result = await gateway.create_presigned_post(
        bucket="spike-private-test-uploads",
        key=f"report-uploads/{business_id}/{batch_id}/{upload_id}.csv",
        content_type="text/csv",
        expected_size_bytes=512,
        business_id=business_id,
        batch_id=batch_id,
        upload_id=upload_id,
        expires_in_seconds=600,
    )

    assert result.url.startswith("https://")
    assert client.presign_call["ExpiresIn"] == 600
    assert ["content-length-range", 512, 512] in client.presign_call["Conditions"]
    fields = client.presign_call["Fields"]
    assert fields["Content-Type"] == "text/csv"
    assert fields["Cache-Control"] == "no-store"
    assert fields["x-amz-server-side-encryption"] == "AES256"
    assert fields["x-amz-meta-business-id"] == str(business_id)
    assert fields["x-amz-meta-batch-id"] == str(batch_id)
    assert fields["x-amz-meta-upload-id"] == str(upload_id)
    assert fields["x-amz-meta-expected-size"] == "512"
    assert "acl" not in fields


@pytest.mark.asyncio
async def test_bucket_security_preflight_rejects_public_access() -> None:
    gateway = Boto3S3UploadGateway(
        upload_settings(),
        client=FakeS3Client(public_access=True),
    )
    with pytest.raises(S3BucketSecurityError, match="Block Public Access"):
        await gateway.ensure_bucket_security("spike-private-test-uploads")


def test_milestone3_openapi_exposes_only_server_owned_upload_routes() -> None:
    schema = create_app().openapi()
    assert schema["info"]["version"] == "0.4.0"
    paths = schema["paths"]
    assert "/api/v1/report-uploads/batches" in paths
    assert "/api/v1/report-uploads/batches/{batch_id}" in paths
    assert "/api/v1/report-uploads/batches/{batch_id}/complete" in paths

    batch_properties = schema["components"]["schemas"]["ReportUploadBatchCreate"]["properties"]
    file_properties = schema["components"]["schemas"]["ReportUploadFileCreate"]["properties"]
    assert list(batch_properties) == ["files"]
    assert list(file_properties) == ["filename", "content_type", "size_bytes"]
