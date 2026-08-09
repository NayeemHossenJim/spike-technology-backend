from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.dashboard import DASHBOARD_TITLE_MAX_LENGTH, DashboardType

MAX_DASHBOARD_CONFIGURATION_BYTES = 16 * 1024
MAX_DASHBOARD_CONFIGURATION_DEPTH = 6
MAX_DASHBOARD_CONFIGURATION_ITEMS = 512
MAX_DASHBOARD_CONFIGURATION_KEY_LENGTH = 120


def _validate_json_value(
    value: Any,
    *,
    depth: int = 0,
    item_counter: list[int] | None = None,
) -> None:
    if item_counter is None:
        item_counter = [0]

    if depth > MAX_DASHBOARD_CONFIGURATION_DEPTH:
        raise ValueError("Dashboard configuration is too deeply nested.")

    if isinstance(value, dict):
        for key, nested in value.items():
            item_counter[0] += 1
            if item_counter[0] > MAX_DASHBOARD_CONFIGURATION_ITEMS:
                raise ValueError("Dashboard configuration contains too many items.")
            if not isinstance(key, str):
                raise ValueError("Dashboard configuration keys must be strings.")
            if not 1 <= len(key) <= MAX_DASHBOARD_CONFIGURATION_KEY_LENGTH:
                raise ValueError("Dashboard configuration contains an invalid key.")
            _validate_json_value(
                nested,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    if isinstance(value, list):
        for nested in value:
            item_counter[0] += 1
            if item_counter[0] > MAX_DASHBOARD_CONFIGURATION_ITEMS:
                raise ValueError("Dashboard configuration contains too many items.")
            _validate_json_value(
                nested,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Dashboard configuration numbers must be finite.")
        return

    raise ValueError("Dashboard configuration must contain JSON-compatible values only.")


def validate_dashboard_configuration(value: dict[str, Any]) -> dict[str, Any]:
    _validate_json_value(value)

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Dashboard configuration must be valid JSON.") from exc

    if len(serialized) > MAX_DASHBOARD_CONFIGURATION_BYTES:
        raise ValueError("Dashboard configuration is too large.")

    return value


def normalize_dashboard_title(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= DASHBOARD_TITLE_MAX_LENGTH:
        raise ValueError(
            f"Dashboard title must contain between 1 and {DASHBOARD_TITLE_MAX_LENGTH} characters."
        )
    return normalized


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_type: DashboardType
    title: str = Field(max_length=DASHBOARD_TITLE_MAX_LENGTH)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_dashboard_title(value)

    @field_validator("configuration")
    @classmethod
    def validate_configuration(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return validate_dashboard_configuration(value)


class DashboardUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        max_length=DASHBOARD_TITLE_MAX_LENGTH,
    )
    configuration: dict[str, Any] | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Dashboard title cannot be null.")
        return normalize_dashboard_title(value)

    @field_validator("configuration")
    @classmethod
    def validate_configuration(
        cls,
        value: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if value is None:
            raise ValueError("Dashboard configuration cannot be null.")
        return validate_dashboard_configuration(value)

    @model_validator(mode="after")
    def require_change(self) -> DashboardUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one dashboard field must be updated.")
        return self


class DashboardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    created_by_user_id: UUID
    dashboard_type: DashboardType
    title: str
    configuration: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DashboardPageRead(BaseModel):
    items: list[DashboardRead]
    total: int
    limit: int
    offset: int


class DashboardSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    dashboard_id: UUID
    version: int
    schema_version: int
    payload: dict[str, Any]
    content_hash: str
    created_by_user_id: UUID
    created_at: datetime


class DashboardSnapshotSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    snapshot_id: UUID
    processing_job_id: UUID
    source_position: int
    normalized_storage_version_id: str
    normalized_etag: str
    profile_storage_version_id: str
    profile_etag: str
    created_at: datetime
