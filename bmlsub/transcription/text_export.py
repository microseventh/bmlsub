"""Export readable transcript text without changing transcript JSON artifacts."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

from ..artifacts.writer import ArtifactWriter
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools
from ..state.models import Diagnostic, StageInputBinding, ValidationStatus
from ..state.sqlite_store import SQLiteJobStore

EXPORT_VERSION = "transcript-text-export-v1"


def transcript_text_path(workspace: Path | str, episode_id: str, mode: str, model: str) -> Path:
    root = Path(workspace).expanduser().resolve()
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in model.rstrip("/").split("/")[-1])
    return root / f"{episode_id}.{mode}.{safe}.txt"


def _lines_from_payload(payload: dict[str, Any]) -> list[str]:
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError("transcript segments must be a list")
    lines = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise ValueError("transcript segment text is invalid")
        text = segment["text"].strip()
        if text:
            lines.append(text)
    return lines


def run_transcript_text_export(*, workspace: Path | str, episode_id: str,
                               transcript_artifact_id: str, mode: str, model: str,
                               store: SQLiteJobStore | None = None,
                               state_dir: Path | str | None = None,
                               force: bool = False) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    source = ledger.get_artifact(transcript_artifact_id)
    if source is None or source.validation_status is not ValidationStatus.VALID:
        raise ValueError("transcript artifact is not current")
    payload = json.loads(source.path.read_text(encoding="utf-8"))
    lines = _lines_from_payload(payload)
    target = transcript_text_path(root, episode_id, mode, model)
    input_fp = source.content_hash or ""
    parameter_fp = fingerprint_parameters({"target": str(target.relative_to(root)), "mode": mode, "model": model, "export": EXPORT_VERSION})
    tool_fp = fingerprint_tools({"export": EXPORT_VERSION})

    def adapter(context: StageContext) -> StageOutcome:
        written = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, artifact_type=f"generated.transcript.text.{mode}",
            source_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
            metadata={"source_transcript_artifact_id": source.artifact_id, "mode": mode,
                      "model": model, "export_version": EXPORT_VERSION, "line_count": len(lines)},
        ).write(
            lambda path: path.write_text("\n".join(lines) + "\n", encoding="utf-8"),
            lambda path: _validate_text(path, lines),
        )
        return StageOutcome(artifacts=(written.artifact,), diagnostics=(Diagnostic(
            code="transcript_text_exported", message="transcript text was exported one line per segment",
            context={"mode": mode, "line_count": len(lines)},
        ),))

    result = StageRunner(ledger).run(
        workspace=root, command_name="transcribe.export-text", stage_name=f"transcription.export_text.{mode}",
        episode_id=episode_id, input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(source.artifact_id, "transcript", 0),), force=force,
    )
    return {"status": result.status.value, "run_id": result.run_id, "reused": result.reused,
            "artifacts": [replace(item).to_dict() for item in result.artifacts],
            "diagnostics": [item.to_dict() for item in result.diagnostics],
            "artifact_id": result.artifacts[0].artifact_id if result.artifacts else None}


def _validate_text(path: Path, expected: list[str]) -> None:
    if path.read_text(encoding="utf-8").splitlines() != expected:
        raise ValueError("transcript text does not match transcript JSON segments")
