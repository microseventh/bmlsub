"""Reliable local torrent creation Stage."""

from __future__ import annotations

from ..version import __version__

from dataclasses import replace
import os
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactWriter
from ..execution.errors import BmlsubError, ErrorCode
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media import get_current_artifact
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import (
    Diagnostic,
    DiagnosticLevel,
    StageInputBinding,
    StageResult,
    ValidationStatus,
)
from ..state.sqlite_store import SQLiteJobStore
from .profiles import (
    TORRENT_NAMING_VERSION,
    TORRENT_PROFILE_VERSION,
    TorrentProfile,
    normalize_torrent_profile,
)
from .torrent import (
    PIECE_SIZE_POLICY_VERSION,
    TORRENT_BACKEND_VERSION,
    TORRENT_CREATOR_VERSION,
    TORRENT_READER_VERSION,
    TORRENT_VALIDATOR_VERSION,
    TorrentMetadata,
    create_torrent,
    libtorrent_version,
    validate_torrent,
)
from .trackers import (
    TRACKER_BASELINE_VERSION,
    TRACKER_RESOLVER_VERSION,
    TrackerListClient,
    resolve_trackers,
)


TORRENT_STAGE = "release.create_torrent"
TORRENT_ARTIFACT_TYPE = "generated.release.torrent"
_RELEASE_CONTENT_TYPES = {
    "generated.video.hevc",
    "generated.video.hardsub.chs",
    "generated.video.hardsub.cht",
    "generated.video.muxed",
}


