"""Cloudflare R2 upload adapter and remote object validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.request import Request, urlopen

from ..execution.errors import BmlsubError, ErrorCode
from .credentials import R2Credentials
from .external_profiles import R2UploadProfile


R2_ADAPTER_VERSION = "r2-boto3-adapter-v1"
R2_VALIDATOR_VERSION = "r2-head-validator-v1"
R2_RECEIPT_SCHEMA = "r2-receipt-v1"


@dataclass(frozen=True)
class R2ObjectIdentity:
    bucket: str
    object_key: str
    size: int
    content_type: str
    sha256: str
    etag: str | None = None
    version_id: str | None = None
    public_url: str | None = None

    def bounded(self) -> dict[str, Any]:
        return {
            "schema_version": R2_RECEIPT_SCHEMA, "provider": "cloudflare-r2",
            "bucket": self.bucket, "object_key": self.object_key, "size": self.size,
            "content_type": self.content_type, "sha256": self.sha256,
            "etag": self.etag, "version_id": self.version_id,
            "public_url": self.public_url,
        }


class R2Client(Protocol):
    @property
    def version(self) -> str: ...
    def upload(self, source: Path, profile: R2UploadProfile, *, metadata: Mapping[str, str]) -> None: ...
    def head(self, profile: R2UploadProfile) -> Mapping[str, Any]: ...


class Boto3R2Client:
    def __init__(self, credentials: R2Credentials) -> None:
        try:
            import boto3
            import botocore
            from botocore.config import Config
        except ImportError as exc:
            raise BmlsubError(
                "R2 upload requires the release optional dependency",
                code=ErrorCode.DEPENDENCY_MISSING,
            ) from exc
        self._boto3_version = getattr(boto3, "__version__", "unknown")
        self._botocore_version = getattr(botocore, "__version__", "unknown")
        self.client = boto3.client(
            "s3", endpoint_url=credentials.endpoint_url,
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            config=Config(retries={"max_attempts": 4, "mode": "adaptive"}),
        )

    @property
    def version(self) -> str:
        return f"boto3/{self._boto3_version} botocore/{self._botocore_version}"

    def upload(self, source: Path, profile: R2UploadProfile, *, metadata: Mapping[str, str]) -> None:
        from boto3.s3.transfer import TransferConfig
        config = TransferConfig(
            multipart_threshold=profile.multipart_threshold,
            multipart_chunksize=profile.multipart_chunk_size,
            max_concurrency=profile.max_concurrency,
        )
        try:
            self.client.upload_file(
                str(source), profile.bucket, profile.object_key,
                ExtraArgs={"ContentType": profile.content_type, "Metadata": dict(metadata)},
                Config=config,
            )
        except Exception as exc:
            raise _provider_error("R2 upload failed", exc) from exc

    def head(self, profile: R2UploadProfile) -> Mapping[str, Any]:
        try:
            return self.client.head_object(Bucket=profile.bucket, Key=profile.object_key)
        except Exception as exc:
            raise _provider_error("R2 object validation failed", exc) from exc


def validate_remote_object(client: R2Client, profile: R2UploadProfile, *,
                           expected_size: int, expected_sha256: str) -> R2ObjectIdentity:
    response = client.head(profile)
    metadata = {str(key).lower(): str(value) for key, value in dict(response.get("Metadata") or {}).items()}
    size = int(response.get("ContentLength", -1))
    content_type = str(response.get("ContentType") or "")
    sha256 = metadata.get("bml-sha256", "")
    if size != expected_size:
        raise BmlsubError("R2 object size does not match the source Artifact", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    if content_type.split(";", 1)[0].strip().lower() != profile.content_type.lower():
        raise BmlsubError("R2 object content type does not match the Profile", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    if sha256 != expected_sha256:
        raise BmlsubError("R2 object SHA-256 metadata does not match the source Artifact", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    public_url = None
    if profile.access == "public":
        public_url = f"{profile.public_base_url.rstrip('/')}/{profile.object_key}"
        request = Request(public_url, method="HEAD")
        try:
            with urlopen(request, timeout=30) as result:
                public_size = int(result.headers.get("Content-Length", -1))
        except Exception as exc:
            raise BmlsubError(
                "public R2 object validation failed", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=True, details={"exception_type": type(exc).__name__},
            ) from exc
        if public_size != expected_size:
            raise BmlsubError("public R2 object size does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    return R2ObjectIdentity(
        bucket=profile.bucket, object_key=profile.object_key, size=size,
        content_type=content_type, sha256=sha256,
        etag=_clean(response.get("ETag")), version_id=_clean(response.get("VersionId")),
        public_url=public_url,
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"')
    return text[:256] if text else None


def _provider_error(message: str, exc: Exception) -> BmlsubError:
    response = getattr(exc, "response", None)
    status = None
    code = None
    if isinstance(response, Mapping):
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = response.get("Error", {}).get("Code")
    retryable = status in {408, 429, 500, 502, 503, 504} or code in {
        "SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable",
    } or isinstance(exc, (TimeoutError, ConnectionError))
    return BmlsubError(
        message, code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=retryable,
        details={"provider": "cloudflare-r2", "status": status, "provider_code": code,
                 "exception_type": type(exc).__name__},
    )
