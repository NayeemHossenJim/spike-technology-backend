from __future__ import annotations

import copy
import json
import math
import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import (
    ADMIN_AUDIT_LABEL_MAX_LENGTH,
    ADMIN_AUDIT_REQUEST_ID_MAX_LENGTH,
    AdminAuditEvent,
)
from app.models.user import User, UserRole

ADMIN_AUDIT_METADATA_MAX_BYTES = 16 * 1024
ADMIN_AUDIT_METADATA_MAX_DEPTH = 5
ADMIN_AUDIT_METADATA_MAX_ITEMS = 128
ADMIN_AUDIT_METADATA_MAX_KEY_LENGTH = 64
ADMIN_AUDIT_METADATA_MAX_STRING_LENGTH = 512

AUDIT_LABEL_PATTERN = re.compile(rf"^[a-z][a-z0-9_.:-]{{0,{ADMIN_AUDIT_LABEL_MAX_LENGTH - 1}}}$")
AUDIT_REQUEST_ID_PATTERN = re.compile(
    rf"^[A-Za-z0-9._:-]{{1,{ADMIN_AUDIT_REQUEST_ID_MAX_LENGTH}}}$"
)

PLATFORM_AUDIT_ROLES = frozenset(
    {
        UserRole.SUPER_ADMIN,
        UserRole.CUSTOMER_SERVICE,
    }
)

SENSITIVE_METADATA_KEY_PARTS = frozenset(
    {
        "password",
        "otp",
        "secret",
        "token",
        "authorization",
        "cookie",
        "prompt",
        "content",
    }
)

SENSITIVE_METADATA_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "raw_payload",
        "raw_request",
        "raw_response",
        "file_contents",
        "message_body",
    }
)


class AdminAuditError(Exception):
    pass


class AdminAuditActorError(AdminAuditError):
    pass


class AdminAuditLabelError(AdminAuditError, ValueError):
    pass


class AdminAuditRequestIdError(AdminAuditError, ValueError):
    pass


class AdminAuditMetadataError(AdminAuditError, ValueError):
    pass


def normalize_admin_audit_label(value: str) -> str:
    normalized = value.strip()

    if not AUDIT_LABEL_PATTERN.fullmatch(normalized):
        raise AdminAuditLabelError

    return normalized


def normalize_admin_audit_request_id(
    request_id: str | None,
) -> str | None:
    if request_id is None:
        return None

    normalized = request_id.strip()

    if not AUDIT_REQUEST_ID_PATTERN.fullmatch(normalized):
        raise AdminAuditRequestIdError

    return normalized


def _metadata_key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")

    if normalized in SENSITIVE_METADATA_KEYS:
        return True

    parts = {part for part in normalized.split("_") if part}

    return bool(parts.intersection(SENSITIVE_METADATA_KEY_PARTS))


def _validate_metadata_value(
    value: object,
    *,
    depth: int,
    item_counter: list[int],
) -> None:
    if depth > ADMIN_AUDIT_METADATA_MAX_DEPTH:
        raise AdminAuditMetadataError("Audit metadata is too deeply nested.")

    if value is None or isinstance(value, bool):
        return

    if isinstance(value, int):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise AdminAuditMetadataError("Audit metadata numbers must be finite.")
        return

    if isinstance(value, str):
        if len(value) > ADMIN_AUDIT_METADATA_MAX_STRING_LENGTH:
            raise AdminAuditMetadataError("Audit metadata string is too long.")
        return

    if isinstance(value, list):
        item_counter[0] += len(value)

        if item_counter[0] > ADMIN_AUDIT_METADATA_MAX_ITEMS:
            raise AdminAuditMetadataError("Audit metadata contains too many items.")

        for item in value:
            _validate_metadata_value(
                item,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    if isinstance(value, dict):
        item_counter[0] += len(value)

        if item_counter[0] > ADMIN_AUDIT_METADATA_MAX_ITEMS:
            raise AdminAuditMetadataError("Audit metadata contains too many items.")

        for key, item in value.items():
            if not isinstance(key, str):
                raise AdminAuditMetadataError("Audit metadata keys must be strings.")

            if not 1 <= len(key) <= ADMIN_AUDIT_METADATA_MAX_KEY_LENGTH:
                raise AdminAuditMetadataError("Audit metadata contains an invalid key.")

            if _metadata_key_is_sensitive(key):
                raise AdminAuditMetadataError("Sensitive fields are forbidden in audit metadata.")

            _validate_metadata_value(
                item,
                depth=depth + 1,
                item_counter=item_counter,
            )
        return

    raise AdminAuditMetadataError("Audit metadata must contain JSON-compatible values only.")


def validate_admin_audit_metadata(
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    if metadata is None:
        return {}

    if not isinstance(metadata, dict):
        raise AdminAuditMetadataError("Audit metadata must be a JSON object.")

    safe_copy = copy.deepcopy(metadata)

    _validate_metadata_value(
        safe_copy,
        depth=0,
        item_counter=[0],
    )

    try:
        serialized = json.dumps(
            safe_copy,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdminAuditMetadataError("Audit metadata must be valid JSON.") from exc

    if len(serialized) > ADMIN_AUDIT_METADATA_MAX_BYTES:
        raise AdminAuditMetadataError("Audit metadata is too large.")

    return safe_copy


def validate_admin_audit_actor(actor: User) -> None:
    if actor.role not in PLATFORM_AUDIT_ROLES or not actor.is_active or not actor.is_verified:
        raise AdminAuditActorError


class AdminAuditService:
    """Append an audit event without committing the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        actor: User,
        action: str,
        target_type: str,
        target_id: UUID,
        business_id: UUID | None = None,
        request_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AdminAuditEvent:
        validate_admin_audit_actor(actor)

        event = AdminAuditEvent(
            actor_user_id=actor.id,
            actor_role=actor.role,
            action=normalize_admin_audit_label(action),
            target_type=normalize_admin_audit_label(target_type),
            target_id=target_id,
            business_id=business_id,
            request_id=normalize_admin_audit_request_id(request_id),
            metadata_json=validate_admin_audit_metadata(metadata),
        )

        self.session.add(event)

        # Intentionally flush only. The privileged mutation and its audit event
        # must be committed or rolled back as one transaction by the caller.
        await self.session.flush()

        return event
