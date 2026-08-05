from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

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

    s3_uploads_enabled: bool = False
    s3_upload_bucket: str | None = None
    s3_upload_prefix: str = "report-uploads"
    s3_presigned_post_expire_minutes: int = 10

    stripe_enabled: bool = False
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_premium_monthly_price_id: str | None = None
    stripe_pro_monthly_price_id: str | None = None
    stripe_checkout_success_url: str = (
        "http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    stripe_checkout_cancel_url: str = "http://localhost:3000/billing/cancel"
    stripe_checkout_session_minutes: int = 60
    stripe_webhook_tolerance_seconds: int = 300

    gemini_enabled: bool = False
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_request_timeout_seconds: int = 60
    gemini_max_output_tokens: int = 4096

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def parse_list_setting(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("gemini_model", mode="before")
    @classmethod
    def normalize_gemini_model(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("s3_upload_bucket", mode="before")
    @classmethod
    def normalize_optional_bucket(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized or None
        return value

    @field_validator("s3_upload_prefix", mode="before")
    @classmethod
    def normalize_upload_prefix(cls, value: object) -> object:
        return value.strip().strip("/") if isinstance(value, str) else value

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
        if not 31 <= self.stripe_checkout_session_minutes <= 1439:
            raise ValueError("STRIPE_CHECKOUT_SESSION_MINUTES must be between 31 and 1439")
        if not 60 <= self.stripe_webhook_tolerance_seconds <= 900:
            raise ValueError("STRIPE_WEBHOOK_TOLERANCE_SECONDS must be between 60 and 900")
        if (
            not self.gemini_model
            or len(self.gemini_model) > 128
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", self.gemini_model)
        ):
            raise ValueError("GEMINI_MODEL is invalid")
        if not 5 <= self.gemini_request_timeout_seconds <= 300:
            raise ValueError("GEMINI_REQUEST_TIMEOUT_SECONDS must be between 5 and 300")
        if not 128 <= self.gemini_max_output_tokens <= 65536:
            raise ValueError("GEMINI_MAX_OUTPUT_TOKENS must be between 128 and 65536")
        if self.gemini_enabled:
            gemini_key = (
                self.gemini_api_key.get_secret_value().strip()
                if self.gemini_api_key is not None
                else ""
            )
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY is required when Gemini AI is enabled")
        self.terms_version = self.terms_version.strip()
        if not self.terms_version:
            raise ValueError("TERMS_VERSION cannot be empty")

        if not 1 <= self.s3_presigned_post_expire_minutes <= 60:
            raise ValueError("S3_PRESIGNED_POST_EXPIRE_MINUTES must be between 1 and 60")
        if (
            not self.s3_upload_prefix
            or len(self.s3_upload_prefix) > 128
            or ".." in self.s3_upload_prefix
            or "//" in self.s3_upload_prefix
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]*", self.s3_upload_prefix)
        ):
            raise ValueError("S3_UPLOAD_PREFIX is invalid")
        if self.s3_uploads_enabled:
            if not self.aws_region or not self.s3_upload_bucket:
                raise ValueError(
                    "AWS_REGION and S3_UPLOAD_BUCKET are required when S3 uploads are enabled"
                )
            bucket = self.s3_upload_bucket
            if (
                not 3 <= len(bucket) <= 63
                or ".." in bucket
                or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                    for label in bucket.split(".")
                )
            ):
                raise ValueError("S3_UPLOAD_BUCKET is not a valid S3 bucket name")
            if bucket.startswith(("xn--", "sthree-", "amzn-s3-demo-")) or bucket.endswith(
                ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")
            ):
                raise ValueError("S3_UPLOAD_BUCKET uses an AWS-reserved name")
            try:
                ipaddress.ip_address(bucket)
            except ValueError:
                pass
            else:
                raise ValueError("S3_UPLOAD_BUCKET cannot be formatted as an IP address")

        if self.stripe_enabled:
            required_values = {
                "STRIPE_SECRET_KEY": (
                    self.stripe_secret_key.get_secret_value()
                    if self.stripe_secret_key is not None
                    else None
                ),
                "STRIPE_WEBHOOK_SECRET": (
                    self.stripe_webhook_secret.get_secret_value()
                    if self.stripe_webhook_secret is not None
                    else None
                ),
                "STRIPE_PREMIUM_MONTHLY_PRICE_ID": self.stripe_premium_monthly_price_id,
                "STRIPE_PRO_MONTHLY_PRICE_ID": self.stripe_pro_monthly_price_id,
            }
            missing = [name for name, value in required_values.items() if not value]
            if missing:
                raise ValueError(
                    "Stripe billing is enabled but required settings are missing: "
                    + ", ".join(missing)
                )

            stripe_secret = required_values["STRIPE_SECRET_KEY"] or ""
            webhook_secret = required_values["STRIPE_WEBHOOK_SECRET"] or ""
            if self.app_env is AppEnvironment.PRODUCTION:
                if not stripe_secret.startswith("sk_live_"):
                    raise ValueError("Production Stripe billing requires an sk_live_ secret key")
            elif not stripe_secret.startswith("sk_test_"):
                raise ValueError("Non-production Stripe billing requires an sk_test_ secret key")
            if not webhook_secret.startswith("whsec_"):
                raise ValueError("STRIPE_WEBHOOK_SECRET must start with whsec_")
            for setting_name in (
                "stripe_premium_monthly_price_id",
                "stripe_pro_monthly_price_id",
            ):
                price_id = getattr(self, setting_name)
                if price_id is None or not price_id.startswith("price_"):
                    raise ValueError(f"{setting_name.upper()} must start with price_")
            if self.stripe_premium_monthly_price_id == self.stripe_pro_monthly_price_id:
                raise ValueError("Premium and Pro must use different Stripe Price IDs")

            for setting_name in (
                "stripe_checkout_success_url",
                "stripe_checkout_cancel_url",
            ):
                url = getattr(self, setting_name)
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError(f"{setting_name.upper()} must be an absolute HTTP(S) URL")
                if self.app_env is AppEnvironment.PRODUCTION and parsed.scheme != "https":
                    raise ValueError(f"{setting_name.upper()} must use HTTPS in production")
            if "{CHECKOUT_SESSION_ID}" not in self.stripe_checkout_success_url:
                raise ValueError("STRIPE_CHECKOUT_SUCCESS_URL must include {CHECKOUT_SESSION_ID}")

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
