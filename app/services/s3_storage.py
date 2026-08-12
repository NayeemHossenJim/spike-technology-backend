from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

import boto3
from asyncer import asyncify
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from app.core.aws import build_aws_client_config
from app.core.config import Settings, get_settings

S3_BUCKET_SECURITY_CACHE_SECONDS = 60
S3_ENCRYPTION_ALGORITHM = "AES256"
S3_CACHE_CONTROL = "no-store"


class S3StorageError(Exception):
    pass


class S3BucketSecurityError(S3StorageError):
    pass


class S3ObjectNotFoundError(S3StorageError):
    pass


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class StoredS3Object:
    content_length: int
    content_type: str | None
    cache_control: str | None
    server_side_encryption: str | None
    metadata: dict[str, str]
    etag: str | None
    version_id: str | None
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class WrittenS3Object:
    content_length: int
    etag: str
    version_id: str


class S3UploadGateway(Protocol):
    async def ensure_bucket_security(self, bucket: str) -> None: ...

    async def create_presigned_post(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        expected_size_bytes: int,
        business_id: UUID,
        batch_id: UUID,
        upload_id: UUID,
        expires_in_seconds: int,
    ) -> PresignedPost: ...

    async def head_object(self, *, bucket: str, key: str) -> StoredS3Object: ...

    async def read_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        max_bytes: int,
    ) -> bytes: ...

    async def write_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
        content_encoding: str | None,
        metadata: dict[str, str],
    ) -> WrittenS3Object: ...

    async def delete_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
    ) -> None: ...


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class Boto3S3UploadGateway:
    def __init__(
        self,
        settings: Settings,
        *,
        client: BaseClient | Any | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self._security_checked_bucket: str | None = None
        self._security_checked_at = 0.0
        self._security_lock = asyncio.Lock()

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                region_name=self.settings.aws_region,
                config=build_aws_client_config(
                    self.settings,
                    signature_version="s3v4",
                ),
            )
        return self._client

    def _check_bucket_security(self, bucket: str) -> None:
        public_access = self.client.get_public_access_block(Bucket=bucket).get(
            "PublicAccessBlockConfiguration",
            {},
        )
        required_public_blocks = (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
        if not all(public_access.get(setting) is True for setting in required_public_blocks):
            raise S3BucketSecurityError(
                "The upload bucket must enable all four S3 Block Public Access settings."
            )

        ownership_rules = (
            self.client.get_bucket_ownership_controls(Bucket=bucket)
            .get(
                "OwnershipControls",
                {},
            )
            .get("Rules", [])
        )
        ownership_is_enforced = any(
            rule.get("ObjectOwnership") == "BucketOwnerEnforced" for rule in ownership_rules
        )
        if not ownership_is_enforced:
            raise S3BucketSecurityError(
                "The upload bucket must use Bucket owner enforced object ownership."
            )

        versioning = self.client.get_bucket_versioning(Bucket=bucket)
        if versioning.get("Status") != "Enabled":
            raise S3BucketSecurityError("The upload bucket must have versioning enabled.")

        try:
            policy_status = self.client.get_bucket_policy_status(Bucket=bucket)
        except ClientError as exc:
            if _client_error_code(exc) != "NoSuchBucketPolicy":
                raise
        else:
            if policy_status.get("PolicyStatus", {}).get("IsPublic") is True:
                raise S3BucketSecurityError("The upload bucket policy must not be public.")

    async def ensure_bucket_security(self, bucket: str) -> None:
        if (
            self._security_checked_bucket == bucket
            and time.monotonic() - self._security_checked_at < S3_BUCKET_SECURITY_CACHE_SECONDS
        ):
            return
        async with self._security_lock:
            if (
                self._security_checked_bucket == bucket
                and time.monotonic() - self._security_checked_at < S3_BUCKET_SECURITY_CACHE_SECONDS
            ):
                return
            try:
                await asyncify(self._check_bucket_security)(bucket)
            except S3BucketSecurityError:
                raise
            except (BotoCoreError, ClientError, OSError) as exc:
                raise S3StorageError("Unable to verify S3 upload-bucket security.") from exc
            self._security_checked_bucket = bucket
            self._security_checked_at = time.monotonic()

    async def create_presigned_post(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        expected_size_bytes: int,
        business_id: UUID,
        batch_id: UUID,
        upload_id: UUID,
        expires_in_seconds: int,
    ) -> PresignedPost:
        fields = {
            "Content-Type": content_type,
            "Cache-Control": S3_CACHE_CONTROL,
            "success_action_status": "201",
            "x-amz-server-side-encryption": S3_ENCRYPTION_ALGORITHM,
            "x-amz-meta-business-id": str(business_id),
            "x-amz-meta-batch-id": str(batch_id),
            "x-amz-meta-upload-id": str(upload_id),
            "x-amz-meta-expected-size": str(expected_size_bytes),
        }
        conditions: list[dict[str, str] | list[str | int]] = [
            {"Content-Type": content_type},
            {"Cache-Control": S3_CACHE_CONTROL},
            {"success_action_status": "201"},
            {"x-amz-server-side-encryption": S3_ENCRYPTION_ALGORITHM},
            {"x-amz-meta-business-id": str(business_id)},
            {"x-amz-meta-batch-id": str(batch_id)},
            {"x-amz-meta-upload-id": str(upload_id)},
            {"x-amz-meta-expected-size": str(expected_size_bytes)},
            ["content-length-range", expected_size_bytes, expected_size_bytes],
        ]
        try:
            result = await asyncify(self.client.generate_presigned_post)(
                Bucket=bucket,
                Key=key,
                Fields=fields,
                Conditions=conditions,
                ExpiresIn=expires_in_seconds,
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise S3StorageError("Unable to create an S3 presigned upload.") from exc

        url = result.get("url")
        returned_fields = result.get("fields")
        if not isinstance(url, str) or not isinstance(returned_fields, dict):
            raise S3StorageError("S3 returned an invalid presigned upload response.")
        valid_fields = all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in returned_fields.items()
        )
        if not valid_fields:
            raise S3StorageError("S3 returned invalid presigned upload fields.")
        return PresignedPost(url=url, fields=dict(returned_fields))

    async def head_object(self, *, bucket: str, key: str) -> StoredS3Object:
        try:
            response = await asyncify(self.client.head_object)(Bucket=bucket, Key=key)
        except ClientError as exc:
            if _client_error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                raise S3ObjectNotFoundError from exc
            raise S3StorageError("Unable to inspect the uploaded S3 object.") from exc
        except (BotoCoreError, OSError) as exc:
            raise S3StorageError("Unable to inspect the uploaded S3 object.") from exc

        metadata = response.get("Metadata") or {}
        return StoredS3Object(
            content_length=int(response.get("ContentLength", -1)),
            content_type=response.get("ContentType"),
            cache_control=response.get("CacheControl"),
            server_side_encryption=response.get("ServerSideEncryption"),
            metadata={str(key).lower(): str(value) for key, value in metadata.items()},
            etag=str(response["ETag"]).strip('"') if response.get("ETag") else None,
            version_id=str(response["VersionId"]) if response.get("VersionId") else None,
            last_modified=response.get("LastModified"),
        )

    def _read_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        max_bytes: int,
    ) -> bytes:
        response = self.client.get_object(
            Bucket=bucket,
            Key=key,
            VersionId=version_id,
        )
        body = response["Body"]
        try:
            content = body.read(max_bytes + 1)
        finally:
            body.close()
        if len(content) > max_bytes:
            raise S3StorageError("The S3 object exceeded its verified maximum size.")
        return bytes(content)

    async def read_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        max_bytes: int,
    ) -> bytes:
        try:
            return await asyncify(self._read_object)(
                bucket=bucket,
                key=key,
                version_id=version_id,
                max_bytes=max_bytes,
            )
        except S3StorageError:
            raise
        except ClientError as exc:
            if _client_error_code(exc) in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                raise S3ObjectNotFoundError from exc
            raise S3StorageError("Unable to read the uploaded S3 object.") from exc
        except (BotoCoreError, OSError) as exc:
            raise S3StorageError("Unable to read the uploaded S3 object.") from exc

    def _write_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
        content_encoding: str | None,
        metadata: dict[str, str],
    ) -> WrittenS3Object:
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
        request: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "Body": content,
            "ContentType": content_type,
            "CacheControl": S3_CACHE_CONTROL,
            "ServerSideEncryption": S3_ENCRYPTION_ALGORITHM,
            "Metadata": metadata,
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": checksum,
        }
        if content_encoding is not None:
            request["ContentEncoding"] = content_encoding
        response = self.client.put_object(**request)
        etag = str(response["ETag"]).strip('"') if response.get("ETag") else ""
        version_id = str(response["VersionId"]) if response.get("VersionId") else ""
        if not etag or not version_id or version_id == "null":
            raise S3BucketSecurityError(
                "Stored processing artifacts require immutable S3 version identifiers."
            )
        return WrittenS3Object(
            content_length=len(content),
            etag=etag,
            version_id=version_id,
        )

    async def write_object(
        self,
        *,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
        content_encoding: str | None,
        metadata: dict[str, str],
    ) -> WrittenS3Object:
        try:
            return await asyncify(self._write_object)(
                bucket=bucket,
                key=key,
                content=content,
                content_type=content_type,
                content_encoding=content_encoding,
                metadata=metadata,
            )
        except S3BucketSecurityError:
            raise
        except (BotoCoreError, ClientError, OSError) as exc:
            raise S3StorageError("Unable to store a private processing artifact.") from exc

    async def delete_object(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
    ) -> None:
        try:
            await asyncify(self.client.delete_object)(
                Bucket=bucket,
                Key=key,
                VersionId=version_id,
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise S3StorageError("Unable to remove a rejected S3 object.") from exc


@lru_cache
def get_s3_upload_gateway() -> S3UploadGateway:
    return Boto3S3UploadGateway(get_settings())
