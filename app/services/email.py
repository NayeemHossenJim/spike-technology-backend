from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

import boto3
from asyncer import asyncify

from app.core.config import EmailBackend, Settings, get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send_verification_email(self, recipient: str, token: str) -> None: ...

    async def send_password_reset_email(self, recipient: str, token: str) -> None: ...


class ConsoleEmailSender:
    """Development-only sender. It never runs when APP_ENV=production."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_verification_email(self, recipient: str, token: str) -> None:
        verification_url = f"{self.settings.frontend_url}/verify-email?token={token}"
        logger.info(
            "Development verification email for %s. Verification URL: %s",
            recipient,
            verification_url,
        )

    async def send_password_reset_email(self, recipient: str, token: str) -> None:
        password_reset_url = f"{self.settings.frontend_url}/reset-password?token={token}"
        logger.info(
            "Development password reset email for %s. Password-reset URL: %s",
            recipient,
            password_reset_url,
        )


class SesEmailSender:
    def __init__(self, settings: Settings) -> None:
        if not settings.aws_region or not settings.ses_from_email:
            raise ValueError("AWS_REGION and SES_FROM_EMAIL are required for AWS SES")
        self.settings = settings
        self.client = boto3.client("sesv2", region_name=settings.aws_region)

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

    async def send_verification_email(self, recipient: str, token: str) -> None:
        url = f"{self.settings.frontend_url}/verify-email?token={token}"
        await self._send(
            recipient=recipient,
            subject="Verify your Spike Technology account",
            body=(
                "Welcome to Spike Technology. Verify your email address by opening this link: "
                f"{url}\n\n"
                "This link expires in "
                f"{self.settings.email_verification_expire_minutes} minutes."
            ),
        )

    async def send_password_reset_email(self, recipient: str, token: str) -> None:
        url = f"{self.settings.frontend_url}/reset-password?token={token}"
        await self._send(
            recipient=recipient,
            subject="Reset your Spike Technology password",
            body=(
                "Reset your password by opening this link: "
                f"{url}\n\n"
                "This link expires in "
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
