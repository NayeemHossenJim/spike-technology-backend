from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

import app.services.report_parser as report_parser
from app.core.config import get_settings
from app.services.processing import ReportProcessingClaim
from app.services.processing_artifacts import (
    NORMALIZED_REPORT_CONTENT_ENCODING,
    NORMALIZED_REPORT_CONTENT_TYPE,
    PROFILE_REPORT_CONTENT_TYPE,
    ReportArtifactProcessor,
    TerminalReportProcessingError,
)
from app.services.report_parser import (
    REPORT_MAX_CELL_CHARACTERS,
    REPORT_MAX_COLUMNS_PER_SHEET,
    REPORT_MAX_ROWS_PER_SHEET,
    REPORT_MAX_SHEETS,
    REPORT_MAX_TOTAL_CELLS,
    ReportParsingContext,
    ReportParsingError,
    parse_report_content,
)
from app.services.s3_storage import (
    Boto3S3UploadGateway,
    S3BucketSecurityError,
    S3ObjectNotFoundError,
    WrittenS3Object,
)
from app.services.uploads import validate_report_content


def parsing_context(*, extension: str, content_type: str, filename: str) -> ReportParsingContext:
    return ReportParsingContext(
        job_id=uuid4(),
        business_id=uuid4(),
        report_upload_id=uuid4(),
        original_filename=filename,
        file_extension=extension,
        content_type=content_type,
    )


def test_parser_resource_contract_is_fixed_and_bounded() -> None:
    assert REPORT_MAX_SHEETS == 20
    assert REPORT_MAX_ROWS_PER_SHEET == 100_000
    assert REPORT_MAX_COLUMNS_PER_SHEET == 256
    assert REPORT_MAX_TOTAL_CELLS == 2_000_000
    assert REPORT_MAX_CELL_CHARACTERS == 32_767


def test_csv_normalization_is_deterministic_and_preserves_untrusted_text() -> None:
    context = parsing_context(
        extension="csv",
        content_type="text/csv",
        filename="accounts.csv",
    )
    content = b"\xef\xbb\xbf\nAccount,account,,Formula\n001,42,,=2+2\n002,43,active,@SUM(A1:A2)\n"

    first = parse_report_content(context=context, content=content)
    second = parse_report_content(context=context, content=content)

    assert first.normalized_content == second.normalized_content
    assert first.profile_content == second.profile_content
    assert first.record_count == 2
    assert first.sheet_count == 1
    records = [
        json.loads(line)
        for line in gzip.decompress(first.normalized_content).decode("utf-8").splitlines()
    ]
    assert records == [
        {
            "sheet_index": 0,
            "sheet_name": "data",
            "row_number": 3,
            "values": {
                "Account": "001",
                "account__2": "42",
                "column_3": None,
                "Formula": "=2+2",
            },
        },
        {
            "sheet_index": 0,
            "sheet_name": "data",
            "row_number": 4,
            "values": {
                "Account": "002",
                "account__2": "43",
                "column_3": "active",
                "Formula": "@SUM(A1:A2)",
            },
        },
    ]
    profile = json.loads(first.profile_content)
    assert profile["source"]["filename"] == "accounts.csv"
    assert profile["record_count"] == 2
    assert profile["normalized"]["sha256"] == hashlib.sha256(first.normalized_content).hexdigest()
    assert [column["name"] for column in profile["sheets"][0]["columns"]] == [
        "Account",
        "account__2",
        "column_3",
        "Formula",
    ]


def test_xlsx_parser_uses_cached_values_and_typed_cells_without_formula_execution() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Forecast"
    worksheet.append(["month", "revenue", "as_of", "formula"])
    worksheet.append(["Jan", 1200, date(2026, 1, 31), "=1+1"])
    workbook.create_sheet("Blank")
    output = BytesIO()
    workbook.save(output)

    parsed = parse_report_content(
        context=parsing_context(
            extension="xlsx",
            content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            filename="forecast.xlsx",
        ),
        content=output.getvalue(),
    )

    assert parsed.record_count == 1
    assert parsed.sheet_count == 1
    [record] = [
        json.loads(line)
        for line in gzip.decompress(parsed.normalized_content).decode("utf-8").splitlines()
    ]
    assert record["values"] == {
        "month": "Jan",
        "revenue": 1200,
        "as_of": "2026-01-31T00:00:00",
        "formula": None,
    }
    profile = json.loads(parsed.profile_content)
    assert profile["sheets"][0]["columns"][1]["type_counts"] == {"integer": 1}
    assert profile["sheets"][0]["columns"][3]["type_counts"] == {"null": 1}


