"""Explicit production request models for encoded and packaged outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..state.models import (
    JsonValue,
    _assert_no_secret_keys,
    datetime_to_text,
    ensure_utc,
)


class ProductionOperation(str, Enum):
    ENCODE = "encode"
    REMUX = "remux"
    HARDSUB = "hardsub"
    MUX_SUBTITLE = "mux_subtitle"


class ProductionRequestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class ProductionRequestInput:
    artifact_id: str
    input_role: str
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.input_role.strip():
            raise ValueError("production request input fields must not be empty")
        if self.ordinal < 0:
            raise ValueError("production request input ordinal must be non-negative")
        object.__setattr__(self, "input_role", self.input_role.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "input_role": self.input_role,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class ProductionRequestRecord:
    request_id: str
    workspace_path: Path
    episode_id: str
    operation: ProductionOperation
    output_profile: str
    output_target: Path
    status: ProductionRequestStatus
    created_at: datetime
    updated_at: datetime
    inputs: tuple[ProductionRequestInput, ...]
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)
    run_id: str | None = None
    stage_id: str | None = None
    artifact_id: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id or not self.episode_id.strip():
            raise ValueError("production request identifiers must not be empty")
        if not self.output_profile.strip():
            raise ValueError("production request output_profile must not be empty")
        inputs = tuple(self.inputs)
        keys = {(item.input_role, item.ordinal) for item in inputs}
        if not inputs or len(keys) != len(inputs):
            raise ValueError("production request inputs must be nonempty and uniquely ordered")
        parameters = dict(self.parameters)
        _assert_no_secret_keys(parameters)
        object.__setattr__(self, "workspace_path", Path(self.workspace_path).expanduser().resolve())
        object.__setattr__(self, "output_target", Path(self.output_target).expanduser().resolve())
        object.__setattr__(self, "episode_id", self.episode_id.strip())
        object.__setattr__(self, "output_profile", self.output_profile.strip())
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "parameters", MappingProxyType(parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workspace_path": str(self.workspace_path),
            "episode_id": self.episode_id,
            "operation": self.operation.value,
            "output_profile": self.output_profile,
            "output_target": str(self.output_target),
            "parameters": dict(self.parameters),
            "status": self.status.value,
            "created_at": datetime_to_text(self.created_at),
            "updated_at": datetime_to_text(self.updated_at),
            "inputs": [item.to_dict() for item in self.inputs],
            "run_id": self.run_id,
            "stage_id": self.stage_id,
            "artifact_id": self.artifact_id,
            "error_code": self.error_code,
        }
