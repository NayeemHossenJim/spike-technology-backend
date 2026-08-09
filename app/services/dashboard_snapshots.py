from __future__ import annotations

import copy
import gzip
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.dashboard import (
    DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
    Dashboard,
    DashboardSnapshot,
    DashboardSnapshotSource,
    DashboardType,
)
from app.models.processing import ReportProcessingJob, ReportProcessingStatus
from app.services.report_parser import (
    REPORT_ARTIFACT_FORMAT_VERSION,
    REPORT_MAX_NORMALIZED_UNCOMPRESSED_BYTES,
    REPORT_MAX_PROFILE_BYTES,
)
from app.services.s3_storage import (
    S3ObjectNotFoundError,
    S3StorageError,
    S3UploadGateway,
)
from app.services.tenant import TenantScope

DASHBOARD_SNAPSHOT_FORMAT = "spike.dashboard-snapshot"
DASHBOARD_MAX_SNAPSHOT_SOURCES = 20
DASHBOARD_MAX_SNAPSHOT_PAYLOAD_BYTES = 4 * 1024 * 1024
DASHBOARD_MAX_NORMALIZED_LINE_BYTES = 16 * 1024 * 1024

_STRICT_DECIMAL_TEXT = re.compile(r"^[+-]?(?:\d{1,40})(?:\.\d{1,20})?$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class DashboardSnapshotNotFoundError(Exception):
    pass


class DashboardSnapshotSourceNotFoundError(Exception):
    pass


class DashboardSnapshotSourceNotReadyError(Exception):
    pass


class DashboardSnapshotArtifactError(Exception):
    pass


class DashboardSnapshotStorageError(Exception):
    pass


class DashboardSnapshotConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DashboardSnapshotMaterialization:
    snapshot: DashboardSnapshot
    sources: tuple[DashboardSnapshotSource, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class DashboardSnapshotPage:
    items: tuple[DashboardSnapshot, ...]
    total: int
    limit: int
    offset: int


@dataclass(slots=True)
class _NumericAccumulator:
    count: int = 0
    total: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def observe(self, value: Decimal) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)


@dataclass(slots=True)
class _ColumnAccumulator:
    non_null_count: int = 0
    null_count: int = 0
    numeric: _NumericAccumulator | None = None

    def observe(self, value: object) -> None:
        if value is None:
            self.null_count += 1
            return

        self.non_null_count += 1
        numeric = _strict_decimal(value)
        if numeric is None:
            return

        if self.numeric is None:
            self.numeric = _NumericAccumulator()
        self.numeric.observe(numeric)


@dataclass(frozen=True, slots=True)
class _SourceArtifact:
    job: ReportProcessingJob
    profile: dict[str, Any]
    aggregate: dict[str, Any]


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DashboardSnapshotArtifactError from exc


def dashboard_snapshot_content_hash(payload: dict[str, Any]) -> str:
    return sha256(_canonical_json_bytes(payload)).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"

    normalized = value.normalize()
    rendered = format(normalized, "f")

    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")

    if rendered == "-0":
        return "0"
    return rendered


def _strict_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))

    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not _STRICT_DECIMAL_TEXT.fullmatch(candidate):
        return None

    try:
        parsed = Decimal(candidate)
    except InvalidOperation:
        return None

    return parsed if parsed.is_finite() else None


def _render_numeric(
    accumulator: _NumericAccumulator | None,
) -> dict[str, object] | None:
    if accumulator is None or accumulator.count == 0:
        return None

    with localcontext() as context:
        context.prec = 50
        average = accumulator.total / Decimal(accumulator.count)

    assert accumulator.minimum is not None
    assert accumulator.maximum is not None

    return {
        "count": accumulator.count,
        "sum_decimal": _decimal_text(accumulator.total),
        "min_decimal": _decimal_text(accumulator.minimum),
        "max_decimal": _decimal_text(accumulator.maximum),
        "average_decimal": _decimal_text(average),
    }


