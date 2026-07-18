"""Read-only source video registration stage."""

from __future__ import annotations

from ..version import __version__

from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from ..execution.errors import BmlsubError, ErrorCode
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import artifact_matches, fingerprint_file, fingerprint_parameters, fingerprint_tools
from ..state.models import (
    ArtifactPurpose,
    ArtifactRecord,
    Diagnostic,
    DiagnosticLevel,
    StageResult,
    StageStatus,
    ValidationStatus,
    utc_now,
)
from ..state.sqlite_store import SQLiteJobStore
from .models import VideoPurpose
from .probe import FFprobeClient


VIDEO_REGISTRATION_STAGE = "asset.register_video"
VIDEO_PROBE_SCHEMA_VERSION = "video-probe-v2"


@dataclass(frozen=True)
class VideoRegistrationOptions:
    purposes: tuple[VideoPurpose, ...]
    default_for: tuple[VideoPurpose, ...] = ()
    reference: bool = False
    origin: str = "explicit_user_input"

    def __post_init__(self) -> None:
        purposes = tuple(dict.fromkeys(self.purposes))
        defaults = tuple(dict.fromkeys(self.default_for))
        if not purposes:
            raise ValueError("at least one video purpose is required")
        if any(item not in purposes for item in defaults):
            raise ValueError("default purposes must also be included in purposes")
        if not self.origin.strip():
            raise ValueError("video registration origin must not be empty")
        object.__setattr__(self, "purposes", purposes)
        object.__setattr__(self, "default_for", defaults)


def run_video_registration(
    video_path: Path | str,
    *,
    workspace: Path | str,
    episode_id: str,
    options: VideoRegistrationOptions,
    probe: FFprobeClient | None = None,
    store: SQLiteJobStore | None = None,
    state_dir: Path | str | None = None,
    force: bool = False,
) -> StageResult:
    source = Path(video_path).expanduser().resolve()
    root = Path(workspace).expanduser().resolve()
    if not episode_id.strip():
        raise ValueError("episode_id must not be empty")
    if not source.exists():
        raise FileNotFoundError(f"video does not exist: {source}")
    if not source.is_file():
        raise BmlsubError("video input is not a file", code=ErrorCode.INPUT_MISSING)
    if source.stat().st_size <= 0:
        raise BmlsubError("video input is empty", code=ErrorCode.INPUT_MISSING)
    if not os.access(source, os.R_OK):
        raise BmlsubError("video input is not readable", code=ErrorCode.INPUT_MISSING)

    client = probe or FFprobeClient()
    file_fingerprint = fingerprint_file(source)
    artifact_type = "reference.video" if options.reference else "source.video"
    purpose_values = sorted(item.value for item in options.purposes)
    default_values = sorted(item.value for item in options.default_for)
    parameter_fingerprint = fingerprint_parameters({
        "episode_id": episode_id,
        "artifact_type": artifact_type,
        "purposes": purpose_values,
        "default_for": default_values,
        "origin": options.origin,
        "probe_schema": VIDEO_PROBE_SCHEMA_VERSION,
    })
    ffprobe_version = client.version()
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__,
        "ffprobe": ffprobe_version,
        "probe_schema": VIDEO_PROBE_SCHEMA_VERSION,
    })
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    runner = StageRunner(ledger)

    ambiguous_purposes = _ambiguous_purposes(
        ledger, episode_id, source, options.purposes, options.default_for
    )

    def adapter(context: StageContext) -> StageOutcome:
        summary = client.inspect(source)
        artifact = ArtifactRecord(
            artifact_id=uuid.uuid4().hex,
            run_id=context.run_id,
            stage_id=context.stage_id,
            episode_id=episode_id,
            artifact_type=artifact_type,
            path=source,
            size=file_fingerprint.size,
            mtime_ns=file_fingerprint.mtime_ns,
            source_fingerprint=file_fingerprint.digest,
            parameter_fingerprint=context.parameter_fingerprint,
            validation_status=ValidationStatus.VALID,
            created_at=utc_now(),
            metadata={
                "origin": options.origin,
                "probe_schema": VIDEO_PROBE_SCHEMA_VERSION,
                "ffprobe_version": ffprobe_version,
                "media": summary.to_dict(),
            },
            purposes=tuple(
                ArtifactPurpose(item.value, item in options.default_for)
                for item in options.purposes
            ),
        )
        if ambiguous_purposes:
            diagnostic = Diagnostic(
                code="video_purpose_ambiguous",
                message="multiple videos are registered for a purpose without a unique default",
                level=DiagnosticLevel.WARNING,
                context={"purposes": list(ambiguous_purposes)},
            )
            return StageOutcome(
                status=StageStatus.NEEDS_REVIEW,
                artifacts=(artifact,),
                diagnostics=(diagnostic,),
            )
        return StageOutcome(
            artifacts=(artifact,),
            diagnostics=(Diagnostic(
                code="video_registered",
                message="source video was inspected and registered",
                context={"artifact_type": artifact_type},
            ),),
        )

    return runner.run(
        workspace=root,
        command_name="asset.register-video",
        stage_name=VIDEO_REGISTRATION_STAGE,
        episode_id=episode_id,
        input_fingerprint=file_fingerprint.digest,
        parameter_fingerprint=parameter_fingerprint,
        tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        run_metadata={
            "input_type": artifact_type,
            "origin": options.origin,
            "purposes": purpose_values,
        },
        force=force,
    )