def run_torrent_creation(
    *,
    workspace: Path | str,
    episode_id: str,
    content_artifact_id: str,
    profile: TorrentProfile | Mapping[str, Any] | None = None,
    output: Path | str | None = None,
    tracker_client: TrackerListClient | None = None,
    tracker_timeout: float | None = None,
    store: SQLiteJobStore | None = None,
    state_dir: Path | str | None = None,
    force: bool = False,
) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    content = get_current_artifact(ledger, content_artifact_id)
    if (
        content is None
        or content.validation_status is not ValidationStatus.VALID
        or content.episode_id != episode_id
        or content.artifact_type not in _RELEASE_CONTENT_TYPES
    ):
        raise BmlsubError(
            "torrent content Artifact is not a current formal release output",
            code=ErrorCode.INPUT_MISSING,
        )
    if not content.path.is_file() or content.size <= 0 or not os.access(content.path, os.R_OK):
        raise BmlsubError("torrent content is not a readable non-empty file", code=ErrorCode.INPUT_MISSING)
    if content.content_hash is None:
        raise BmlsubError("torrent content Artifact has no content hash", code=ErrorCode.INPUT_MISSING)

    normalized = normalize_torrent_profile(profile)
    if tracker_timeout is not None:
        values = normalized.normalized()
        values.pop("version")
        values["tracker_timeout"] = tracker_timeout
        normalized = TorrentProfile.from_mapping(values)
    backend_version = libtorrent_version()
    trackers = resolve_trackers(
        normalized.tracker_best_url,
        normalized.tracker_timeout,
        client=tracker_client,
    )
    target = _target_path(root, episode_id, content.path.name, output)
    input_fingerprint = hash_json({
        "artifact_id": content.artifact_id,
        "artifact_type": content.artifact_type,
        "content_hash": content.content_hash,
        "name": content.path.name,
        "episode_id": episode_id,
    })
    parameter_fingerprint = fingerprint_parameters({
        "profile": normalized.normalized(),
        "trackers": list(trackers.trackers),
        "tracker_list_sha256": trackers.list_sha256,
        "output": str(target.relative_to(root)),
        "naming_version": TORRENT_NAMING_VERSION,
    })
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__,
        "libtorrent": backend_version,
        "torrent_backend": TORRENT_BACKEND_VERSION,
        "creator": TORRENT_CREATOR_VERSION,
        "reader": TORRENT_READER_VERSION,
        "validator": TORRENT_VALIDATOR_VERSION,
        "piece_policy": PIECE_SIZE_POLICY_VERSION,
        "tracker_baseline": TRACKER_BASELINE_VERSION,
        "tracker_resolver": TRACKER_RESOLVER_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        validation: dict[str, TorrentMetadata] = {}

        def validator(path: Path) -> None:
            validation["metadata"] = validate_torrent(
                path,
                source=content.path,
                expected_trackers=trackers.trackers,
                profile=normalized,
                expected_sha256=content.content_hash,
            )

        writer = ArtifactWriter(
            target,
            workspace=root,
            run_id=context.run_id,
            stage_id=context.stage_id,
            artifact_type=TORRENT_ARTIFACT_TYPE,
            episode_id=episode_id,
            source_fingerprint=input_fingerprint,
            parameter_fingerprint=parameter_fingerprint,
            metadata={
                "source_artifact_id": content.artifact_id,
                "source_content_hash": content.content_hash,
                "profile_version": TORRENT_PROFILE_VERSION,
                "naming_version": TORRENT_NAMING_VERSION,
                "creator_version": TORRENT_CREATOR_VERSION,
                "reader_version": TORRENT_READER_VERSION,
                "validator_version": TORRENT_VALIDATOR_VERSION,
                "piece_policy_version": PIECE_SIZE_POLICY_VERSION,
                "torrent_backend_version": TORRENT_BACKEND_VERSION,
                "libtorrent_version": backend_version,
                "trackers": trackers.provenance(normalized.tracker_best_url),
            },
        )
        result = writer.write(
            lambda candidate: create_torrent(
                content.path,
                candidate,
                trackers=trackers.trackers,
                profile=normalized,
                expected_sha256=content.content_hash,
            ),
            validator,
        )
        metadata = validation["metadata"]
        artifact_metadata = dict(result.artifact.metadata)
        artifact_metadata["torrent"] = metadata.bounded()
        artifact = replace(result.artifact, metadata=artifact_metadata)
        diagnostics: list[Diagnostic] = []
        if result.backup_path:
            diagnostics.append(Diagnostic(
                code="artifact_backup_created",
                message="existing torrent was backed up",
                context={"path": str(result.backup_path)},
            ))
        if trackers.fetch_status == "fallback":
            diagnostics.append(Diagnostic(
                code="tracker_best_fetch_failed",
                message="best tracker list was unavailable; the legacy 42 trackers were used",
                level=DiagnosticLevel.WARNING,
                context={
                    "best_url": normalized.tracker_best_url,
                    "error_type": trackers.error_type,
                    "tracker_count": len(trackers.trackers),
                },
            ))
        else:
            diagnostics.append(Diagnostic(
                code="tracker_best_appended",
                message="current best trackers were appended after the legacy 42 trackers",
                context={
                    "best_count": len(trackers.best_trackers),
                    "tracker_count": len(trackers.trackers),
                    "list_sha256": trackers.list_sha256,
                },
            ))
        diagnostics.append(Diagnostic(
            code="torrent_created",
            message=f"a validated BitTorrent {metadata.format} file was created with libtorrent",
            context={
                "torrent_id": metadata.torrent_id,
                "info_hash_v1": metadata.info_hash_v1,
                "info_hash_v2": metadata.info_hash_v2,
                "format": metadata.format,
                "piece_length": metadata.piece_length,
                "piece_count": metadata.piece_count,
                "tracker_count": len(metadata.trackers),
            },
        ))
        return StageOutcome(artifacts=(artifact,), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root,
        command_name="release.create-torrent",
        stage_name=TORRENT_STAGE,
        episode_id=episode_id,
        input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint,
        tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        inputs=(StageInputBinding(content.artifact_id, "content", 0),),
        run_metadata={
            "input_type": content.artifact_type,
            "tracker_fetch_status": trackers.fetch_status,
            "tracker_count": len(trackers.trackers),
        },
        force=force,
    )


def _target_path(root: Path, episode_id: str, source_name: str,
                 output: Path | str | None) -> Path:
    target = (
        Path(output).expanduser()
        if output is not None
        else Path("outputs") / episode_id / "release" / f"{source_name}.torrent"
    )
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("torrent output must be inside the workspace") from exc
    if target.suffix.lower() != ".torrent":
        raise ValueError("torrent output must use the .torrent extension")
    return target
