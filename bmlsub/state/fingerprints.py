"""Stable fingerprints for inputs, parameters, tools, and artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import ArtifactRecord, ValidationStatus


@dataclass(frozen=True)
class FileFingerprint:
    path: Path
    size: int
    mtime_ns: int
    content_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        if self.size < 0 or self.mtime_ns < 0:
            raise ValueError("file fingerprint size and mtime_ns must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "content_hash": self.content_hash,
        }

    @property
    def digest(self) -> str:
        return hash_json(self.to_dict())


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_file(path: Path | str, *, content_hash: bool = False) -> FileFingerprint:
    source = Path(path).expanduser().resolve()
    stat = source.stat()
    return FileFingerprint(
        path=source,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        content_hash=sha256_file(source) if content_hash else None,
    )


def fingerprint_subtitle(path: Path | str) -> FileFingerprint:
    return fingerprint_file(path, content_hash=True)


def stable_json(value: Any) -> str:
    return json.dumps(
        _normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def hash_json(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def fingerprint_parameters(parameters: Mapping[str, Any]) -> str:
    return hash_json(parameters)


def fingerprint_tools(tools: Mapping[str, Any]) -> str:
    return hash_json(tools)


def combine_fingerprints(fingerprints: Sequence[FileFingerprint]) -> str:
    return hash_json([item.to_dict() for item in fingerprints])


def artifact_matches(record: ArtifactRecord, *, verify_hash: bool = False,
                     require_valid: bool = True) -> bool:
    if require_valid and record.validation_status is not ValidationStatus.VALID:
        return False
    try:
        current = fingerprint_file(
            record.path,
            content_hash=verify_hash or record.content_hash is not None,
        )
    except (FileNotFoundError, OSError):
        return False
    if current.size != record.size or current.mtime_ns != record.mtime_ns:
        return False
    if record.content_hash is not None and current.content_hash != record.content_hash:
        return False
    return True


def artifacts_are_current(records: Sequence[ArtifactRecord], *, verify_hash: bool = False) -> bool:
    return bool(records) and all(
        artifact_matches(record, verify_hash=verify_hash) for record in records
    )


def _normalize_json(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_json(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value.expanduser().resolve())
    if isinstance(value, Mapping):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize_json(item) for item in value), key=stable_json)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported value for stable JSON: {type(value).__name__}")
