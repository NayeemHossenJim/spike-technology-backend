from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

import boto3
from asyncer import asyncify

from app.core.aws import build_aws_client_config
from app.core.config import EmailBackend, Settings, get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send_verification_otp(self, recipient: str, otp: str) -> None: ...

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None: ...


class ConsoleEmailSender:
    """Development-only sender. It never runs when APP_ENV=production."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_verification_otp(self, recipient: str, otp: str) -> None:
        logger.info(
            "Development signup-verification OTP for %s: %s. Expires in %s minutes.",
            recipient,
            otp,
            self.settings.email_verification_expire_minutes,
        )

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None:
        logger.info(
            "Development password-reset OTP for %s: %s. Expires in %s minutes.",
            recipient,
            otp,
            self.settings.password_reset_expire_minutes,
        )


class SesEmailSender:
    def __init__(self, settings: Settings) -> None:
        if not settings.aws_region or not settings.ses_from_email:
            raise ValueError("AWS_REGION and SES_FROM_EMAIL are required for AWS SES")
        self.settings = settings
        self.client = boto3.client(
            "sesv2",
            region_name=settings.aws_region,
            config=build_aws_client_config(settings),
        )

    async def _send(self, *, recipient: str, subject: str, body: str) -> None:
        await asyncify(self.client.send_email)(
            FromEmailAddress=self.settings.ses_from_email,
            Destination={"ToAddresses": [recipient]},
            Content={
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                }
            },
        )

    async def send_verification_otp(self, recipient: str, otp: str) -> None:
        await self._send(
            recipient=recipient,
            subject="Verify your Spike Technology account",
            body=(
                "Welcome to Spike Technology. Your verification code is: "
                f"{otp}\n\n"
                "This code expires in "
                f"{self.settings.email_verification_expire_minutes} minutes."
            ),
        )

    async def send_password_reset_otp(self, recipient: str, otp: str) -> None:
        await self._send(
            recipient=recipient,
            subject="Reset your Spike Technology password",
            body=(
                "Your password-reset code is: "
                f"{otp}\n\n"
                "This code expires in "
                f"{self.settings.password_reset_expire_minutes} minutes. "
                "If you did not request this, you can ignore this email."
            ),
        )


@lru_cache
def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_backend is EmailBackend.SES:
        return SesEmailSender(settings)
    return ConsoleEmailSender(settings)
