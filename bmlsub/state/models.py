"""Reliable execution state models for bmlsub."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    INTERRUPTED = "interrupted"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"


class ValidationStatus(str, Enum):
    DISCOVERED = "discovered"
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"
    STALE = "stale"


class AssetMatchStatus(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


class DiagnosticLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_SECRET_MARKERS = (
    "password", "passwd", "secret", "token", "credential", "api_key", "apikey",
    "access_key", "private_key", "authorization", "cookie",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC timezone")
    return value.astimezone(timezone.utc)


def datetime_to_text(value: datetime | None) -> str | None:
    normalized = ensure_utc(value)
    return normalized.isoformat().replace("+00:00", "Z") if normalized else None


def datetime_from_text(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(parsed)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _assert_no_secret_keys(value: Any, prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in _SECRET_MARKERS):
                location = f"{prefix}.{key}" if prefix else str(key)
                raise ValueError(f"diagnostic context must not contain secret field: {location}")
            _assert_no_secret_keys(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secret_keys(item, f"{prefix}[{index}]")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    level: DiagnosticLevel = DiagnosticLevel.INFO
    context: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("diagnostic code must not be empty")
        if not self.message.strip():
            raise ValueError("diagnostic message must not be empty")
        _assert_no_secret_keys(self.context)
        object.__setattr__(self, "context", _freeze_mapping(self.context))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "level": self.level.value,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Diagnostic":
        return cls(
            code=str(data["code"]),
            message=str(data["message"]),
            level=DiagnosticLevel(data.get("level", DiagnosticLevel.INFO.value)),
            context=data.get("context", {}),
        )


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    workspace_path: Path
    command_name: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    episode_id: str | None = None
    error_code: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not self.command_name:
            raise ValueError("run_id and command_name must not be empty")
        object.__setattr__(self, "workspace_path", Path(self.workspace_path).expanduser().resolve())
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_path": str(self.workspace_path),
            "episode_id": self.episode_id,
            "command_name": self.command_name,
            "status": self.status.value,
            "created_at": datetime_to_text(self.created_at),
            "started_at": datetime_to_text(self.started_at),
            "finished_at": datetime_to_text(self.finished_at),
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StageRecord:
    stage_id: str
    run_id: str
    stage_name: str
    status: StageStatus
    created_at: datetime
    input_fingerprint: str | None = None
    parameter_fingerprint: str | None = None
    tool_fingerprint: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retryable: bool = False
    needs_review: bool = False
    error_code: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not self.stage_id or not self.run_id or not self.stage_name:
            raise ValueError("stage_id, run_id and stage_name must not be empty")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.status is StageStatus.NEEDS_REVIEW and not self.needs_review:
            raise ValueError("needs_review stage status requires needs_review=True")
        if self.needs_review and self.status is not StageStatus.NEEDS_REVIEW:
            raise ValueError("needs_review=True is only valid for needs_review status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "run_id": self.run_id,
            "stage_name": self.stage_name,
            "status": self.status.value,
            "created_at": datetime_to_text(self.created_at),
            "input_fingerprint": self.input_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "tool_fingerprint": self.tool_fingerprint,
            "started_at": datetime_to_text(self.started_at),
            "finished_at": datetime_to_text(self.finished_at),
            "retryable": self.retryable,
            "needs_review": self.needs_review,
            "error_code": self.error_code,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True)
class ArtifactPurpose:
    purpose: str
    is_default: bool = False

    def __post_init__(self) -> None:
        normalized = self.purpose.strip()
        if not normalized:
            raise ValueError("artifact purpose must not be empty")
        object.__setattr__(self, "purpose", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {"purpose": self.purpose, "is_default": self.is_default}


@dataclass(frozen=True)
class StageInputBinding:
    artifact_id: str
    input_role: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.input_role.strip():
            raise ValueError("stage input artifact_id and input_role must not be empty")
        if self.ordinal < 0:
            raise ValueError("stage input ordinal must be non-negative")
        object.__setattr__(self, "input_role", self.input_role.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "input_role": self.input_role,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class StageInputRecord:
    stage_id: str
    artifact_id: str
    input_role: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.stage_id:
            raise ValueError("stage input stage_id must not be empty")
        StageInputBinding(self.artifact_id, self.input_role, self.ordinal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "artifact_id": self.artifact_id,
            "input_role": self.input_role,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class AssetMatchCandidateRecord:
    match_set_id: str
    artifact_id: str
    rank: int
    score: int
    evidence: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.match_set_id or not self.artifact_id:
            raise ValueError("match candidate identifiers must not be empty")
        if self.rank < 0:
            raise ValueError("match candidate rank must be non-negative")
        _assert_no_secret_keys(self.evidence)
        object.__setattr__(self, "evidence", _freeze_mapping(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_set_id": self.match_set_id,
            "artifact_id": self.artifact_id,
            "rank": self.rank,
            "score": self.score,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AssetMatchSelectionRecord:
    match_set_id: str
    artifact_id: str
    input_role: str
    ordinal: int
    stage_id: str

    def __post_init__(self) -> None:
        if not self.match_set_id or not self.artifact_id or not self.stage_id:
            raise ValueError("match selection identifiers must not be empty")
        if not self.input_role.strip() or self.ordinal < 0:
            raise ValueError("match selection role and ordinal are invalid")
        object.__setattr__(self, "input_role", self.input_role.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_set_id": self.match_set_id,
            "artifact_id": self.artifact_id,
            "input_role": self.input_role,
            "ordinal": self.ordinal,
            "stage_id": self.stage_id,
        }


@dataclass(frozen=True)
class AssetMatchSetRecord:
    match_set_id: str
    stage_id: str
    episode_id: str
    anchor_artifact_id: str
    input_role: str
    status: AssetMatchStatus
    rule_version: str
    created_at: datetime
    superseded_by_match_set_id: str | None = None
    candidates: tuple[AssetMatchCandidateRecord, ...] = ()
    selections: tuple[AssetMatchSelectionRecord, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.match_set_id, self.stage_id, self.episode_id,
                    self.anchor_artifact_id, self.input_role.strip(), self.rule_version)):
            raise ValueError("match set fields must not be empty")
        object.__setattr__(self, "input_role", self.input_role.strip())
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "selections", tuple(self.selections))

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_set_id": self.match_set_id,
            "stage_id": self.stage_id,
            "episode_id": self.episode_id,
            "anchor_artifact_id": self.anchor_artifact_id,
            "input_role": self.input_role,
            "status": self.status.value,
            "rule_version": self.rule_version,
            "created_at": datetime_to_text(self.created_at),
            "superseded_by_match_set_id": self.superseded_by_match_set_id,
            "candidates": [item.to_dict() for item in self.candidates],
            "selections": [item.to_dict() for item in self.selections],
        }


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    stage_id: str
    episode_id: str | None
    artifact_type: str
    path: Path
    size: int
    mtime_ns: int
    validation_status: ValidationStatus
    created_at: datetime
    content_hash: str | None = None
    source_fingerprint: str | None = None
    parameter_fingerprint: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    purposes: tuple[ArtifactPurpose, ...] = ()
    superseded_by_artifact_id: str | None = None

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.run_id or not self.stage_id or not self.artifact_type:
            raise ValueError("artifact identifiers and artifact_type must not be empty")
        if self.size < 0 or self.mtime_ns < 0:
            raise ValueError("artifact size and mtime_ns must be non-negative")
        _assert_no_secret_keys(self.metadata)
        purposes = tuple(self.purposes)
        if len({item.purpose for item in purposes}) != len(purposes):
            raise ValueError("artifact purposes must be unique")
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        object.__setattr__(self, "purposes", purposes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "stage_id": self.stage_id,
            "episode_id": self.episode_id,
            "artifact_type": self.artifact_type,
            "path": str(self.path),
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "content_hash": self.content_hash,
            "source_fingerprint": self.source_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "validation_status": self.validation_status.value,
            "created_at": datetime_to_text(self.created_at),
            "metadata": dict(self.metadata),
            "purposes": [item.to_dict() for item in self.purposes],
            "superseded_by_artifact_id": self.superseded_by_artifact_id,
        }


@dataclass(frozen=True)
class StageResult:
    run_id: str
    stage_name: str
    status: StageStatus
    artifacts: tuple[ArtifactRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    error: Mapping[str, JsonValue] | None = None
    retryable: bool = False
    needs_review: bool = False
    reused: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "finished_at", ensure_utc(self.finished_at))
        if self.error is not None:
            object.__setattr__(self, "error", _freeze_mapping(self.error))
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if self.status is StageStatus.FAILED and self.error is None:
            raise ValueError("failed StageResult requires an error")
        if self.status is not StageStatus.FAILED and self.error is not None:
            raise ValueError("only failed StageResult may contain an error")
        if self.status is StageStatus.NEEDS_REVIEW and not self.needs_review:
            raise ValueError("needs_review status requires needs_review=True")
        if self.needs_review and self.status is not StageStatus.NEEDS_REVIEW:
            raise ValueError("needs_review=True is only valid for needs_review status")
        if self.reused and self.status is not StageStatus.SKIPPED:
            raise ValueError("reused results must use skipped status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage_name": self.stage_name,
            "status": self.status.value,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "error": dict(self.error) if self.error is not None else None,
            "retryable": self.retryable,
            "needs_review": self.needs_review,
            "reused": self.reused,
            "started_at": datetime_to_text(self.started_at),
            "finished_at": datetime_to_text(self.finished_at),
            "duration_ms": self.duration_ms,
        }
