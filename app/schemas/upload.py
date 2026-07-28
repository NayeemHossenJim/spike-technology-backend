from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.upload import (
    MAX_REPORT_UPLOAD_BYTES,
    MAX_REPORT_UPLOAD_FILES_PER_BATCH,
    ReportUploadBatchStatus,
    ReportUploadStatus,
)


class ReportUploadFileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=MAX_REPORT_UPLOAD_BYTES)

    @field_validator("content_type", mode="before")
    @classmethod
    def normalize_content_type(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class ReportUploadBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    files: list[ReportUploadFileCreate] = Field(
        min_length=1,
        max_length=MAX_REPORT_UPLOAD_FILES_PER_BATCH,
    )

    @model_validator(mode="after")
    def reject_duplicate_filenames(self) -> ReportUploadBatchCreate:
        filenames = [item.filename.casefold() for item in self.files]
        if len(filenames) != len(set(filenames)):
            raise ValueError("A batch cannot contain duplicate filenames.")
        return self


class PresignedPostRead(BaseModel):
    method: str = "POST"
    url: str
    fields: dict[str, str]


class ReportUploadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    original_filename: str
    content_type: str
    expected_size_bytes: int
    actual_size_bytes: int | None
    status: ReportUploadStatus
    expires_at: datetime
    uploaded_at: datetime | None
    rejection_code: str | None


class PresignedReportUploadRead(ReportUploadRead):
    upload: PresignedPostRead


class ReportUploadBatchRead(BaseModel):
    id: UUID
    status: ReportUploadBatchStatus
    expires_at: datetime
    completed_at: datetime | None
    files: list[ReportUploadRead]


class ReportUploadBatchCreatedRead(BaseModel):
    id: UUID
    status: ReportUploadBatchStatus
    expires_at: datetime
    completed_at: datetime | None
    files: list[PresignedReportUploadRead]