def _required_job_string(
    job: ReportProcessingJob,
    attribute: str,
) -> str:
    value = getattr(job, attribute)
    if not isinstance(value, str) or not value.strip():
        raise DashboardSnapshotSourceNotReadyError
    return value


def _job_signature(job: ReportProcessingJob) -> tuple[object, ...]:
    return (
        ReportProcessingStatus(job.status),
        job.result_storage_bucket,
        job.normalized_storage_key,
        job.normalized_storage_version_id,
        job.normalized_etag,
        job.normalized_size_bytes,
        job.profile_storage_key,
        job.profile_storage_version_id,
        job.profile_etag,
        job.record_count,
        job.sheet_count,
    )


def _validate_completed_job(job: ReportProcessingJob) -> None:
    if ReportProcessingStatus(job.status) is not ReportProcessingStatus.COMPLETED:
        raise DashboardSnapshotSourceNotReadyError

    _required_job_string(job, "result_storage_bucket")
    _required_job_string(job, "normalized_storage_key")
    _required_job_string(job, "normalized_storage_version_id")
    _required_job_string(job, "normalized_etag")
    _required_job_string(job, "profile_storage_key")
    _required_job_string(job, "profile_storage_version_id")
    _required_job_string(job, "profile_etag")

    if (
        job.normalized_size_bytes is None
        or job.normalized_size_bytes <= 0
        or job.record_count is None
        or job.record_count < 0
        or job.sheet_count is None
        or job.sheet_count <= 0
    ):
        raise DashboardSnapshotSourceNotReadyError


def _profile_sheet_contract(
    profile: dict[str, Any],
) -> dict[tuple[int, str], tuple[str, ...]]:
    sheets = profile.get("sheets")
    if not isinstance(sheets, list):
        raise DashboardSnapshotArtifactError

    result: dict[tuple[int, str], tuple[str, ...]] = {}

    for sheet in sheets:
        if not isinstance(sheet, dict):
            raise DashboardSnapshotArtifactError

        sheet_index = sheet.get("index")
        sheet_name = sheet.get("name")
        columns = sheet.get("columns")
        record_count = sheet.get("record_count")

        if (
            isinstance(sheet_index, bool)
            or not isinstance(sheet_index, int)
            or sheet_index < 0
            or not isinstance(sheet_name, str)
            or not sheet_name
            or isinstance(record_count, bool)
            or not isinstance(record_count, int)
            or record_count < 0
            or not isinstance(columns, list)
        ):
            raise DashboardSnapshotArtifactError

        names: list[str] = []

        for column in columns:
            if not isinstance(column, dict):
                raise DashboardSnapshotArtifactError

            name = column.get("name")
            if not isinstance(name, str) or not name:
                raise DashboardSnapshotArtifactError
            names.append(name)

        key = (sheet_index, sheet_name)
        if key in result or len(set(names)) != len(names):
            raise DashboardSnapshotArtifactError

        result[key] = tuple(names)

    return result


def _validate_profile(
    job: ReportProcessingJob,
    content: bytes,
) -> dict[str, Any]:
    if len(content) > REPORT_MAX_PROFILE_BYTES:
        raise DashboardSnapshotArtifactError

    try:
        decoded = content.decode("utf-8")
        profile = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DashboardSnapshotArtifactError from exc

    if not isinstance(profile, dict):
        raise DashboardSnapshotArtifactError

    normalized = profile.get("normalized")
    if not isinstance(normalized, dict):
        raise DashboardSnapshotArtifactError

    normalized_sha256 = normalized.get("sha256")

    if (
        profile.get("format") != "spike.report-profile"
        or profile.get("version") != REPORT_ARTIFACT_FORMAT_VERSION
        or profile.get("job_id") != str(job.id)
        or profile.get("business_id") != str(job.business_id)
        or profile.get("record_count") != job.record_count
        or profile.get("sheet_count") != job.sheet_count
        or normalized.get("format") != "jsonl"
        or normalized.get("compression") != "gzip"
        or not isinstance(normalized_sha256, str)
        or not _SHA256_HEX.fullmatch(normalized_sha256)
    ):
        raise DashboardSnapshotArtifactError

    sheet_contract = _profile_sheet_contract(profile)

    if len(sheet_contract) != job.sheet_count:
        raise DashboardSnapshotArtifactError

    return profile


