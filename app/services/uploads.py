from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath
from struct import unpack_from
from uuid import UUID
from zipfile import BadZipFile, ZipFile

import olefile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.base import utc_now
from app.models.upload import (
    ReportUpload,
    ReportUploadBatch,
    ReportUploadBatchStatus,
    ReportUploadStatus,
)
from app.schemas.upload import ReportUploadBatchCreate, ReportUploadFileCreate
from app.services.processing import ReportProcessingService
from app.services.processing_dispatch import ReportProcessingDispatcher
from app.services.s3_storage import (
    S3_CACHE_CONTROL,
    S3_ENCRYPTION_ALGORITHM,
    PresignedPost,
    S3BucketSecurityError,
    S3ObjectNotFoundError,
    S3StorageError,
    S3UploadGateway,
    StoredS3Object,
)
from app.services.tenant import TenantScope

CANONICAL_CONTENT_TYPES = {
    "csv": "text/csv",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

DANGEROUS_FILENAME_SUFFIXES = {
    "app",
    "bat",
    "cmd",
    "com",
    "dll",
    "exe",
    "hta",
    "html",
    "jar",
    "js",
    "jse",
    "lnk",
    "msi",
    "php",
    "ps1",
    "py",
    "scr",
    "sh",
    "svg",
    "vbe",
    "vbs",
    "wsf",
}

KNOWN_BINARY_PREFIXES = (
    b"\x7fELF",
    b"%PDF",
    b"MZ",
    b"PK\x03\x04",
    b"Rar!\x1a\x07",
    b"\x89PNG\r\n\x1a\n",
    b"GIF87a",
    b"GIF89a",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
)

XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
XLS_BOF_RECORD_IDS = {0x0009, 0x0209, 0x0409, 0x0809}
XLS_ENCRYPTION_RECORD_ID = 0x002F
XLS_VBA_PROJECT_RECORD_ID = 0x00D3
XLS_MACRO_SHEET_TYPE = 0x0040
XLS_DANGEROUS_STORAGE_NAMES = {
    "_vba_project_cur",
    "macros",
    "objectpool",
    "vba",
}
XLSX_REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
}
XLSX_FORBIDDEN_PART_PREFIXES = (
    "customui/",
    "xl/activex/",
    "xl/embeddings/",
    "xl/externallinks/",
)
XLSX_FORBIDDEN_PART_NAMES = {
    "xl/vbaproject.bin",
}
XLSX_MAX_ENTRIES = 10_000
XLSX_MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
XLSX_MAX_COMPRESSION_RATIO = 100


class ReportUploadRequestRejectedError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ReportUploadsDisabledError(Exception):
    pass


class ReportUploadConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CreatedReportUpload:
    record: ReportUpload
    presigned_post: PresignedPost


@dataclass(frozen=True, slots=True)
class CreatedReportUploadBatch:
    batch: ReportUploadBatch
    uploads: tuple[CreatedReportUpload, ...]


@dataclass(frozen=True, slots=True)
class ReportUploadBatchResult:
    batch: ReportUploadBatch
    uploads: tuple[ReportUpload, ...]


@dataclass(frozen=True, slots=True)
class ValidatedFileMetadata:
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int


class SuspiciousReportContentError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_report_upload_file(payload: ReportUploadFileCreate) -> ValidatedFileMetadata:
    filename = payload.filename
    normalized = unicodedata.normalize("NFKC", filename)
    if filename != filename.strip() or normalized != filename:
        raise ReportUploadRequestRejectedError(
            "Filenames must be normalized and cannot start or end with whitespace."
        )
    if filename.startswith(".") or filename.endswith("."):
        raise ReportUploadRequestRejectedError(
            "Hidden or dot-terminated filenames are not allowed."
        )
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ReportUploadRequestRejectedError(
            "Filenames cannot contain path separators or consecutive dots."
        )
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in filename):
        raise ReportUploadRequestRejectedError(
            "Filenames cannot contain control or invisible formatting characters."
        )
    try:
        filename_bytes = filename.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReportUploadRequestRejectedError(
            "Filenames must contain valid Unicode characters."
        ) from exc
    if len(filename_bytes) > 255:
        raise ReportUploadRequestRejectedError("Filenames cannot exceed 255 UTF-8 bytes.")

    suffixes = [part.lower() for part in filename.split(".")[1:] if part]
    if not suffixes or suffixes[-1] not in CANONICAL_CONTENT_TYPES:
        raise ReportUploadRequestRejectedError("Only CSV, XLS, and XLSX files are allowed.")
    if any(suffix in DANGEROUS_FILENAME_SUFFIXES for suffix in suffixes[:-1]):
        raise ReportUploadRequestRejectedError(
            "The filename contains a suspicious executable or active-content extension."
        )

    extension = suffixes[-1]
    expected_content_type = CANONICAL_CONTENT_TYPES[extension]
    if payload.content_type != expected_content_type:
        raise ReportUploadRequestRejectedError(
            f"{extension.upper()} files must use content type {expected_content_type}."
        )
    return ValidatedFileMetadata(
        original_filename=filename,
        extension=extension,
        content_type=expected_content_type,
        size_bytes=payload.size_bytes,
    )


