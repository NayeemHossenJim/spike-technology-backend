from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import Settings


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class IssuedToken:
    raw_token: str
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class DecodedToken:
    user_id: UUID
    token_id: UUID
    token_type: TokenType
    session_version: int
    expires_at: datetime


password_hasher = PasswordHash.recommended()
dummy_password_hash = password_hasher.hash("not-a-real-password")
dummy_otp_hash = password_hasher.hash("000000")


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def verify_password_against_dummy(password: str) -> None:
    """Equalize timing for login attempts where an account does not exist."""

    password_hasher.verify(password, dummy_password_hash)


def create_six_digit_otp() -> str:
    """Create a cryptographically secure six-digit code and preserve leading zeroes."""

    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(otp: str) -> str:
    """Use a salted Argon2 hash because a six-digit OTP has a small keyspace."""

    return password_hasher.hash(otp)


def verify_otp(otp: str, otp_hash: str) -> bool:
    return password_hasher.verify(otp, otp_hash)


def verify_otp_against_dummy(otp: str) -> None:
    """Equalize timing for OTP validation when no active OTP exists."""

    password_hasher.verify(otp, dummy_otp_hash)


def create_jwt_token(
    *,
    user_id: UUID,
    token_type: TokenType,
    settings: Settings,
    expires_delta: timedelta,
    session_version: int = 0,
) -> IssuedToken:
    now = utc_now()
    token_id = uuid4()
    expires_at = now + expires_delta
    if (
        isinstance(session_version, bool)
        or not isinstance(session_version, int)
        or session_version < 0
    ):
        raise ValueError("Session version must be a non-negative integer.")

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "jti": str(token_id),
        "type": token_type.value,
        "ver": session_version,
        "iat": now,
        "exp": expires_at,
    }
    raw_token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return IssuedToken(raw_token=raw_token, token_id=token_id, expires_at=expires_at)


def decode_jwt_token(token: str, settings: Settings) -> DecodedToken:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "jti", "type", "iat", "exp"]},
        )
        session_version = payload.get("ver", 0)
        if (
            isinstance(session_version, bool)
            or not isinstance(session_version, int)
            or session_version < 0
        ):
            raise ValueError("Invalid session version.")

        return DecodedToken(
            user_id=UUID(payload["sub"]),
            token_id=UUID(payload["jti"]),
            token_type=TokenType(payload["type"]),
            session_version=session_version,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (InvalidTokenError, ValueError, KeyError) as exc:
        raise ValueError("Invalid or expired token") from exc


def create_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