def _aggregate_normalized(
    *,
    job: ReportProcessingJob,
    profile: dict[str, Any],
    compressed_content: bytes,
) -> dict[str, Any]:
    if job.normalized_size_bytes is None or len(compressed_content) != job.normalized_size_bytes:
        raise DashboardSnapshotArtifactError

    normalized = profile["normalized"]
    expected_digest = normalized["sha256"]

    if sha256(compressed_content).hexdigest() != expected_digest:
        raise DashboardSnapshotArtifactError

    sheet_contract = _profile_sheet_contract(profile)

    accumulators: dict[
        tuple[int, str],
        dict[str, _ColumnAccumulator],
    ] = {
        key: {column_name: _ColumnAccumulator() for column_name in column_names}
        for key, column_names in sheet_contract.items()
    }

    row_counts = {key: 0 for key in sheet_contract}
    total_uncompressed = 0
    total_records = 0

    try:
        with gzip.GzipFile(
            fileobj=BytesIO(compressed_content),
            mode="rb",
        ) as stream:
            while True:
                line = stream.readline(DASHBOARD_MAX_NORMALIZED_LINE_BYTES + 1)

                if not line:
                    break

                if len(line) > DASHBOARD_MAX_NORMALIZED_LINE_BYTES:
                    raise DashboardSnapshotArtifactError

                total_uncompressed += len(line)
                if total_uncompressed > REPORT_MAX_NORMALIZED_UNCOMPRESSED_BYTES:
                    raise DashboardSnapshotArtifactError

                try:
                    row = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DashboardSnapshotArtifactError from exc

                if not isinstance(row, dict):
                    raise DashboardSnapshotArtifactError

                sheet_index = row.get("sheet_index")
                sheet_name = row.get("sheet_name")
                values = row.get("values")

                if (
                    isinstance(sheet_index, bool)
                    or not isinstance(sheet_index, int)
                    or not isinstance(sheet_name, str)
                    or not isinstance(values, dict)
                ):
                    raise DashboardSnapshotArtifactError

                key = (sheet_index, sheet_name)
                expected_columns = sheet_contract.get(key)

                if expected_columns is None:
                    raise DashboardSnapshotArtifactError

                if set(values) != set(expected_columns):
                    raise DashboardSnapshotArtifactError

                row_counts[key] += 1
                total_records += 1

                for column_name in expected_columns:
                    accumulators[key][column_name].observe(values[column_name])
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise DashboardSnapshotArtifactError from exc

    if total_records != job.record_count:
        raise DashboardSnapshotArtifactError

    profile_sheets = {(sheet["index"], sheet["name"]): sheet for sheet in profile["sheets"]}

    rendered_sheets: list[dict[str, Any]] = []

    for key in sorted(
        sheet_contract,
        key=lambda item: (item[0], item[1]),
    ):
        sheet_index, sheet_name = key
        profile_sheet = profile_sheets[key]

        if row_counts[key] != profile_sheet["record_count"]:
            raise DashboardSnapshotArtifactError

        rendered_columns: list[dict[str, Any]] = []

        for column_name in sheet_contract[key]:
            accumulator = accumulators[key][column_name]

            rendered_columns.append(
                {
                    "name": column_name,
                    "non_null_count": accumulator.non_null_count,
                    "null_count": accumulator.null_count,
                    "numeric": _render_numeric(accumulator.numeric),
                }
            )

        rendered_sheets.append(
            {
                "sheet_index": sheet_index,
                "sheet_name": sheet_name,
                "record_count": row_counts[key],
                "columns": rendered_columns,
            }
        )

    return {
        "record_count": total_records,
        "sheet_count": len(rendered_sheets),
        "sheets": rendered_sheets,
    }