def test_xlsx_preflight_caps_metadata_loaded_eagerly_by_the_parser(monkeypatch) -> None:
    monkeypatch.setattr(report_parser, "REPORT_MAX_XLSX_SHARED_STRINGS_BYTES", 32)
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("xl/sharedStrings.xml", b"x" * 33)

    with pytest.raises(ReportParsingError) as captured:
        parse_report_content(
            context=parsing_context(
                extension="xlsx",
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                filename="bounded.xlsx",
            ),
            content=output.getvalue(),
        )
    assert captured.value.code == "workbook_metadata_limit_exceeded"


def test_real_biff_xls_fixture_is_supported() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "legacy_report.xls.gz.b64"
    content = gzip.decompress(base64.b64decode(fixture.read_text(encoding="ascii")))
    validate_report_content("xls", content)

    parsed = parse_report_content(
        context=parsing_context(
            extension="xls",
            content_type="application/vnd.ms-excel",
            filename="legacy.xls",
        ),
        content=content,
    )

    assert parsed.record_count == 1
    assert parsed.sheet_count == 1
    [record] = [
        json.loads(line)
        for line in gzip.decompress(parsed.normalized_content).decode("utf-8").splitlines()
    ]
    assert record["sheet_name"] == "Legacy"
    assert record["values"] == {"month": "Apr", "revenue": 1500, "active": True}


@pytest.mark.parametrize(
    ("content", "error_code"),
    [
        (
            b"a," + (b"x" * (REPORT_MAX_CELL_CHARACTERS + 1)) + b"\n1,2\n",
            "cell_text_limit_exceeded",
        ),
        (
            (
                ",".join(f"column_{index}" for index in range(REPORT_MAX_COLUMNS_PER_SHEET + 1))
            ).encode()
            + b"\n",
            "column_limit_exceeded",
        ),
        (b"\n" * 101 + b"header\nvalue\n", "header_not_found"),
    ],
    ids=("cell-text-limit", "column-limit", "header-search-limit"),
)
def test_csv_parser_fails_safely_at_fixed_resource_boundaries(
    content: bytes,
    error_code: str,
) -> None:
    with pytest.raises(ReportParsingError) as captured:
        parse_report_content(
            context=parsing_context(
                extension="csv",
                content_type="text/csv",
                filename="bounded.csv",
            ),
            content=content,
        )
    assert captured.value.code == error_code
    assert str(captured.value) == ""


@dataclass
class InMemoryArtifactStorage:
    source_content: bytes | None
    security_checks: list[str] = field(default_factory=list)
    writes: list[dict[str, object]] = field(default_factory=list)

    async def ensure_bucket_security(self, bucket: str) -> None:
        self.security_checks.append(bucket)

    async def read_object(self, **kwargs) -> bytes:
        if self.source_content is None:
            raise S3ObjectNotFoundError
        assert len(self.source_content) <= kwargs["max_bytes"]
        return self.source_content

    async def write_object(self, **kwargs) -> WrittenS3Object:
        self.writes.append(dict(kwargs))
        position = len(self.writes)
        return WrittenS3Object(
            content_length=len(kwargs["content"]),
            etag=f"etag-{position}",
            version_id=f"version-{position}",
        )


def processing_claim(*, content: bytes) -> ReportProcessingClaim:
    return ReportProcessingClaim(
        job_id=uuid4(),
        business_id=uuid4(),
        report_upload_id=uuid4(),
        lease_token=uuid4(),
        attempt_count=1,
        max_attempts=3,
        storage_bucket="spike-private-test-uploads",
        storage_key="report-uploads/private/source.csv",
        storage_version_id="immutable-source-version",
        source_etag="source-etag",
        original_filename="sales.csv",
        file_extension="csv",
        content_type="text/csv",
        expected_size_bytes=len(content),
    )


