"""StageRunner integration for verified server-side R2 pulls."""

from __future__ import annotations

from ..version import __version__

import json
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactWriter
from ..execution.errors import BmlsubError, ErrorCode
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media import get_current_artifact
from ..state.fingerprints import artifact_matches, fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding, StageResult, ValidationStatus
from ..state.sqlite_store import SQLiteJobStore
from .external_profiles import RemotePullProfile
from .r2 import R2_RECEIPT_SCHEMA
from .remote import REMOTE_FILE_RECEIPT_SCHEMA, REMOTE_PULL_ADAPTER_VERSION, RemotePullClient


REMOTE_PULL_STAGE = "release.pull_remote"
REMOTE_FILE_ARTIFACT_TYPE = "generated.release.remote.file"
REMOTE_PULL_EXECUTION_VERSION = "remote-pull-execution-v1"


def run_remote_pull(*, workspace: Path | str, episode_id: str, content_artifact_id: str,
                    r2_receipt_artifact_id: str, profile: RemotePullProfile | Mapping[str, Any],
                    client: RemotePullClient, store: SQLiteJobStore | None = None,
                    state_dir: Path | str | None = None, force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    content = get_current_artifact(ledger, content_artifact_id)
    r2_receipt = get_current_artifact(ledger, r2_receipt_artifact_id)
    if content is None or content.episode_id != episode_id or not content.content_hash:
        raise BmlsubError("remote pull content Artifact is unavailable", code=ErrorCode.INPUT_MISSING)
    if (r2_receipt is None or r2_receipt.episode_id != episode_id
            or r2_receipt.artifact_type != "generated.release.remote.r2"):
        raise BmlsubError("remote pull R2 receipt Artifact is unavailable", code=ErrorCode.INPUT_MISSING)
    normalized = profile if isinstance(profile, RemotePullProfile) else RemotePullProfile.from_mapping(profile)
    receipt_data = json.loads(r2_receipt.path.read_text(encoding="utf-8"))
    if receipt_data.get("schema_version") != R2_RECEIPT_SCHEMA:
        raise BmlsubError("R2 receipt schema is unsupported", code=ErrorCode.INPUT_MISSING)
    remote = receipt_data.get("remote") or {}
    if (receipt_data.get("source_artifact_id") != content.artifact_id
            or remote.get("bucket") != normalized.bucket
            or remote.get("object_key") != normalized.object_key
            or remote.get("sha256") != content.content_hash
            or remote.get("size") != content.size):
        raise BmlsubError("R2 receipt does not match the requested content and remote Profile", code=ErrorCode.INPUT_MISSING)
    input_fingerprint = hash_json({
        "content_artifact_id": content.artifact_id, "content_hash": content.content_hash,
        "r2_receipt_artifact_id": r2_receipt.artifact_id, "r2_receipt_hash": r2_receipt.content_hash,
    })
    parameter_fingerprint = fingerprint_parameters({"profile": normalized.normalized()})
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__, "client": client.version,
        "adapter": REMOTE_PULL_ADAPTER_VERSION, "execution": REMOTE_PULL_EXECUTION_VERSION,
        "receipt": REMOTE_FILE_RECEIPT_SCHEMA,
    })
    target = root / "outputs" / episode_id / "release" / "receipts" / f"{content.artifact_id}.remote.json"

    def artifact_validator(artifact) -> bool:
        if not artifact_matches(artifact, verify_hash=artifact.content_hash is not None):
            return False
        if artifact.artifact_type != REMOTE_FILE_ARTIFACT_TYPE:
            return True
        try:
            data = json.loads(artifact.path.read_text(encoding="utf-8"))
            receipt_profile = RemotePullProfile.from_mapping(data["profile"])
            identity = client.inspect(receipt_profile)
            return identity.size == data["remote_file"]["size"] and identity.sha256 == data["remote_file"]["sha256"]
        except Exception:
            return False

    def adapter(context: StageContext) -> StageOutcome:
        identity = client.pull(
            normalized, run_id=context.run_id,
            expected_size=content.size, expected_sha256=content.content_hash,
        )
        payload = {
            "schema_version": REMOTE_FILE_RECEIPT_SCHEMA,
            "source_artifact_id": content.artifact_id,
            "r2_receipt_artifact_id": r2_receipt.artifact_id,
            "profile": {key: value for key, value in normalized.normalized().items() if key != "version"},
            "r2": {"bucket": normalized.bucket, "object_key": normalized.object_key},
            "remote_file": identity.bounded(),
        }
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            artifact_type=REMOTE_FILE_ARTIFACT_TYPE, episode_id=episode_id,
            source_fingerprint=input_fingerprint, parameter_fingerprint=parameter_fingerprint,
            metadata={
                "source_artifact_id": content.artifact_id,
                "r2_receipt_artifact_id": r2_receipt.artifact_id,
                "ssh_alias": normalized.ssh_alias, "remote_path": identity.path,
                "remote_size": identity.size, "remote_sha256": identity.sha256,
                "receipt_schema": REMOTE_FILE_RECEIPT_SCHEMA,
            },
        )
        result = writer.write(
            lambda candidate: candidate.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"),
            lambda path: _validate_receipt(path, content.artifact_id, content.content_hash, content.size),
        )
        return StageOutcome(
            artifacts=(result.artifact,), diagnostics=(Diagnostic(
                code="remote_pull_verified", message="server-side R2 pull and SHA-256 validation succeeded",
                context={"ssh_alias": normalized.ssh_alias, "remote_path": identity.path,
                         "size": identity.size, "sha256": identity.sha256},
            ),),
        )

    return StageRunner(ledger, artifact_validator=artifact_validator).run(
        workspace=root, command_name="release.pull-remote", stage_name=REMOTE_PULL_STAGE,
        episode_id=episode_id, input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint, tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        inputs=(StageInputBinding(content.artifact_id, "content", 0),
                StageInputBinding(r2_receipt.artifact_id, "r2_receipt", 0)),
        run_metadata={"ssh_alias": normalized.ssh_alias, "remote_path": normalized.target_path,
                      "bucket": normalized.bucket, "object_key": normalized.object_key},
        force=force,
    )


def _validate_receipt(path: Path, source_id: str, sha256: str, size: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != REMOTE_FILE_RECEIPT_SCHEMA or data.get("source_artifact_id") != source_id:
        raise ValueError("remote file receipt identity is invalid")
    remote = data.get("remote_file") or {}
    if remote.get("sha256") != sha256 or remote.get("size") != size:
        raise ValueError("remote file receipt integrity fields are invalid")