def _validate_csv_content(content: bytes) -> None:
    if not content or content.startswith(KNOWN_BINARY_PREFIXES):
        raise SuspiciousReportContentError("content_signature_mismatch")
    sample = content[: 64 * 1024]
    if b"\x00" in sample:
        raise SuspiciousReportContentError("content_signature_mismatch")
    try:
        decoded = sample.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SuspiciousReportContentError("csv_not_utf8") from exc
    invalid_controls = sum(
        1 for character in decoded if ord(character) < 32 and character not in {"\t", "\r", "\n"}
    )
    if invalid_controls:
        raise SuspiciousReportContentError("csv_contains_control_bytes")


def _validate_xls_workbook_stream(workbook: bytes) -> None:
    if len(workbook) < 8:
        raise SuspiciousReportContentError("xls_structure_rejected")

    first_record_id, first_record_size = unpack_from("<HH", workbook)
    if first_record_id not in XLS_BOF_RECORD_IDS or first_record_size < 4:
        raise SuspiciousReportContentError("xls_structure_rejected")
    if 4 + first_record_size > len(workbook):
        raise SuspiciousReportContentError("xls_structure_rejected")
    if unpack_from("<H", workbook, 6)[0] != 0x0005:
        raise SuspiciousReportContentError("xls_structure_rejected")

    offset = 0
    while offset + 4 <= len(workbook):
        record_id, record_size = unpack_from("<HH", workbook, offset)
        record_end = offset + 4 + record_size
        if record_end > len(workbook):
            raise SuspiciousReportContentError("xls_structure_rejected")
        if record_id == XLS_ENCRYPTION_RECORD_ID:
            raise SuspiciousReportContentError("xls_encrypted_rejected")
        if record_id == XLS_VBA_PROJECT_RECORD_ID:
            raise SuspiciousReportContentError("xls_active_content_rejected")
        if (
            record_id in XLS_BOF_RECORD_IDS
            and record_size >= 4
            and unpack_from("<H", workbook, offset + 6)[0] == XLS_MACRO_SHEET_TYPE
        ):
            raise SuspiciousReportContentError("xls_active_content_rejected")
        offset = record_end


def _validate_xls_content(content: bytes) -> None:
    if not content.startswith(XLS_MAGIC):
        raise SuspiciousReportContentError("content_signature_mismatch")
    try:
        if not olefile.isOleFile(BytesIO(content)):
            raise SuspiciousReportContentError("xls_structure_rejected")
        with olefile.OleFileIO(
            BytesIO(content),
            raise_defects=olefile.DEFECT_INCORRECT,
        ) as compound:
            paths = compound.listdir(streams=True, storages=True)
            for path in paths:
                for segment in path:
                    lowered = segment.casefold()
                    if lowered in XLS_DANGEROUS_STORAGE_NAMES or lowered.startswith("mbd"):
                        raise SuspiciousReportContentError("xls_active_content_rejected")

            workbook_path = next(
                (path for path in paths if path and path[-1].casefold() in {"book", "workbook"}),
                None,
            )
            if workbook_path is None:
                raise SuspiciousReportContentError("xls_structure_rejected")
            workbook_size = compound.get_size(workbook_path)
            if workbook_size <= 0 or workbook_size > len(content):
                raise SuspiciousReportContentError("xls_structure_rejected")
            workbook = compound.openstream(workbook_path).read(workbook_size)
            if len(workbook) != workbook_size:
                raise SuspiciousReportContentError("xls_structure_rejected")
    except SuspiciousReportContentError:
        raise
    except (OSError, EOFError, TypeError, ValueError) as exc:
        raise SuspiciousReportContentError("xls_structure_rejected") from exc

    _validate_xls_workbook_stream(workbook)