async def _read_source_artifact(
    *,
    storage: S3UploadGateway,
    job: ReportProcessingJob,
) -> _SourceArtifact:
    _validate_completed_job(job)

    bucket = _required_job_string(job, "result_storage_bucket")
    profile_key = _required_job_string(job, "profile_storage_key")
    profile_version = _required_job_string(
        job,
        "profile_storage_version_id",
    )
    normalized_key = _required_job_string(job, "normalized_storage_key")
    normalized_version = _required_job_string(
        job,
        "normalized_storage_version_id",
    )

    assert job.normalized_size_bytes is not None

    try:
        profile_content = await storage.read_object(
            bucket=bucket,
            key=profile_key,
            version_id=profile_version,
            max_bytes=REPORT_MAX_PROFILE_BYTES,
        )
        normalized_content = await storage.read_object(
            bucket=bucket,
            key=normalized_key,
            version_id=normalized_version,
            max_bytes=job.normalized_size_bytes,
        )
    except S3ObjectNotFoundError as exc:
        raise DashboardSnapshotArtifactError from exc
    except S3StorageError as exc:
        raise DashboardSnapshotStorageError from exc

    profile = _validate_profile(job, profile_content)
    aggregate = _aggregate_normalized(
        job=job,
        profile=profile,
        compressed_content=normalized_content,
    )

    return _SourceArtifact(
        job=job,
        profile=profile,
        aggregate=aggregate,
    )


def _source_payload(source: _SourceArtifact) -> dict[str, Any]:
    job = source.job
    profile_source = source.profile.get("source")

    if not isinstance(profile_source, dict):
        raise DashboardSnapshotArtifactError

    filename = profile_source.get("filename")
    report_upload_id = profile_source.get("report_upload_id")

    if (
        not isinstance(filename, str)
        or not filename
        or report_upload_id != str(job.report_upload_id)
    ):
        raise DashboardSnapshotArtifactError

    return {
        "processing_job_id": str(job.id),
        "report_upload_id": str(job.report_upload_id),
        "filename": filename,
        "normalized_storage_version_id": job.normalized_storage_version_id,
        "normalized_etag": job.normalized_etag,
        "profile_storage_version_id": job.profile_storage_version_id,
        "profile_etag": job.profile_etag,
        "record_count": job.record_count,
        "sheet_count": job.sheet_count,
        "aggregate": source.aggregate,
    }


