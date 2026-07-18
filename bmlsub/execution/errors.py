"""Structured errors used by the reliability core."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class ErrorCode(str, Enum):
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    STATE_SCHEMA_UNSUPPORTED = "state_schema_unsupported"
    STATE_STORE_ERROR = "state_store_error"
    INPUT_MISSING = "input_missing"
    DEPENDENCY_MISSING = "dependency_missing"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"
    ARTIFACT_COMMIT_FAILED = "artifact_commit_failed"
    EXTERNAL_SERVICE_ERROR = "external_service_error"
    REVIEW_REQUIRED = "review_required"
    INTERRUPTED = "interrupted"
    UNEXPECTED = "unexpected"


class BmlsubError(Exception):
    """Base exception with stable machine-readable attributes."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.UNEXPECTED,
                 retryable: bool = False, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": str(self),
            "retryable": self.retryable,
            "details": dict(self.details),
        }


class StateTransitionError(BmlsubError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.INVALID_STATE_TRANSITION, details=details)


class SchemaVersionError(BmlsubError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.STATE_SCHEMA_UNSUPPORTED, details=details)


class StateStoreError(BmlsubError):
    def __init__(self, message: str, *, retryable: bool = False,
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.STATE_STORE_ERROR,
                         retryable=retryable, details=details)


class OutputValidationError(BmlsubError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.OUTPUT_VALIDATION_FAILED, details=details)


class ArtifactCommitError(BmlsubError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.ARTIFACT_COMMIT_FAILED, details=details)


class ReviewRequiredError(BmlsubError):
    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=ErrorCode.REVIEW_REQUIRED, details=details)
