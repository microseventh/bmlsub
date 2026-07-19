"""StageRunner integration for verified qBittorrent seeding."""

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
from ..state.models import Diagnostic, DiagnosticLevel, StageInputBinding, StageResult
from ..state.sqlite_store import SQLiteJobStore
from .external_profiles import QBittorrentSeedProfile
from .qbittorrent import (
    QB_ADAPTER_VERSION, QB_SEED_RECEIPT_SCHEMA, QB_VALIDATOR_VERSION,
    QBittorrentClient, validate_seed_identity,
)
from .remote import REMOTE_FILE_RECEIPT_SCHEMA
from .torrent import read_torrent_metadata


QB_SEED_STAGE = "release.seed_qbittorrent"
QB_SEED_ARTIFACT_TYPE = "generated.release.remote.seed"
QB_SEED_EXECUTION_VERSION = "qb-seed-execution-v5"


def run_qbittorrent_seed(*, workspace: Path | str, episode_id: str,
                         torrent_artifact_id: str, content_artifact_id: str,
                         remote_content_artifact_id: str,
                         remote_torrent_artifact_id: str,
                         profile: QBittorrentSeedProfile | Mapping[str, Any],
                         client: QBittorrentClient, credential_reference: str,
                         store: SQLiteJobStore | None = None,
                         state_dir: Path | str | None = None, force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    torrent = get_current_artifact(ledger, torrent_artifact_id)
    content = get_current_artifact(ledger, content_artifact_id)
    remote_content = get_current_artifact(ledger, remote_content_artifact_id)
    remote_torrent = get_current_artifact(ledger, remote_torrent_artifact_id)
    if torrent is None or torrent.artifact_type != "generated.release.torrent":
        raise BmlsubError("qBittorrent torrent Artifact is unavailable", code=ErrorCode.INPUT_MISSING)
    if content is None or not content.content_hash or content.episode_id != episode_id:
        raise BmlsubError("qBittorrent content Artifact is unavailable", code=ErrorCode.INPUT_MISSING)
    if (remote_content is None or remote_content.artifact_type != "generated.release.remote.file"
            or remote_content.episode_id != episode_id):
        raise BmlsubError("qBittorrent remote content receipt is unavailable", code=ErrorCode.INPUT_MISSING)
    if (remote_torrent is None or remote_torrent.artifact_type != "generated.release.remote.file"
            or remote_torrent.episode_id != episode_id):
        raise BmlsubError("qBittorrent remote torrent receipt is unavailable", code=ErrorCode.INPUT_MISSING)
    normalized = profile if isinstance(profile, QBittorrentSeedProfile) else QBittorrentSeedProfile.from_mapping(profile)
    remote_data = json.loads(remote_content.path.read_text(encoding="utf-8"))
    remote_torrent_data = json.loads(remote_torrent.path.read_text(encoding="utf-8"))
    if remote_data.get("schema_version") != REMOTE_FILE_RECEIPT_SCHEMA:
        raise BmlsubError("remote content receipt schema is unsupported", code=ErrorCode.INPUT_MISSING)
    if remote_torrent_data.get("schema_version") != REMOTE_FILE_RECEIPT_SCHEMA:
        raise BmlsubError("remote torrent receipt schema is unsupported", code=ErrorCode.INPUT_MISSING)
    remote_file = remote_data.get("remote_file") or {}
    if (remote_data.get("source_artifact_id") != content.artifact_id
            or remote_file.get("size") != content.size
            or remote_file.get("sha256") != content.content_hash):
        raise BmlsubError("remote content receipt does not match the content Artifact", code=ErrorCode.INPUT_MISSING)
    remote_torrent_file = remote_torrent_data.get("remote_file") or {}
    if (remote_torrent_data.get("source_artifact_id") != torrent.artifact_id
            or remote_torrent_file.get("size") != torrent.size
            or remote_torrent_file.get("sha256") != torrent.content_hash):
        raise BmlsubError("remote torrent receipt does not match the torrent Artifact", code=ErrorCode.INPUT_MISSING)
    metadata = read_torrent_metadata(torrent.path)
    if metadata.name != content.path.name or metadata.length != content.size:
        raise BmlsubError("torrent does not map the selected content Artifact", code=ErrorCode.INPUT_MISSING)
    info_hash_v2 = getattr(metadata, "info_hash_v2", None)
    alternate_hashes = tuple(dict.fromkeys(
        value for value in (
            info_hash_v2,
            info_hash_v2[:40] if info_hash_v2 else None,
        ) if value
    ))
    input_fingerprint = hash_json({
        "torrent_artifact_id": torrent.artifact_id, "torrent_hash": torrent.content_hash,
        "content_artifact_id": content.artifact_id, "content_hash": content.content_hash,
        "remote_content_artifact_id": remote_content.artifact_id,
        "remote_content_hash": remote_content.content_hash,
        "remote_torrent_artifact_id": remote_torrent.artifact_id,
        "remote_torrent_hash": remote_torrent.content_hash,
        "torrent_id": metadata.torrent_id,
    })
    parameter_fingerprint = fingerprint_parameters({
        "profile": normalized.normalized(), "credential_reference": credential_reference,
    })
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__, "client": client.version, "adapter": QB_ADAPTER_VERSION,
        "validator": QB_VALIDATOR_VERSION, "execution": QB_SEED_EXECUTION_VERSION,
        "receipt": QB_SEED_RECEIPT_SCHEMA,
    })
    target = root / "outputs" / episode_id / "release" / "receipts" / f"{metadata.torrent_id}.seed.json"

    def artifact_validator(artifact) -> bool:
        if not artifact_matches(artifact, verify_hash=artifact.content_hash is not None):
            return False
        if artifact.artifact_type != QB_SEED_ARTIFACT_TYPE:
            return True
        try:
            data = json.loads(artifact.path.read_text(encoding="utf-8"))
            receipt_profile = QBittorrentSeedProfile.from_mapping(data["profile"])
            identity = client.inspect(torrent_hash=data["seed"]["torrent_hash"], profile=receipt_profile)
            validate_seed_identity(
                identity, expected_hash=metadata.torrent_id, expected_name=metadata.name,
                expected_size=metadata.length, save_path=receipt_profile.save_path,
                alternate_hashes=alternate_hashes,
            )
            return True
        except Exception:
            return False

    def adapter(context: StageContext) -> StageOutcome:
        identity = client.add_and_verify(
            torrent_path=torrent.path, magnet_uri=metadata.magnet_uri,
            expected_hash=metadata.torrent_id, expected_name=metadata.name,
            expected_size=metadata.length, profile=normalized,
            alternate_hashes=alternate_hashes,
        )
        payload = {
            "schema_version": QB_SEED_RECEIPT_SCHEMA,
            "torrent_artifact_id": torrent.artifact_id,
            "content_artifact_id": content.artifact_id,
            "remote_content_artifact_id": remote_content.artifact_id,
            "remote_torrent_artifact_id": remote_torrent.artifact_id,
            "remote_torrent_path": remote_torrent_file["path"],
            "torrent_source": "local-validated-artifact",
            "r2_vps_torrent_receipt_artifact_id": remote_torrent.artifact_id,
            "profile": {key: value for key, value in normalized.normalized().items() if key != "version"},
            "seed": identity.bounded(),
        }
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            artifact_type=QB_SEED_ARTIFACT_TYPE, episode_id=episode_id,
            source_fingerprint=input_fingerprint, parameter_fingerprint=parameter_fingerprint,
            metadata={
                "torrent_artifact_id": torrent.artifact_id,
                "content_artifact_id": content.artifact_id,
                "remote_content_artifact_id": remote_content.artifact_id,
                "remote_torrent_artifact_id": remote_torrent.artifact_id,
                "remote_torrent_path": remote_torrent_file["path"],
                "torrent_source": "local-validated-artifact",
                "r2_vps_torrent_receipt_artifact_id": remote_torrent.artifact_id,
                "torrent_id": identity.torrent_hash, "state": identity.state,
                "remote_size": identity.total_size, "save_path": identity.save_path,
                "receipt_schema": QB_SEED_RECEIPT_SCHEMA,
            },
        )
        result = writer.write(
            lambda candidate: candidate.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"),
            lambda path: _validate_receipt(
                path, metadata.torrent_id, metadata.length, alternate_hashes=alternate_hashes,
            ),
        )
        diagnostics = [Diagnostic(
            code="qbittorrent_seed_verified",
            message="qBittorrent completed content checking and is seeding the verified content",
            context={"torrent_id": identity.torrent_hash, "state": identity.state,
                     "progress": identity.progress, "amount_left": identity.amount_left,
                     "size": identity.total_size, "save_path": identity.save_path},
        )]
        if identity.used_magnet_fallback:
            diagnostics.append(Diagnostic(
                code="qbittorrent_magnet_fallback",
                message="qBittorrent was added with the validated v1 magnet fallback",
                level=DiagnosticLevel.WARNING,
                context={"torrent_id": identity.torrent_hash},
            ))
        return StageOutcome(artifacts=(result.artifact,), diagnostics=tuple(diagnostics))

    return StageRunner(ledger, artifact_validator=artifact_validator).run(
        workspace=root, command_name="release.seed-qbittorrent", stage_name=QB_SEED_STAGE,
        episode_id=episode_id, input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint, tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        inputs=(StageInputBinding(torrent.artifact_id, "torrent", 0),
                StageInputBinding(content.artifact_id, "content", 0),
                StageInputBinding(remote_content.artifact_id, "remote_content", 0),
                StageInputBinding(remote_torrent.artifact_id, "remote_torrent", 0)),
        run_metadata={"ssh_alias": normalized.ssh_alias, "save_path": normalized.save_path,
                      "torrent_id": metadata.torrent_id},
        force=force,
    )


def _validate_receipt(path: Path, torrent_id: str, size: int,
                      alternate_hashes: tuple[str, ...] = ()) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != QB_SEED_RECEIPT_SCHEMA:
        raise ValueError("qBittorrent seed receipt schema is invalid")
    seed = data.get("seed") or {}
    expected_hashes = {torrent_id, *alternate_hashes}
    if seed.get("torrent_hash") not in expected_hashes or seed.get("total_size") != size:
        raise ValueError("qBittorrent seed receipt identity is invalid")
