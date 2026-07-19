from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class EmailBackend(StrEnum):
    CONSOLE = "console"
    SES = "ses"


class Settings(BaseSettings):
    """Validated runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        enable_decoding=False,
        extra="ignore",
    )

    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    app_name: str = "Spike Technology API"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str
    db_pool_size: int = 10
    db_max_overflow: int = 20

    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]

    email_backend: EmailBackend = EmailBackend.CONSOLE
    aws_region: str | None = None
    ses_from_email: str | None = None
    email_verification_expire_minutes: int = 10
    password_reset_expire_minutes: int = 10
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    terms_version: str = "v1"

    auth_rate_limit_requests: int = 10
    auth_rate_limit_window_seconds: int = 60

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def parse_list_setting(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> Settings:
        secret = self.jwt_secret_key.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        if self.email_verification_expire_minutes <= 0:
            raise ValueError("EMAIL_VERIFICATION_EXPIRE_MINUTES must be greater than zero")
        if self.password_reset_expire_minutes <= 0:
            raise ValueError("PASSWORD_RESET_EXPIRE_MINUTES must be greater than zero")
        if self.otp_max_attempts <= 0:
            raise ValueError("OTP_MAX_ATTEMPTS must be greater than zero")
        if self.otp_resend_cooldown_seconds < 0:
            raise ValueError("OTP_RESEND_COOLDOWN_SECONDS cannot be negative")
        self.terms_version = self.terms_version.strip()
        if not self.terms_version:
            raise ValueError("TERMS_VERSION cannot be empty")

        if self.app_env is AppEnvironment.PRODUCTION:
            if "replace-this" in secret or "change-me" in secret:
                raise ValueError("A real JWT_SECRET_KEY is required in production")
            if self.email_backend is not EmailBackend.SES:
                raise ValueError("EMAIL_BACKEND must be 'ses' in production")
            if not self.aws_region or not self.ses_from_email:
                raise ValueError("AWS_REGION and SES_FROM_EMAIL are required in production")
            if "*" in self.cors_origins or "*" in self.trusted_hosts:
                raise ValueError(
                    "Wildcard CORS origins and trusted hosts are not allowed in production"
                )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