@pytest.mark.asyncio
async def test_artifact_processor_uses_server_owned_private_versioned_outputs() -> None:
    content = b"month,revenue\nJan,1200\n"
    claim = processing_claim(content=content)
    storage = InMemoryArtifactStorage(source_content=content)
    settings = get_settings().model_copy(
        update={
            "s3_uploads_enabled": True,
            "aws_region": "us-east-1",
            "s3_upload_bucket": claim.storage_bucket,
            "s3_upload_prefix": "report-uploads",
        }
    )

    result = await ReportArtifactProcessor(settings=settings, storage=storage).process(claim)

    assert storage.security_checks == [claim.storage_bucket]
    assert len(storage.writes) == 2
    normalized, profile = storage.writes
    expected_prefix = f"report-uploads/{claim.business_id}/processed/{claim.job_id}/"
    assert normalized["key"].startswith(expected_prefix)
    assert profile["key"].startswith(expected_prefix)
    assert claim.storage_key not in {normalized["key"], profile["key"]}
    assert normalized["content_type"] == NORMALIZED_REPORT_CONTENT_TYPE
    assert normalized["content_encoding"] == NORMALIZED_REPORT_CONTENT_ENCODING
    assert profile["content_type"] == PROFILE_REPORT_CONTENT_TYPE
    assert profile["content_encoding"] is None
    assert normalized["metadata"]["business-id"] == str(claim.business_id)
    assert normalized["metadata"]["processing-job-id"] == str(claim.job_id)
    assert result.normalized_storage_version_id == "version-1"
    assert result.profile_storage_version_id == "version-2"
    assert result.record_count == 1
    assert result.sheet_count == 1


@pytest.mark.asyncio
async def test_missing_immutable_source_is_terminal_and_detail_free() -> None:
    content = b"month,revenue\nJan,1200\n"
    claim = processing_claim(content=content)
    settings = get_settings().model_copy(
        update={
            "s3_uploads_enabled": True,
            "aws_region": "us-east-1",
            "s3_upload_bucket": claim.storage_bucket,
        }
    )

    with pytest.raises(TerminalReportProcessingError) as captured:
        await ReportArtifactProcessor(
            settings=settings,
            storage=InMemoryArtifactStorage(source_content=None),
        ).process(claim)

    assert captured.value.code == "source_object_missing"
    assert str(captured.value) == ""


class FakePutObjectClient:
    def __init__(self, *, version_id: str | None = "artifact-version") -> None:
        self.version_id = version_id
        self.request: dict[str, object] | None = None

    def put_object(self, **kwargs):
        self.request = dict(kwargs)
        return {"ETag": '"artifact-etag"', "VersionId": self.version_id}


@pytest.mark.asyncio
async def test_s3_artifact_write_binds_checksum_encryption_and_no_store_policy() -> None:
    client = FakePutObjectClient()
    gateway = Boto3S3UploadGateway(get_settings(), client=client)
    content = b"private normalized content"

    written = await gateway.write_object(
        bucket="spike-private-test-uploads",
        key="report-uploads/tenant/processed/job/normalized.v1.jsonl.gz",
        content=content,
        content_type=NORMALIZED_REPORT_CONTENT_TYPE,
        content_encoding="gzip",
        metadata={"artifact": "normalized"},
    )

    assert written.version_id == "artifact-version"
    assert written.etag == "artifact-etag"
    assert client.request is not None
    assert client.request["ServerSideEncryption"] == "AES256"
    assert client.request["CacheControl"] == "no-store"
    assert client.request["ContentEncoding"] == "gzip"
    assert client.request["ChecksumAlgorithm"] == "SHA256"
    assert client.request["ChecksumSHA256"] == base64.b64encode(
        hashlib.sha256(content).digest()
    ).decode("ascii")
    assert "ACL" not in client.request


@pytest.mark.asyncio
async def test_s3_artifact_write_requires_a_real_version_id() -> None:
    gateway = Boto3S3UploadGateway(
        get_settings(),
        client=FakePutObjectClient(version_id=None),
    )
    with pytest.raises(S3BucketSecurityError, match="immutable S3 version"):
        await gateway.write_object(
            bucket="spike-private-test-uploads",
            key="report-uploads/tenant/processed/job/profile.v1.json",
            content=b"{}\n",
            content_type="application/json",
            content_encoding=None,
            metadata={"artifact": "profile"},
        )
