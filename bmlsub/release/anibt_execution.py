"""StageRunner integration for anibt.net publish."""

from __future__ import annotations

from ..version import __version__

import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import ArtifactWriter
from ..execution.errors import BmlsubError, ErrorCode
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media import get_current_artifact
from ..state.fingerprints import artifact_matches, fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding, StageResult, ValidationStatus
from ..state.sqlite_store import SQLiteJobStore
from .anibt import (
    ANIBT_ADAPTER_VERSION, ANIBT_RECEIPT_SCHEMA,
    AnibtClient, build_receipt_payload,
)
from .external_profiles import AnibtPublishProfile
from .torrent import read_torrent_metadata


ANIBT_PUBLISH_STAGE = "release.publish_anibt"
ANIBT_PUBLISH_ARTIFACT_TYPE = "generated.release.remote.anibt"
ANIBT_EXECUTION_VERSION = "anibt-execution-v7"


def run_anibt_publish(*, workspace: Path | str, episode_id: str,
                       torrent_artifact_id: str,
                       profile: AnibtPublishProfile | Mapping[str, Any],
                       client: AnibtClient, credential_reference: str,
                       api_url: str, token: str,
                       store: SQLiteJobStore | None = None,
                       state_dir: Path | str | None = None,
                       force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()

    # ── 1. 探测：从 SQLite 获取 Artifact 记录并验证文件系统 ──
    torrent = get_current_artifact(ledger, torrent_artifact_id)
    if (torrent is None
            or torrent.validation_status is not ValidationStatus.VALID
            or torrent.artifact_type != "generated.release.torrent"
            or torrent.episode_id != episode_id
            or not torrent.content_hash):
        raise BmlsubError("anibt publish torrent Artifact is not a current formal torrent",
                          code=ErrorCode.INPUT_MISSING)
    if not torrent.path.is_file() or torrent.size <= 0 or not os.access(torrent.path, os.R_OK):
        raise BmlsubError("anibt publish torrent is not a readable non-empty file",
                          code=ErrorCode.INPUT_MISSING)

    # ── 2. 探测：读取 torrent 文件内部元数据 (libtorrent) ──
    try:
        torrent_meta = read_torrent_metadata(torrent.path)
    except Exception as exc:
        raise BmlsubError(
            f"anibt publish torrent metadata is unreadable: {exc}",
            code=ErrorCode.INPUT_MISSING,
        ) from exc

    normalized = profile if isinstance(profile, AnibtPublishProfile) else AnibtPublishProfile.from_mapping(profile)

    # ── 3. 计算输入指纹（包含 Artifact 身份 + torrent 内部元数据） ──
    input_fingerprint = hash_json({
        "artifact_id": torrent.artifact_id,
        "artifact_type": torrent.artifact_type,
        "content_hash": torrent.content_hash,
        "size": torrent.size,
        "torrent_id": torrent_meta.torrent_id,
        "info_hash_v1": torrent_meta.info_hash_v1,
        "info_hash_v2": torrent_meta.info_hash_v2,
        "name": torrent_meta.name,
        "length": torrent_meta.length,
        "format": torrent_meta.format,
        "piece_count": torrent_meta.piece_count,
        "tracker_count": len(torrent_meta.trackers),
    })
    parameter_fingerprint = fingerprint_parameters({
        "profile": normalized.normalized(),
        "credential_reference": credential_reference,
    })
    tool_fingerprint = fingerprint_tools({
        "bmlsub": __version__,
        "client": client.version,
        "adapter": ANIBT_ADAPTER_VERSION,
        "receipt": ANIBT_RECEIPT_SCHEMA,
        "execution": ANIBT_EXECUTION_VERSION,
    })
    target = root / "outputs" / episode_id / "release" / "receipts" / f"{torrent.artifact_id}.anibt.json"

    def artifact_validator(artifact) -> bool:
        if not artifact_matches(artifact, verify_hash=artifact.content_hash is not None):
            return False
        if artifact.artifact_type != ANIBT_PUBLISH_ARTIFACT_TYPE:
            return True
        try:
            data = json.loads(artifact.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != ANIBT_RECEIPT_SCHEMA:
                return False
            if data.get("torrent_artifact_id") != torrent.artifact_id:
                return False
            # 验证 receipt 中的 torrent 元数据与原始种子一致
            receipt_torrent = data.get("torrent") or {}
            if receipt_torrent.get("torrent_id") != torrent_meta.torrent_id:
                return False
            if receipt_torrent.get("info_hash_v1") != torrent_meta.info_hash_v1:
                return False
            if receipt_torrent.get("name") != torrent_meta.name:
                return False
            if receipt_torrent.get("length") != torrent_meta.length:
                return False
            publish = data.get("publish") or {}
            if publish.get("ok") is not True:
                return False
            if publish.get("preview") is not normalized.preview:
                return False
            if "published_at" not in publish:
                return False
            return True
        except Exception:
            return False

    def adapter(context: StageContext) -> StageOutcome:
        mode = "multipart"
        api_response = client.publish(
            torrent_path=torrent.path, profile=normalized,
            api_url=api_url, token=token,
        )
        receipt = build_receipt_payload(
            torrent_artifact_id=torrent.artifact_id,
            profile=normalized, api_response=api_response, mode=mode,
            torrent_meta=torrent_meta.bounded(),
        )
        writer = ArtifactWriter(
            target, workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            artifact_type=ANIBT_PUBLISH_ARTIFACT_TYPE, episode_id=episode_id,
            source_fingerprint=input_fingerprint, parameter_fingerprint=parameter_fingerprint,
            metadata={
                "torrent_artifact_id": torrent.artifact_id,
                "torrent_id": torrent_meta.torrent_id,
                "info_hash_v1": torrent_meta.info_hash_v1,
                "anime_id_type": normalized.anime_id_type,
                "anime_id": normalized.anime_id,
                "torrent_name": torrent_meta.name,
                "torrent_length": torrent_meta.length,
                "format": torrent_meta.format,
                "mode": mode,
                "preview": normalized.preview,
                "receipt_schema": ANIBT_RECEIPT_SCHEMA,
            },
        )
        result = writer.write(
            lambda candidate: candidate.write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            ),
            lambda path: _validate_receipt(
                path, torrent.artifact_id, torrent_meta.torrent_id,
                torrent_meta.info_hash_v1, torrent_meta.name, torrent_meta.length,
            ),
        )
        diagnostics = [Diagnostic(
            code="anibt_publish_succeeded",
            message="anibt.net release published successfully",
            context={
                "torrent_artifact_id": torrent.artifact_id,
                "torrent_id": torrent_meta.torrent_id,
                "info_hash_v1": torrent_meta.info_hash_v1,
                "torrent_name": torrent_meta.name,
                "torrent_length": torrent_meta.length,
                "format": torrent_meta.format,
                "piece_count": torrent_meta.piece_count,
                "tracker_count": len(torrent_meta.trackers),
                "anime_id": normalized.anime_id,
                "anime_id_type": normalized.anime_id_type,
                "mode": mode,
                "preview": normalized.preview,
                "api_ok": api_response.get("ok") if isinstance(api_response, dict) else None,
            },
        )]
        return StageOutcome(artifacts=(result.artifact,), diagnostics=tuple(diagnostics))

    # ── 4. 登记：StageRunner 将输入登记为独立源 Artifact ──
    return StageRunner(ledger, artifact_validator=artifact_validator).run(
        workspace=root, command_name="release.publish-anibt", stage_name=ANIBT_PUBLISH_STAGE,
        episode_id=episode_id, input_fingerprint=input_fingerprint,
        parameter_fingerprint=parameter_fingerprint, tool_fingerprint=tool_fingerprint,
        adapter=adapter,
        inputs=(StageInputBinding(torrent.artifact_id, "torrent", 0),),
        run_metadata={
            "torrent_artifact_id": torrent.artifact_id,
            "torrent_id": torrent_meta.torrent_id,
            "info_hash_v1": torrent_meta.info_hash_v1,
            "torrent_name": torrent_meta.name,
            "torrent_length": torrent_meta.length,
            "format": torrent_meta.format,
            "anime_id": normalized.anime_id,
            "anime_id_type": normalized.anime_id_type,
        },
        force=force,
    )


def _validate_receipt(path: Path, torrent_artifact_id: str, torrent_id: str,
                       info_hash_v1: str, name: str, length: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != ANIBT_RECEIPT_SCHEMA:
        raise ValueError("anibt publish receipt schema is invalid")
    if data.get("torrent_artifact_id") != torrent_artifact_id:
        raise ValueError("anibt publish receipt torrent_artifact_id mismatch")
    # 验证 receipt 中的 torrent 元数据与探测结果一致
    receipt_torrent = data.get("torrent") or {}
    if receipt_torrent.get("torrent_id") != torrent_id:
        raise ValueError("anibt publish receipt torrent_id mismatch")
    if receipt_torrent.get("info_hash_v1") != info_hash_v1:
        raise ValueError("anibt publish receipt info_hash_v1 mismatch")
    if receipt_torrent.get("name") != name:
        raise ValueError("anibt publish receipt torrent name mismatch")
    if receipt_torrent.get("length") != length:
        raise ValueError("anibt publish receipt torrent length mismatch")
    publish = data.get("publish") or {}
    if publish.get("ok") is not True:
        raise ValueError("anibt publish receipt contains a failed API response")
    if not isinstance(publish.get("preview"), bool):
        raise ValueError("anibt publish receipt preview flag is invalid")
    if "published_at" not in publish:
        raise ValueError("anibt publish receipt missing published_at")
