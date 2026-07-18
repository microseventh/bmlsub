"""Read-only registration of external source assets."""

from __future__ import annotations

from ..version import __version__

from pathlib import Path
import os
import uuid

from ..execution.errors import BmlsubError, ErrorCode
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import fingerprint_file, fingerprint_parameters, fingerprint_tools
from ..state.models import ArtifactRecord, Diagnostic, StageResult, ValidationStatus, utc_now
from ..state.sqlite_store import SQLiteJobStore
from .inspectors import inspect_source
from .models import SourceAssetRegistrationOptions


SOURCE_INSPECTOR_VERSION = "source-asset-v1"


def run_source_asset_registration(
    path: Path | str,
    *,
    workspace: Path | str,
    episode_id: str,
    options: SourceAssetRegistrationOptions,
    store: SQLiteJobStore | None = None,
    state_dir: Path | str | None = None,
    force: bool = False,
) -> StageResult:
    source = Path(path).expanduser().resolve()
    root = Path(workspace).expanduser().resolve()
    if not episode_id.strip():
        raise ValueError("episode_id must not be empty")
    if not source.exists():
        raise FileNotFoundError(f"source asset does not exist: {source}")
    if not source.is_file() or source.stat().st_size <= 0 or not os.access(source, os.R_OK):
        raise BmlsubError("source asset is not a readable non-empty file", code=ErrorCode.INPUT_MISSING)

    file_fingerprint = fingerprint_file(source, content_hash=True)
    parameter_fingerprint = fingerprint_parameters({
        "episode_id": episode_id,
        "kind": options.kind.value,
        "language": options.language,
        "origin": options.origin,
        "inspector_version": SOURCE_INSPECTOR_VERSION,
    })
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__, "source_inspector": SOURCE_INSPECTOR_VERSION,
    })
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()

    def adapter(context: StageContext) -> StageOutcome:
        artifact_type, inspected = inspect_source(source, options.kind, options.language)
        artifact = ArtifactRecord(
            artifact_id=uuid.uuid4().hex,
            run_id=context.run_id,
            stage_id=context.stage_id,
            episode_id=episode_id,
            artifact_type=artifact_type,
            path=source,
            size=file_fingerprint.size,
            mtime_ns=file_fingerprint.mtime_ns,
            content_hash=file_fingerprint.content_hash,
            source_fingerprint=file_fingerprint.digest,
            parameter_fingerprint=context.parameter_fingerprint,
            validation_status=ValidationStatus.VALID,
            created_at=utc_now(),
            metadata={
                "origin": options.origin,
                "inspector_version": SOURCE_INSPECTOR_VERSION,
                **inspected,
            },
        )
        return StageOutcome(
            artifacts=(artifact,),
            diagnostics=(Diagnostic(
                code=f"{options.kind.value}_registered",
                message=f"source {options.kind.value} was validated and registered",
                context={"artifact_type": artifact_type},
            ),),
        )

    stage_name = f"asset.register_{options.kind.value}"
    return StageRunner(ledger).run(
        workspace=root,
        command_name=stage_name.replace("_", "-"),
        stage_name=stage_name,
        episode_id=episode_id,
        input_fingerprint=file_fingerprint.digest,
        parameter_fingerprint=parameter_fingerprint,
        tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        run_metadata={"input_type": options.kind.value, "origin": options.origin},
        force=force,
    )
