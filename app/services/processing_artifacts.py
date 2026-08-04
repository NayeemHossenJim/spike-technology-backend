from __future__ import annotations

from dataclasses import dataclass

from asyncer import asyncify

from app.core.config import Settings
from app.services.processing import (
    ReportProcessingClaim,
    ReportProcessingOutput,
    validate_processing_error_code,
)
from app.services.report_parser import (
    ParsedReportArtifacts,
    ReportParsingContext,
    ReportParsingError,
    parse_report_content,
)
from app.services.s3_storage import (
    S3BucketSecurityError,
    S3ObjectNotFoundError,
    S3StorageError,
    S3UploadGateway,
)
from app.services.uploads import SuspiciousReportContentError, validate_report_content

NORMALIZED_REPORT_CONTENT_TYPE = "application/x-ndjson"
NORMALIZED_REPORT_CONTENT_ENCODING = "gzip"
PROFILE_REPORT_CONTENT_TYPE = "application/json"


class TerminalReportProcessingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = validate_processing_error_code(code)


class RetryableReportProcessingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = validate_processing_error_code(code)


@dataclass(frozen=True, slots=True)
class ReportArtifactKeys:
    normalized: str
    profile: str


def report_artifact_keys(
    *,
    prefix: str,
    claim: ReportProcessingClaim,
) -> ReportArtifactKeys:
    base = f"{prefix}/{claim.business_id}/processed/{claim.job_id}"
    return ReportArtifactKeys(
        normalized=f"{base}/normalized.v1.jsonl.gz",
        profile=f"{base}/profile.v1.json",
    )


def _validate_and_parse(
    *,
    claim: ReportProcessingClaim,
    content: bytes,
) -> ParsedReportArtifacts:
    validate_report_content(claim.file_extension, content)
    return parse_report_content(
        context=ReportParsingContext(
            job_id=claim.job_id,
            business_id=claim.business_id,
            report_upload_id=claim.report_upload_id,
            original_filename=claim.original_filename,
            file_extension=claim.file_extension,
            content_type=claim.content_type,
        ),
        content=content,
    )


class ReportArtifactProcessor:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: S3UploadGateway,
    ) -> None:
        self.settings = settings
        self.storage = storage

    async def process(self, claim: ReportProcessingClaim) -> ReportProcessingOutput:
        if (
            not self.settings.s3_uploads_enabled
            or not self.settings.s3_upload_bucket
            or self.settings.s3_upload_bucket != claim.storage_bucket
        ):
            raise RetryableReportProcessingError("processing_storage_unavailable")

        try:
            await self.storage.ensure_bucket_security(claim.storage_bucket)
        except S3BucketSecurityError as exc:
            raise TerminalReportProcessingError("processing_storage_security_invalid") from exc
        except S3StorageError as exc:
            raise RetryableReportProcessingError("processing_storage_unavailable") from exc

        try:
            content = await self.storage.read_object(
                bucket=claim.storage_bucket,
                key=claim.storage_key,
                version_id=claim.storage_version_id,
                max_bytes=claim.expected_size_bytes,
            )
        except S3ObjectNotFoundError as exc:
            raise TerminalReportProcessingError("source_object_missing") from exc
        except S3StorageError as exc:
            raise RetryableReportProcessingError("source_storage_unavailable") from exc
        if len(content) != claim.expected_size_bytes:
            raise TerminalReportProcessingError("source_size_mismatch")

        try:
            parsed = await asyncify(_validate_and_parse)(
                claim=claim,
                content=content,
            )
        except SuspiciousReportContentError as exc:
            raise TerminalReportProcessingError("source_content_invalid") from exc
        except ReportParsingError as exc:
            raise TerminalReportProcessingError(exc.code) from exc
        except (OSError, UnicodeError, ValueError) as exc:
            raise TerminalReportProcessingError("report_malformed") from exc

        keys = report_artifact_keys(
            prefix=self.settings.s3_upload_prefix,
            claim=claim,
        )
        base_metadata = {
            "business-id": str(claim.business_id),
            "processing-job-id": str(claim.job_id),
            "upload-id": str(claim.report_upload_id),
            "artifact-version": "1",
        }
        try:
            normalized = await self.storage.write_object(
                bucket=claim.storage_bucket,
                key=keys.normalized,
                content=parsed.normalized_content,
                content_type=NORMALIZED_REPORT_CONTENT_TYPE,
                content_encoding=NORMALIZED_REPORT_CONTENT_ENCODING,
                metadata={
                    **base_metadata,
                    "artifact": "normalized",
                    "sha256": parsed.normalized_sha256,
                },
            )
            profile = await self.storage.write_object(
                bucket=claim.storage_bucket,
                key=keys.profile,
                content=parsed.profile_content,
                content_type=PROFILE_REPORT_CONTENT_TYPE,
                content_encoding=None,
                metadata={
                    **base_metadata,
                    "artifact": "profile",
                    "sha256": parsed.profile_sha256,
                },
            )
        except S3BucketSecurityError as exc:
            raise TerminalReportProcessingError("processing_storage_security_invalid") from exc
        except S3StorageError as exc:
            raise RetryableReportProcessingError("artifact_storage_unavailable") from exc

        if normalized.content_length != len(parsed.normalized_content):
            raise RetryableReportProcessingError("artifact_storage_unavailable")
        if profile.content_length != len(parsed.profile_content):
            raise RetryableReportProcessingError("artifact_storage_unavailable")

        return ReportProcessingOutput(
            storage_bucket=claim.storage_bucket,
            normalized_storage_key=keys.normalized,
            normalized_storage_version_id=normalized.version_id,
            normalized_etag=normalized.etag,
            normalized_size_bytes=normalized.content_length,
            profile_storage_key=keys.profile,
            profile_storage_version_id=profile.version_id,
            profile_etag=profile.etag,
            record_count=parsed.record_count,
            sheet_count=parsed.sheet_count,
        )


__all__ = [
    "NORMALIZED_REPORT_CONTENT_ENCODING",
    "NORMALIZED_REPORT_CONTENT_TYPE",
    "PROFILE_REPORT_CONTENT_TYPE",
    "ReportArtifactKeys",
    "ReportArtifactProcessor",
    "RetryableReportProcessingError",
    "TerminalReportProcessingError",
    "report_artifact_keys",
]