def _safe_xlsx_path(name: str) -> bool:
    if "\\" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _validate_xlsx_content(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > XLSX_MAX_ENTRIES:
                raise SuspiciousReportContentError("xlsx_structure_rejected")

            names = {entry.filename for entry in entries}
            if not XLSX_REQUIRED_PARTS.issubset(names):
                raise SuspiciousReportContentError("xlsx_structure_rejected")

            total_uncompressed = 0
            total_compressed = 0
            for entry in entries:
                if not _safe_xlsx_path(entry.filename) or entry.flag_bits & 0x1:
                    raise SuspiciousReportContentError("xlsx_structure_rejected")
                lowered_name = entry.filename.casefold()
                if lowered_name in XLSX_FORBIDDEN_PART_NAMES or lowered_name.startswith(
                    XLSX_FORBIDDEN_PART_PREFIXES
                ):
                    raise SuspiciousReportContentError("xlsx_active_content_rejected")
                total_uncompressed += entry.file_size
                total_compressed += max(entry.compress_size, 1)

            if total_uncompressed > XLSX_MAX_UNCOMPRESSED_BYTES:
                raise SuspiciousReportContentError("xlsx_expansion_limit_exceeded")
            if (
                total_uncompressed > len(content)
                and total_uncompressed / total_compressed > XLSX_MAX_COMPRESSION_RATIO
            ):
                raise SuspiciousReportContentError("xlsx_expansion_limit_exceeded")

            content_types = archive.read("[Content_Types].xml")
            expected_workbook_type = (
                b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
            )
            if expected_workbook_type not in content_types:
                raise SuspiciousReportContentError("xlsx_structure_rejected")
            if archive.testzip() is not None:
                raise SuspiciousReportContentError("xlsx_crc_failed")
    except SuspiciousReportContentError:
        raise
    except (BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise SuspiciousReportContentError("content_signature_mismatch") from exc


def validate_report_content(extension: str, content: bytes) -> None:
    if extension == "csv":
        _validate_csv_content(content)
        return
    if extension == "xls":
        _validate_xls_content(content)
        return
    if extension == "xlsx":
        _validate_xlsx_content(content)
        return
    raise ValueError(f"Unsupported report extension: {extension}")


def _object_rejection_code(upload: ReportUpload, stored: StoredS3Object) -> str | None:
    if stored.content_length != upload.expected_size_bytes:
        return "size_mismatch"
    if stored.content_type != upload.content_type:
        return "content_type_mismatch"
    if stored.cache_control != S3_CACHE_CONTROL:
        return "storage_policy_mismatch"
    if stored.server_side_encryption != S3_ENCRYPTION_ALGORITHM:
        return "storage_encryption_mismatch"
    if not stored.etag or stored.last_modified is None:
        return "storage_policy_mismatch"
    if stored.last_modified.tzinfo is None:
        return "storage_policy_mismatch"
    if stored.last_modified > upload.expires_at:
        return "upload_expired"
    expected_metadata = {
        "business-id": str(upload.business_id),
        "batch-id": str(upload.batch_id),
        "upload-id": str(upload.id),
        "expected-size": str(upload.expected_size_bytes),
    }
    if any(stored.metadata.get(key) != value for key, value in expected_metadata.items()):
        return "storage_metadata_mismatch"
    return None


def _batch_status(uploads: tuple[ReportUpload, ...]) -> ReportUploadBatchStatus:
    statuses = [ReportUploadStatus(upload.status) for upload in uploads]
    if any(status is ReportUploadStatus.PENDING for status in statuses):
        return ReportUploadBatchStatus.PENDING
    if all(status is ReportUploadStatus.UPLOADED for status in statuses):
        return ReportUploadBatchStatus.COMPLETE
    if any(status is ReportUploadStatus.UPLOADED for status in statuses):
        return ReportUploadBatchStatus.PARTIAL
    if all(status is ReportUploadStatus.EXPIRED for status in statuses):
        return ReportUploadBatchStatus.EXPIRED
    return ReportUploadBatchStatus.REJECTED


class ReportUploadService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        storage: S3UploadGateway,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage

    def _storage_configuration(self) -> tuple[str, str, int]:
        if (
            not self.settings.s3_uploads_enabled
            or not self.settings.s3_upload_bucket
            or not self.settings.aws_region
        ):
            raise ReportUploadsDisabledError
        return (
            self.settings.s3_upload_bucket,
            self.settings.s3_upload_prefix,
            self.settings.s3_presigned_post_expire_minutes * 60,
        )

    async def create_batch(
        self,
        *,
        scope: TenantScope,
        uploader_user_id: UUID,
        payload: ReportUploadBatchCreate,
    ) -> CreatedReportUploadBatch:
        bucket, prefix, expires_in_seconds = self._storage_configuration()
        validated_files = tuple(validate_report_upload_file(item) for item in payload.files)
        await self.storage.ensure_bucket_security(bucket)

        now = utc_now()
        expires_at = now + timedelta(seconds=expires_in_seconds)
        batch = ReportUploadBatch(
            business_id=scope.business_id,
            created_by_user_id=uploader_user_id,
            file_count=len(validated_files),
            expires_at=expires_at,
        )
        records: list[ReportUpload] = []
        created_uploads: list[CreatedReportUpload] = []

        for batch_position, metadata in enumerate(validated_files):
            record = ReportUpload(
                business_id=scope.business_id,
                batch_id=batch.id,
                batch_position=batch_position,
                uploaded_by_user_id=uploader_user_id,
                original_filename=metadata.original_filename,
                file_extension=metadata.extension,
                content_type=metadata.content_type,
                expected_size_bytes=metadata.size_bytes,
                storage_bucket=bucket,
                storage_key="",
                expires_at=expires_at,
            )
            record.storage_key = (
                f"{prefix}/{scope.business_id}/{batch.id}/{record.id}.{metadata.extension}"
            )
            presigned_post = await self.storage.create_presigned_post(
                bucket=bucket,
                key=record.storage_key,
                content_type=record.content_type,
                expected_size_bytes=record.expected_size_bytes,
                business_id=scope.business_id,
                batch_id=batch.id,
                upload_id=record.id,
                expires_in_seconds=expires_in_seconds,
            )
            records.append(record)
            created_uploads.append(
                CreatedReportUpload(
                    record=record,
                    presigned_post=presigned_post,
                )
            )

        try:
            self.session.add(batch)
            await self.session.flush()
            self.session.add_all(records)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ReportUploadConflictError from exc
        return CreatedReportUploadBatch(
            batch=batch,
            uploads=tuple(created_uploads),
        )

    async def _locked_batch(
        self,
        scope: TenantScope,
        batch_id: UUID,
    ) -> ReportUploadBatch | None:
        result = await self.session.execute(
            scope.select(
                ReportUploadBatch,
                ReportUploadBatch.id == batch_id,
            ).with_for_update()
        )
        return result.scalar_one_or_none()

    async def _batch_uploads(
        self,
        scope: TenantScope,
        batch_id: UUID,
    ) -> tuple[ReportUpload, ...]:
        result = await self.session.execute(
            scope.select(
                ReportUpload,
                ReportUpload.batch_id == batch_id,
            ).order_by(ReportUpload.batch_position)
        )
        return tuple(result.scalars().all())

    async def get_batch(
        self,
        *,
        scope: TenantScope,
        batch_id: UUID,
    ) -> ReportUploadBatchResult | None:
        result = await self.session.execute(
            scope.select(
                ReportUploadBatch,
                ReportUploadBatch.id == batch_id,
            )
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            return None
        return ReportUploadBatchResult(
            batch=batch,
            uploads=await self._batch_uploads(scope, batch_id),
        )

    async def _reject_object(
        self,
        *,
        upload: ReportUpload,
        version_id: str,
        code: str,
        now: datetime,
    ) -> None:
        await self.storage.delete_object(
            bucket=upload.storage_bucket,
            key=upload.storage_key,
            version_id=version_id,
        )
        upload.status = (
            ReportUploadStatus.EXPIRED if code == "upload_expired" else ReportUploadStatus.REJECTED
        )
        upload.rejection_code = code
        upload.rejected_at = now

    async def _verify_pending_upload(
        self,
        *,
        upload: ReportUpload,
        batch: ReportUploadBatch,
        now: datetime,
    ) -> None:
        try:
            stored = await self.storage.head_object(
                bucket=upload.storage_bucket,
                key=upload.storage_key,
            )
        except S3ObjectNotFoundError:
            if now >= batch.expires_at:
                upload.status = ReportUploadStatus.EXPIRED
                upload.rejection_code = "upload_expired"
                upload.rejected_at = now
            return

        if not stored.version_id or stored.version_id == "null":
            raise S3BucketSecurityError(
                "The uploaded object does not have an immutable S3 version identifier."
            )

        rejection_code = _object_rejection_code(upload, stored)
        if rejection_code is not None:
            await self._reject_object(
                upload=upload,
                version_id=stored.version_id,
                code=rejection_code,
                now=now,
            )
            return

        try:
            content = await self.storage.read_object(
                bucket=upload.storage_bucket,
                key=upload.storage_key,
                version_id=stored.version_id,
                max_bytes=upload.expected_size_bytes,
            )
        except S3ObjectNotFoundError:
            if now >= batch.expires_at:
                upload.status = ReportUploadStatus.EXPIRED
                upload.rejection_code = "upload_expired"
                upload.rejected_at = now
            return

        if len(content) != stored.content_length:
            await self._reject_object(
                upload=upload,
                version_id=stored.version_id,
                code="size_mismatch",
                now=now,
            )
            return

        try:
            validate_report_content(upload.file_extension, content)
        except SuspiciousReportContentError as exc:
            await self._reject_object(
                upload=upload,
                version_id=stored.version_id,
                code=exc.code,
                now=now,
            )
            return

        upload.status = ReportUploadStatus.UPLOADED
        upload.actual_size_bytes = stored.content_length
        upload.storage_version_id = stored.version_id
        upload.etag = stored.etag
        upload.uploaded_at = now
        upload.rejection_code = None
        upload.rejected_at = None

    async def complete_batch(
        self,
        *,
        scope: TenantScope,
        batch_id: UUID,
        dispatcher: ReportProcessingDispatcher,
    ) -> ReportUploadBatchResult | None:
        batch = await self._locked_batch(scope, batch_id)
        if batch is None:
            return None

        uploads = await self._batch_uploads(scope, batch_id)
        if len(uploads) != batch.file_count:
            raise ReportUploadConflictError

        if ReportUploadBatchStatus(batch.status) is ReportUploadBatchStatus.PENDING:
            configured_bucket, _, _ = self._storage_configuration()
            if any(upload.storage_bucket != configured_bucket for upload in uploads):
                raise ReportUploadConflictError
            await self.storage.ensure_bucket_security(configured_bucket)

            now = utc_now()
            for upload in uploads:
                if ReportUploadStatus(upload.status) is ReportUploadStatus.PENDING:
                    await self._verify_pending_upload(
                        upload=upload,
                        batch=batch,
                        now=now,
                    )

            batch.status = _batch_status(uploads)
            if ReportUploadBatchStatus(batch.status) is not ReportUploadBatchStatus.PENDING:
                batch.completed_at = now

        processing = ReportProcessingService(session=self.session)
        jobs = ()
        if ReportUploadBatchStatus(batch.status) is not ReportUploadBatchStatus.PENDING:
            jobs = await processing.ensure_jobs_for_uploads(
                scope=scope,
                uploads=uploads,
            )
        await self.session.commit()
        await processing.dispatch_due_jobs(
            job_ids=(job.id for job in jobs),
            dispatcher=dispatcher,
        )
        return ReportUploadBatchResult(batch=batch, uploads=uploads)


__all__ = [
    "ReportUploadConflictError",
    "ReportUploadRequestRejectedError",
    "ReportUploadService",
    "ReportUploadsDisabledError",
    "S3StorageError",
    "validate_report_content",
    "validate_report_upload_file",
]
