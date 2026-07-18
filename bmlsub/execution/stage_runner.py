"""Unified stage lifecycle, reuse checks, persistence, and error normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Mapping, Sequence

from ..execution.errors import BmlsubError, ErrorCode, ReviewRequiredError
from ..state.fingerprints import artifacts_are_current
from ..state.models import (
    ArtifactRecord,
    Diagnostic,
    DiagnosticLevel,
    RunStatus,
    StageInputBinding,
    StageResult,
    StageStatus,
    utc_now,
)
from ..state.sqlite_store import SQLiteJobStore


@dataclass(frozen=True)
class StageContext:
    run_id: str
    stage_id: str
    stage_name: str
    workspace: Path
    episode_id: str | None
    input_fingerprint: str
    parameter_fingerprint: str
    tool_fingerprint: str


@dataclass(frozen=True)
class StageOutcome:
    status: StageStatus = StageStatus.SUCCEEDED
    artifacts: tuple[ArtifactRecord, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {
            StageStatus.SUCCEEDED, StageStatus.SKIPPED, StageStatus.NEEDS_REVIEW
        }:
            raise ValueError("stage adapter outcome must be succeeded, skipped, or needs_review")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


StageAdapter = Callable[[StageContext], StageOutcome]
ArtifactValidator = Callable[[ArtifactRecord], bool]


class StageRunner:
    def __init__(self, store: SQLiteJobStore, *,
                 artifact_validator: ArtifactValidator | None = None) -> None:
        self.store = store
        self.artifact_validator = artifact_validator or self._default_artifact_validator

    def run(self, *, workspace: Path | str, command_name: str, stage_name: str,
            input_fingerprint: str, parameter_fingerprint: str, tool_fingerprint: str,
            adapter: StageAdapter, episode_id: str | None = None,
            run_metadata: Mapping[str, object] | None = None,
            inputs: Sequence[StageInputBinding] = (),
            force: bool = False) -> StageResult:
        workspace_path = Path(workspace).expanduser().resolve()
        run = self.store.create_run(
            workspace_path, command_name, episode_id=episode_id, metadata=run_metadata
        )
        stage = self.store.create_stage(
            run.run_id,
            stage_name,
            input_fingerprint=input_fingerprint,
            parameter_fingerprint=parameter_fingerprint,
            tool_fingerprint=tool_fingerprint,
        )
        input_bindings = tuple(inputs)
        started_at = utc_now()
        started_clock = monotonic()

        try:
            for binding in input_bindings:
                artifact = self.store.get_artifact(binding.artifact_id)
                if artifact is None:
                    raise BmlsubError(
                        "registered input artifact was not found",
                        code=ErrorCode.INPUT_MISSING,
                        details={"artifact_id": binding.artifact_id},
                    )
                if not self.artifact_validator(artifact):
                    self.store.mark_artifact_stale(artifact.artifact_id)
                    raise BmlsubError(
                        "registered input artifact is missing, changed, or invalid",
                        code=ErrorCode.INPUT_MISSING,
                        details={"artifact_id": artifact.artifact_id},
                    )
            if input_bindings:
                self.store.register_stage_inputs(stage.stage_id, input_bindings)
        except Exception as exc:
            error = self._normalize_error(exc)
            diagnostic = self._diagnostic_from_error(error)
            stored = self.store.fail_stage(
                stage.stage_id, error.code.value, retryable=error.retryable,
                diagnostics=(diagnostic,),
            )
            self.store.finish_run(run.run_id, RunStatus.FAILED, error_code=error.code.value)
            return self._result(
                run.run_id, stage_name, stored.status, (), (diagnostic,),
                started_at, started_clock, error=error.to_dict(), retryable=error.retryable,
            )

        if not force:
            reusable = self.store.find_reusable_stage(
                episode_id, stage_name, input_fingerprint,
                parameter_fingerprint, tool_fingerprint,
                exclude_run_id=run.run_id,
            )
            if reusable is not None:
                artifacts = tuple(self.store.get_stage_artifacts(reusable.stage_id))
                if artifacts and all(self.artifact_validator(item) for item in artifacts):
                    diagnostics = (Diagnostic(
                        code="reused_stage",
                        message="reused a previously validated stage result",
                        context={"source_stage_id": reusable.stage_id},
                    ),)
                    stored = self.store.skip_stage(stage.stage_id, diagnostics=diagnostics)
                    self.store.finish_run(run.run_id, RunStatus.SUCCEEDED)
                    return self._result(
                        run.run_id, stage_name, stored.status, artifacts, diagnostics,
                        started_at, started_clock, reused=True,
                    )
                self.store.mark_stage_stale(
                    reusable.stage_id,
                    diagnostics=(Diagnostic(
                        code="artifact_stale",
                        message="registered artifact is missing, changed, or invalid",
                        level=DiagnosticLevel.WARNING,
                    ),),
                )

        self.store.mark_stage_running(stage.stage_id)
        context = StageContext(
            run_id=run.run_id,
            stage_id=stage.stage_id,
            stage_name=stage_name,
            workspace=workspace_path,
            episode_id=episode_id,
            input_fingerprint=input_fingerprint,
            parameter_fingerprint=parameter_fingerprint,
            tool_fingerprint=tool_fingerprint,
        )
        try:
            outcome = adapter(context)
            for artifact in outcome.artifacts:
                if artifact.run_id != run.run_id or artifact.stage_id != stage.stage_id:
                    raise BmlsubError(
                        "stage adapter returned an artifact for another run or stage",
                        code=ErrorCode.ARTIFACT_COMMIT_FAILED,
                    )
                self.store.register_artifact(artifact)

            if outcome.status is StageStatus.SUCCEEDED:
                stored = self.store.complete_stage(
                    stage.stage_id, diagnostics=outcome.diagnostics
                )
                self.store.finish_run(run.run_id, RunStatus.SUCCEEDED)
            elif outcome.status is StageStatus.SKIPPED:
                stored = self.store.skip_stage(
                    stage.stage_id, diagnostics=outcome.diagnostics
                )
                self.store.finish_run(run.run_id, RunStatus.SUCCEEDED)
            else:
                stored = self.store.require_review(
                    stage.stage_id, diagnostics=outcome.diagnostics
                )
                self.store.finish_run(
                    run.run_id, RunStatus.NEEDS_REVIEW,
                    error_code=ErrorCode.REVIEW_REQUIRED.value,
                )
            return self._result(
                run.run_id, stage_name, stored.status, outcome.artifacts,
                outcome.diagnostics, started_at, started_clock,
                needs_review=stored.needs_review,
            )
        except ReviewRequiredError as exc:
            diagnostic = self._diagnostic_from_error(exc)
            stored = self.store.require_review(
                stage.stage_id, error_code=exc.code.value, diagnostics=(diagnostic,)
            )
            self.store.finish_run(
                run.run_id, RunStatus.NEEDS_REVIEW, error_code=exc.code.value
            )
            return self._result(
                run.run_id, stage_name, stored.status, (), (diagnostic,),
                started_at, started_clock, needs_review=True,
            )
        except Exception as exc:
            error = self._normalize_error(exc)
            diagnostic = self._diagnostic_from_error(error)
            stored = self.store.fail_stage(
                stage.stage_id, error.code.value, retryable=error.retryable,
                diagnostics=(diagnostic,),
            )
            self.store.finish_run(
                run.run_id, RunStatus.FAILED, error_code=error.code.value
            )
            return self._result(
                run.run_id, stage_name, stored.status, (), (diagnostic,),
                started_at, started_clock, error=error.to_dict(),
                retryable=error.retryable,
            )

    @staticmethod
    def _default_artifact_validator(artifact: ArtifactRecord) -> bool:
        return artifacts_are_current((artifact,), verify_hash=artifact.content_hash is not None)

    @staticmethod
    def _normalize_error(exc: Exception) -> BmlsubError:
        if isinstance(exc, BmlsubError):
            return exc
        if isinstance(exc, FileNotFoundError):
            return BmlsubError(
                str(exc), code=ErrorCode.INPUT_MISSING,
                details={"exception_type": type(exc).__name__},
            )
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return BmlsubError(
                str(exc), code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=True,
                details={"exception_type": type(exc).__name__},
            )
        return BmlsubError(
            "unexpected stage error",
            code=ErrorCode.UNEXPECTED,
            details={"exception_type": type(exc).__name__},
        )

    @staticmethod
    def _diagnostic_from_error(error: BmlsubError) -> Diagnostic:
        safe_context = {
            "error_code": error.code.value,
            "retryable": error.retryable,
            "exception_type": error.details.get("exception_type"),
        }
        return Diagnostic(
            code=error.code.value,
            message=str(error),
            level=DiagnosticLevel.ERROR,
            context={key: value for key, value in safe_context.items() if value is not None},
        )

    @staticmethod
    def _result(run_id: str, stage_name: str, status: StageStatus,
                artifacts: Sequence[ArtifactRecord], diagnostics: Sequence[Diagnostic],
                started_at, started_clock: float, *, error=None, retryable: bool = False,
                needs_review: bool = False, reused: bool = False) -> StageResult:
        finished_at = utc_now()
        return StageResult(
            run_id=run_id,
            stage_name=stage_name,
            status=status,
            artifacts=tuple(artifacts),
            diagnostics=tuple(diagnostics),
            error=error,
            retryable=retryable,
            needs_review=needs_review,
            reused=reused,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, round((monotonic() - started_clock) * 1000)),
        )