def get_current_artifact(store: SQLiteJobStore, artifact_id: str) -> ArtifactRecord | None:
    artifact = store.get_artifact(artifact_id)
    if artifact is None:
        return None
    if artifact.validation_status is ValidationStatus.VALID:
        if not artifact_matches(artifact, verify_hash=artifact.content_hash is not None):
            artifact = store.mark_artifact_stale(artifact.artifact_id)
    return artifact


def list_current_artifacts(store: SQLiteJobStore, *, episode_id: str | None = None,
                           artifact_type: str | None = None) -> list[ArtifactRecord]:
    artifacts = store.list_artifacts(
        episode_id=episode_id, artifact_type=artifact_type, current_only=True
    )
    current: list[ArtifactRecord] = []
    for artifact in artifacts:
        refreshed = get_current_artifact(store, artifact.artifact_id)
        if refreshed is not None and refreshed.validation_status is ValidationStatus.VALID:
            current.append(refreshed)
    return current


def resolve_video(store: SQLiteJobStore, episode_id: str,
                  purpose: VideoPurpose) -> tuple[ArtifactRecord | None, bool]:
    artifact, ambiguous = store.resolve_artifact_by_purpose(episode_id, purpose.value)
    if artifact is None:
        return None, ambiguous
    refreshed = get_current_artifact(store, artifact.artifact_id)
    if refreshed is None or refreshed.validation_status is not ValidationStatus.VALID:
        return None, False
    return refreshed, False


def _ambiguous_purposes(store: SQLiteJobStore, episode_id: str, source: Path,
                        purposes: tuple[VideoPurpose, ...],
                        defaults: tuple[VideoPurpose, ...]) -> tuple[str, ...]:
    ambiguous: list[str] = []
    default_set = set(defaults)
    for purpose in purposes:
        if purpose in default_set:
            continue
        candidates = [
            item for item in store.list_artifacts(episode_id=episode_id, current_only=True)
            if item.path != source and any(value.purpose == purpose.value for value in item.purposes)
        ]
        if not candidates:
            continue
        existing_defaults = [
            item for item in candidates
            if any(value.purpose == purpose.value and value.is_default for value in item.purposes)
        ]
        if len(existing_defaults) != 1:
            ambiguous.append(purpose.value)
    return tuple(sorted(ambiguous))
