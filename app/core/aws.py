from __future__ import annotations

from botocore.config import Config

from app.core.config import Settings


def build_aws_client_config(
    settings: Settings,
    *,
    signature_version: str | None = None,
) -> Config:
    kwargs: dict[str, object] = {
        "connect_timeout": settings.aws_connect_timeout_seconds,
        "read_timeout": settings.aws_read_timeout_seconds,
        "retries": {
            "mode": "standard",
            "max_attempts": settings.aws_max_attempts,
        },
    }
    if signature_version is not None:
        kwargs["signature_version"] = signature_version
    return Config(**kwargs)
