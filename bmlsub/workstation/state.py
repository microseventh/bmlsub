"""Readable workstation snapshots derived from the SQLite authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import os
import tempfile

from ..state.models import ArtifactRecord, StageResult
from ..state.sqlite_store import SQLiteJobStore
from .common import ensure_directories


STEP_SCHEMA_VERSION = "workstation-step-v1"
MANIFEST_SCHEMA_VERSION = "workstation-manifest-v1"
SUMMARY_SCHEMA_VERSION = "workstation-summary-v1"


def read_json(path: Path | str, default: Any = None) -> Any:
    target = Path(path)
    if not target.is_file():
        return default
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path | str, payload: Mapping[str, Any] | list[Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    return target


def artifact_payload(artifact: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type,
        "absolute_path": str(artifact.path),
        "size": artifact.size,
        "mtime_ns": artifact.mtime_ns,
        "content_hash": artifact.content_hash,
        "validation_status": artifact.validation_status.value,
        "run_id": artifact.run_id,
        "stage_id": artifact.stage_id,
        "episode_id": artifact.episode_id,
        "source_fingerprint": artifact.source_fingerprint,
        "parameter_fingerprint": artifact.parameter_fingerprint,
        "metadata": dict(artifact.metadata),
        "purposes": [item.to_dict() for item in artifact.purposes],
        "created_at": artifact.to_dict()["created_at"],
        "superseded_by_artifact_id": artifact.superseded_by_artifact_id,
    }


def export_artifact(workspace: Path | str, artifact: ArtifactRecord) -> Path:
    paths = ensure_directories(workspace)
    return atomic_write_json(paths["artifacts"] / f"{artifact.artifact_id}.json", artifact_payload(artifact))


def step_payload(*, workflow_id: str, phase: str, step: str, status: str,
                 result: StageResult | None = None,
                 inputs: tuple[ArtifactRecord, ...] = (),
                 outputs: tuple[ArtifactRecord, ...] = (),
                 diagnostics: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
                 error: Mapping[str, Any] | None = None,
                 next_action: str | None = None,
                 sqlite_stage_id: str | None = None) -> dict[str, Any]:
    stage_name = result.stage_name if result else None
    stage_id = sqlite_stage_id
    payload = {
        "schema_version": STEP_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "phase": phase,
        "step": step,
        "status": status,
        "started_at": result.to_dict()["started_at"] if result else None,
        "finished_at": result.to_dict()["finished_at"] if result else None,
        "duration_ms": result.duration_ms if result else None,
        "sqlite": {
            "run_id": result.run_id if result else None,
            "stage_id": stage_id,
            "stage_name": stage_name,
        },
        "inputs": [artifact_payload(item) for item in inputs],
        "outputs": [artifact_payload(item) for item in (outputs or (result.artifacts if result else ()))],
        "diagnostics": list(diagnostics) if diagnostics else (
            [item.to_dict() for item in result.diagnostics] if result else []
        ),
        "error": dict(error) if error else (dict(result.error) if result and result.error else None),
        "retryable": result.retryable if result else False,
        "needs_review": status == "needs_review" or (result.needs_review if result else False),
        "reused": result.reused if result else False,
        "next_action": next_action,
    }
    return payload


def write_step(workspace: Path | str, payload: Mapping[str, Any]) -> Path:
    paths = ensure_directories(workspace)
    return atomic_write_json(paths["steps"] / f"{payload['step']}.json", payload)


def result_step(workspace: Path | str, *, workflow_id: str, phase: str, step: str,
                result: StageResult, inputs: tuple[ArtifactRecord, ...] = (),
                status: str | None = None, next_action: str | None = None) -> dict[str, Any]:
    store = SQLiteJobStore.for_workspace(workspace, Path(workspace) / "workstation" / "state")
    stages = store.get_run_stages(result.run_id)
    stage_id = stages[0].stage_id if stages else None
    payload = step_payload(
        workflow_id=workflow_id, phase=phase, step=step,
        status=status or result.status.value, result=result, inputs=inputs,
        sqlite_stage_id=stage_id, next_action=next_action,
    )
    write_step(workspace, payload)
    for artifact in result.artifacts:
        export_artifact(workspace, artifact)
    return payload


def pipeline_payload_step(workspace: Path | str, *, workflow_id: str, phase: str,
                          step: str, payload: Mapping[str, Any],
                          inputs: tuple[ArtifactRecord, ...] = (),
                          status: str | None = None,
                          next_action: str | None = None) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    store = SQLiteJobStore.for_workspace(root, root / "workstation" / "state")
    run_id = payload.get("run_id")
    stage_name = payload.get("stage_name") or payload.get("stage")
    stage_id = None
    outputs: tuple[ArtifactRecord, ...] = ()
    if isinstance(run_id, str):
        stages = store.get_run_stages(run_id)
        stage_id = stages[0].stage_id if stages else None
        outputs = tuple(store.get_stage_artifacts(stage_id)) if stage_id else ()
        if stage_name is None and stages:
            stage_name = stages[0].stage_name
    raw_artifacts = payload.get("artifacts")
    if not outputs and isinstance(raw_artifacts, list):
        resolved = []
        for item in raw_artifacts:
            if isinstance(item, Mapping) and isinstance(item.get("artifact_id"), str):
                artifact = store.get_artifact(item["artifact_id"])
                if artifact is not None:
                    resolved.append(artifact)
        outputs = tuple(resolved)
    selected_status = status or str(payload.get("status") or "failed")
    snapshot = {
        "schema_version": STEP_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "phase": phase,
        "step": step,
        "status": selected_status,
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "duration_ms": payload.get("duration_ms"),
        "sqlite": {"run_id": run_id, "stage_id": stage_id, "stage_name": stage_name},
        "inputs": [artifact_payload(item) for item in inputs],
        "outputs": [artifact_payload(item) for item in outputs],
        "diagnostics": list(payload.get("diagnostics") or []),
        "error": payload.get("error"),
        "retryable": bool(payload.get("retryable", False)),
        "needs_review": selected_status == "needs_review" or bool(payload.get("needs_review", False)),
        "reused": bool(payload.get("reused", False)),
        "next_action": next_action,
    }
    write_step(root, snapshot)
    for artifact in outputs:
        export_artifact(root, artifact)
    return snapshot


def manifest_path(workspace: Path | str) -> Path:
    return Path(workspace).expanduser().resolve() / "workstation" / "state" / "manifest.json"


def load_manifest(workspace: Path | str) -> dict[str, Any]:
    return read_json(manifest_path(workspace), {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": {}, "preprocess": {}, "subtitles": {}, "fonts": {"artifact_ids": []},
        "products": {}, "torrents": {}, "publish": {},
    })


def update_manifest(workspace: Path | str, **sections: Mapping[str, Any]) -> dict[str, Any]:
    payload = load_manifest(workspace)
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION
    for name, values in sections.items():
        current = payload.setdefault(name, {})
        current.update(values)
    atomic_write_json(manifest_path(workspace), payload)
    return payload


def refresh_summary(workspace: Path | str) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    step_dir = root / "workstation" / "state" / "steps"
    steps = {}
    for path in sorted(step_dir.glob("*.json")) if step_dir.is_dir() else ():
        payload = read_json(path, {})
        steps[payload.get("step", path.stem)] = payload.get("status", "pending")
    phase_steps = {
        phase: {name: value for name, value in steps.items() if name.startswith(f"{phase}.")}
        for phase in ("preprocess", "translation", "delivery", "publish")
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "preprocess": _phase_status(phase_steps["preprocess"]),
        "translation": {
            "status": _phase_status(phase_steps["translation"])["status"],
            "completed_by_user": True,
            "steps": phase_steps["translation"],
        },
        "delivery": _phase_status(phase_steps["delivery"]),
        "publish": _phase_status(phase_steps["publish"]),
    }
    atomic_write_json(root / "workstation" / "state" / "summary.json", summary)
    return summary


def load_status(workspace: Path | str, step: str | None = None) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    if step:
        payload = read_json(root / "workstation" / "state" / "steps" / f"{step}.json")
        if payload is None:
            return {"status": "failed", "error": {"code": "input_missing", "message": f"step not found: {step}"}}
        return payload
    return {
        "status": "succeeded",
        "config": read_json(root / "workstation" / "state" / "config.json"),
        "manifest": load_manifest(root),
        "summary": read_json(root / "workstation" / "state" / "summary.json", {}),
    }


def _phase_status(steps: Mapping[str, str]) -> dict[str, Any]:
    values = set(steps.values())
    if not steps:
        status = "pending"
    elif "failed" in values:
        status = "failed"
    elif "needs_review" in values:
        status = "needs_review"
    elif "awaiting_confirmation" in values:
        status = "awaiting_confirmation"
    elif "blocked" in values:
        status = "blocked"
    elif values <= {"succeeded", "skipped"}:
        status = "succeeded"
    else:
        status = "running"
    return {"status": status, "steps": dict(steps)}
