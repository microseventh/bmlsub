"""StageRunner integration for Cloudflare R2 uploads."""

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
from .external_profiles import R2UploadProfile
from .r2 import R2_ADAPTER_VERSION, R2_RECEIPT_SCHEMA, R2_VALIDATOR_VERSION, R2Client, validate_remote_object


R2_UPLOAD_STAGE = "release.upload_r2"
R2_RECEIPT_ARTIFACT_TYPE = "generated.release.remote.r2"
R2_EXECUTION_VERSION = "r2-execution-v1"
_ALLOWED_UPLOAD_TYPES = {
    "generated.video.hevc", "generated.video.hardsub.chs",
    "generated.video.hardsub.cht", "generated.video.muxed",
    "generated.release.torrent",
}


def run_r2_upload(*, workspace: Path | str, episode_id: str, artifact_id: str,
                  profile: R2UploadProfile | Mapping[str, Any], client: R2Client,
                  credential_reference: str, store: SQLiteJobStore | None = None,
                  state_dir: Path | str | None = None, force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    source = get_current_artifact(ledger, artifact_id)
    if (source is None or source.episode_id != episode_id
            or source.validation_status is not ValidationStatus.VALID
            or source.artifact_type not in _ALLOWED_UPLOAD_TYPES
            or not source.content_hash):
        raise BmlsubError("R2 input is not a current formal release Artifact", code=ErrorCode.INPUT_MISSING)
    normalized = profile if isinstance(profile, R2UploadProfile) else R2UploadProfile.from_mapping(profile)
    input_fingerprint = hash_json({
        "artifact_id": source.artifact_id, "artifact_type": source.artifact_type,
        "content_hash": source.content_hash, "size": source.size,
    })
    parameter_fingerprint = fingerprint_parameters({
        "profile": normalized.normalized(), "credential_reference": credential_reference,
    })
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__, "client": client.version,
        "adapter": R2_ADAPTER_VERSION, "validator": R2_VALIDATOR_VERSION,
        "execution": R2_EXECUTION_VERSION, "receipt": R2_RECEIPT_SCHEMA,
    })
    target = root / "outputs" / episode_id / "release" / "receipts" / f"{source.artifact_id}.r2.json"

    def artifact_validator(artifact) -> bool:
        if not artifact_matches(artifact, verify_hash=artifact.content_hash is not None):
            return False
        if artifact.artifact_type != R2_RECEIPT_ARTIFACT_TYPE:
            return True
        try:
            data = json.loads(artifact.path.read_text(encoding="utf-8"))
            receipt_profile = R2UploadProfile.from_mapping(data["profile"])
            validate_remote_object(
                client, receipt_profile, expected_size=int(data["remote"]["size"]),
                expected_sha256=str(data["remote"]["sha256"]),
            )
            return True
        except Exception:
            return False

    def adapter(context: StageContext) -> StageOutcome:
        metadata = {
            "bml-sha256": source.content_hash,
            "bml-artifact-id": source.artifact_id,
            "bml-schema": R2_RECEIPT_SCHEMA,
        }
        client.upload(source.path, normalized, metadata=metadata)
        identity = validate_remote_object(
            client, normalized, expected_size=source.size, expected_sha256=source.content_hash,
        )
        payload = {
            "schema_version": R2_RECEIPT_SCHEMA,
            "source_artifact_id": source.artifact_id,
            "source_artifact_type": source.artifact_type,
            "profile": {key: value for key, value in normalized.normalized().items() if key != "version"},
            "remote": identity.bounded(),
        }
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            artifact_type=R2_RECEIPT_ARTIFACT_TYPE, episode_id=episode_id,
            source_fingerprint=input_fingerprint, parameter_fingerprint=parameter_fingerprint,
            metadata={
                "source_artifact_id": source.artifact_id, "provider": "cloudflare-r2",
                "bucket": identity.bucket, "object_key": identity.object_key,
                "remote_size": identity.size, "remote_sha256": identity.sha256,
                "receipt_schema": R2_RECEIPT_SCHEMA,
            },
        )
        result = writer.write(
            lambda candidate: candidate.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"),
            lambda path: _validate_receipt(path, source.artifact_id, source.content_hash, source.size),
        )
        return StageOutcome(
            artifacts=(result.artifact,),
            diagnostics=(Diagnostic(
                code="r2_upload_verified", message="R2 object upload and HEAD validation succeeded",
                context={"bucket": identity.bucket, "object_key": identity.object_key,
                         "size": identity.size, "sha256": identity.sha256,
                         "access": normalized.access},
            ),),
        )

    return StageRunner(ledger, artifact_validator=artifact_validator).run(
        workspace=root, command_name="release.upload-r2", stage_name=R2_UPLOAD_STAGE,
        episode_id=episode_id, input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint, tool_fingerprint=tool_fingerprint,
        adapter=adapter, inputs=(StageInputBinding(source.artifact_id, "upload", 0),),
        run_metadata={"provider": "cloudflare-r2", "bucket": normalized.bucket,
                      "object_key": normalized.object_key, "access": normalized.access},
        force=force,
    )


def _validate_receipt(path: Path, source_artifact_id: str, sha256: str, size: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != R2_RECEIPT_SCHEMA:
        raise ValueError("R2 receipt schema is invalid")
    if data.get("source_artifact_id") != source_artifact_id:
        raise ValueError("R2 receipt source identity is invalid")
    remote = data.get("remote") or {}
    if remote.get("sha256") != sha256 or remote.get("size") != size:
        raise ValueError("R2 receipt integrity fields are invalid")