def build_dashboard_snapshot_payload(
    *,
    dashboard_id: UUID,
    dashboard_type: DashboardType,
    title: str,
    configuration: dict[str, object],
    sources: tuple[_SourceArtifact, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": DASHBOARD_SNAPSHOT_FORMAT,
        "schema_version": DASHBOARD_SNAPSHOT_SCHEMA_VERSION,
        "dashboard": {
            "id": str(dashboard_id),
            "type": DashboardType(dashboard_type).value,
            "title": title,
            "configuration": copy.deepcopy(configuration),
        },
        "source_count": len(sources),
        "record_count": sum(int(source.job.record_count or 0) for source in sources),
        "sheet_count": sum(int(source.job.sheet_count or 0) for source in sources),
        "sources": [_source_payload(source) for source in sources],
    }

    if len(_canonical_json_bytes(payload)) > DASHBOARD_MAX_SNAPSHOT_PAYLOAD_BYTES:
        raise DashboardSnapshotArtifactError

    return payload


class DashboardSnapshotService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: S3UploadGateway,
    ) -> None:
        self.session = session
        self.storage = storage

    async def _dashboard(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
    ) -> Dashboard:
        dashboard = await scope.get(
            self.session,
            Dashboard,
            dashboard_id,
        )
        if dashboard is None:
            raise DashboardSnapshotNotFoundError
        return dashboard

    async def _source_jobs(
        self,
        scope: TenantScope,
        source_ids: tuple[UUID, ...],
    ) -> tuple[ReportProcessingJob, ...]:
        result = await self.session.execute(
            scope.select(
                ReportProcessingJob,
                ReportProcessingJob.id.in_(source_ids),
            )
        )
        jobs = tuple(result.scalars().all())

        if len(jobs) != len(source_ids):
            raise DashboardSnapshotSourceNotFoundError

        by_id = {job.id: job for job in jobs}

        try:
            ordered = tuple(by_id[source_id] for source_id in source_ids)
        except KeyError as exc:
            raise DashboardSnapshotSourceNotFoundError from exc

        for job in ordered:
            _validate_completed_job(job)

        return ordered

    async def materialize(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
        *,
        created_by_user_id: UUID,
        source_processing_job_ids: tuple[UUID, ...],
    ) -> DashboardSnapshotMaterialization:
        if (
            not source_processing_job_ids
            or len(source_processing_job_ids) > DASHBOARD_MAX_SNAPSHOT_SOURCES
            or len(set(source_processing_job_ids)) != len(source_processing_job_ids)
        ):
            raise ValueError("Dashboard snapshot sources are invalid.")

        # Stable ordering makes the same logical source set deterministic even
        # when callers submit the IDs in a different order.
        source_ids = tuple(
            sorted(
                source_processing_job_ids,
                key=str,
            )
        )

        dashboard = await self._dashboard(scope, dashboard_id)
        jobs = await self._source_jobs(scope, source_ids)

        captured_dashboard = (
            DashboardType(dashboard.dashboard_type),
            dashboard.title,
            copy.deepcopy(dashboard.configuration),
            dashboard.updated_at,
        )
        captured_signatures = {job.id: _job_signature(job) for job in jobs}

        # Close the read transaction before potentially large S3 reads.
        await self.session.commit()

        artifacts: list[_SourceArtifact] = []
        for job in jobs:
            artifacts.append(
                await _read_source_artifact(
                    storage=self.storage,
                    job=job,
                )
            )

        dashboard_type, title, configuration, dashboard_updated_at = captured_dashboard

        payload = build_dashboard_snapshot_payload(
            dashboard_id=dashboard_id,
            dashboard_type=dashboard_type,
            title=title,
            configuration=configuration,
            sources=tuple(artifacts),
        )
        content_hash = dashboard_snapshot_content_hash(payload)

        locked_result = await self.session.execute(
            scope.select(
                Dashboard,
                Dashboard.id == dashboard_id,
            ).with_for_update()
        )
        locked_dashboard = locked_result.scalar_one_or_none()

        if locked_dashboard is None:
            await self.session.rollback()
            raise DashboardSnapshotNotFoundError

        if (
            locked_dashboard.updated_at != dashboard_updated_at
            or DashboardType(locked_dashboard.dashboard_type) is not dashboard_type
            or locked_dashboard.title != title
            or locked_dashboard.configuration != configuration
        ):
            await self.session.rollback()
            raise DashboardSnapshotConflictError

        current_jobs = await self._source_jobs(scope, source_ids)

        if any(captured_signatures[job.id] != _job_signature(job) for job in current_jobs):
            await self.session.rollback()
            raise DashboardSnapshotConflictError

        latest_result = await self.session.execute(
            scope.select(
                DashboardSnapshot,
                DashboardSnapshot.dashboard_id == dashboard_id,
            )
            .order_by(DashboardSnapshot.version.desc())
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()

        if latest is not None and latest.content_hash == content_hash:
            sources = await self._snapshot_sources(
                scope,
                latest.id,
            )
            await self.session.commit()
            return DashboardSnapshotMaterialization(
                snapshot=latest,
                sources=sources,
                replayed=True,
            )

        version = 1 if latest is None else latest.version + 1

        snapshot = DashboardSnapshot(
            business_id=scope.business_id,
            dashboard_id=dashboard_id,
            version=version,
            payload=payload,
            content_hash=content_hash,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(snapshot)

        try:
            await self.session.flush()

            snapshot_sources = tuple(
                DashboardSnapshotSource(
                    business_id=scope.business_id,
                    snapshot_id=snapshot.id,
                    processing_job_id=job.id,
                    source_position=position,
                    normalized_storage_version_id=(
                        _required_job_string(
                            job,
                            "normalized_storage_version_id",
                        )
                    ),
                    normalized_etag=_required_job_string(
                        job,
                        "normalized_etag",
                    ),
                    profile_storage_version_id=(
                        _required_job_string(
                            job,
                            "profile_storage_version_id",
                        )
                    ),
                    profile_etag=_required_job_string(
                        job,
                        "profile_etag",
                    ),
                )
                for position, job in enumerate(current_jobs)
            )

            self.session.add_all(snapshot_sources)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DashboardSnapshotConflictError from exc

        return DashboardSnapshotMaterialization(
            snapshot=snapshot,
            sources=snapshot_sources,
            replayed=False,
        )

    async def _snapshot_sources(
        self,
        scope: TenantScope,
        snapshot_id: UUID,
    ) -> tuple[DashboardSnapshotSource, ...]:
        result = await self.session.execute(
            scope.select(
                DashboardSnapshotSource,
                DashboardSnapshotSource.snapshot_id == snapshot_id,
            ).order_by(DashboardSnapshotSource.source_position)
        )
        return tuple(result.scalars().all())

    async def latest(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
    ) -> DashboardSnapshotMaterialization:
        await self._dashboard(scope, dashboard_id)

        result = await self.session.execute(
            scope.select(
                DashboardSnapshot,
                DashboardSnapshot.dashboard_id == dashboard_id,
            )
            .order_by(DashboardSnapshot.version.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()

        if snapshot is None:
            raise DashboardSnapshotNotFoundError

        return DashboardSnapshotMaterialization(
            snapshot=snapshot,
            sources=await self._snapshot_sources(
                scope,
                snapshot.id,
            ),
            replayed=False,
        )

    async def list(
        self,
        scope: TenantScope,
        dashboard_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> DashboardSnapshotPage:
        await self._dashboard(scope, dashboard_id)

        total_result = await self.session.execute(
            select(DashboardSnapshot.id).where(
                DashboardSnapshot.business_id == scope.business_id,
                DashboardSnapshot.dashboard_id == dashboard_id,
            )
        )
        total = len(total_result.scalars().all())

        result = await self.session.execute(
            scope.select(
                DashboardSnapshot,
                DashboardSnapshot.dashboard_id == dashboard_id,
            )
            .order_by(DashboardSnapshot.version.desc())
            .offset(offset)
            .limit(limit)
        )

        return DashboardSnapshotPage(
            items=tuple(result.scalars().all()),
            total=total,
            limit=limit,
            offset=offset,
        )


__all__ = [
    "DASHBOARD_MAX_SNAPSHOT_PAYLOAD_BYTES",
    "DASHBOARD_MAX_SNAPSHOT_SOURCES",
    "DASHBOARD_SNAPSHOT_FORMAT",
    "DashboardSnapshotArtifactError",
    "DashboardSnapshotConflictError",
    "DashboardSnapshotMaterialization",
    "DashboardSnapshotNotFoundError",
    "DashboardSnapshotPage",
    "DashboardSnapshotService",
    "DashboardSnapshotSourceNotFoundError",
    "DashboardSnapshotSourceNotReadyError",
    "DashboardSnapshotStorageError",
    "build_dashboard_snapshot_payload",
    "dashboard_snapshot_content_hash",
]
