"""Creation and validation of explicit production requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from ..execution.errors import BmlsubError, ErrorCode
from ..media.video import get_current_artifact
from ..state.models import ArtifactRecord, ValidationStatus, utc_now
from ..state.sqlite_store import SQLiteJobStore
from .models import (
    ProductionOperation,
    ProductionRequestInput,
    ProductionRequestRecord,
    ProductionRequestStatus,
)
from .profiles import (
    H264_CHS_PROFILE,
    H264_CHT_PROFILE,
    HEVC_10BIT_PROFILE,
    MKV_SUBTITLE_PROFILE,
    H264HardsubProfile,
    MKVSubtitleProfile,
    normalize_profile,
)


PRODUCTION_REQUEST_SCHEMA_VERSION = "production-request-v1"
_VIDEO_TYPES = {"source.video", "reference.video", "generated.video.hevc"}
_DIRECT_VIDEO_TYPES = {"source.video", "reference.video"}
_SUBTITLE_TYPES = {
    "source.subtitle.ass", "source.subtitle.srt", "generated.subtitle.ass",
    "generated.subtitle.srt", "generated.subtitle.ass.normalized", "subtitle.cht.ass",
    "workstation.subtitle.chs", "workstation.subtitle.cht",
    "workstation.subtitle.delivery.cht",
}
_FONT_TYPES = {"source.font", "generated.font"}
_CHAPTER_TYPES = {"source.chapter"}
_ATTACHMENT_TYPES = {"source.attachment", "generated.attachment"}


def create_production_request(*, workspace: Path | str, episode_id: str,
                              operation: ProductionOperation,
                              video_artifact_id: str,
                              subtitle_artifact_id: str | None = None,
                              subtitle_artifact_ids: Sequence[str] = (),
                              font_artifact_ids: Sequence[str] = (),
                              chapter_artifact_id: str | None = None,
                              attachment_artifact_ids: Sequence[str] = (),
                              output_profile: str = HEVC_10BIT_PROFILE,
                              output_target: Path | str | None = None,
                              parameters: Mapping[str, Any] | None = None,
                              store: SQLiteJobStore | None = None,
                              state_dir: Path | str | None = None) -> ProductionRequestRecord:
    root = Path(workspace).expanduser().resolve()
    if not episode_id.strip():
        raise ValueError("episode_id must not be empty")
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    profile = normalize_profile(operation, output_profile, parameters)
    video = _require_artifact(
        ledger, video_artifact_id, episode_id=episode_id,
        accepted_types=(_DIRECT_VIDEO_TYPES if operation is ProductionOperation.HARDSUB
                        else _VIDEO_TYPES),
        description="video",
    )
    inputs = [ProductionRequestInput(video.artifact_id, "video", 0)]
    if operation is ProductionOperation.HARDSUB:
        if subtitle_artifact_ids:
            raise ValueError("hardsub request accepts one subtitle artifact")
        if not subtitle_artifact_id:
            raise ValueError("hardsub request requires one subtitle artifact")
        subtitle = _require_artifact(
            ledger, subtitle_artifact_id, episode_id=episode_id,
            accepted_types=_SUBTITLE_TYPES, description="subtitle",
        )
        _validate_subtitle_language(subtitle, profile)
        inputs.append(ProductionRequestInput(subtitle.artifact_id, "subtitle", 0))
        _append_unique_inputs(
            inputs, ledger, font_artifact_ids, role="font", episode_id=episode_id,
            accepted_types=_FONT_TYPES,
        )
        if chapter_artifact_id or attachment_artifact_ids:
            raise ValueError("hardsub request does not accept chapter or attachment inputs")
    elif operation is ProductionOperation.MUX_SUBTITLE:
        subtitle_ids = tuple(subtitle_artifact_ids)
        if subtitle_artifact_id:
            subtitle_ids = (subtitle_artifact_id, *subtitle_ids)
        if not subtitle_ids:
            raise ValueError("mux_subtitle request requires at least one subtitle artifact")
        _append_unique_inputs(
            inputs, ledger, subtitle_ids, role="subtitle", episode_id=episode_id,
            accepted_types=_SUBTITLE_TYPES, require_language=True,
        )
        _append_unique_inputs(
            inputs, ledger, font_artifact_ids, role="font", episode_id=episode_id,
            accepted_types=_FONT_TYPES,
        )
        if chapter_artifact_id:
            chapter = _require_artifact(
                ledger, chapter_artifact_id, episode_id=episode_id,
                accepted_types=_CHAPTER_TYPES, description="chapter",
            )
            inputs.append(ProductionRequestInput(chapter.artifact_id, "chapter", 0))
        _append_unique_inputs(
            inputs, ledger, attachment_artifact_ids, role="attachment", episode_id=episode_id,
            accepted_types=_ATTACHMENT_TYPES,
        )
        _validate_mux_profile_ordinals(profile, len(subtitle_ids))
    elif (subtitle_artifact_id or subtitle_artifact_ids or font_artifact_ids or
          chapter_artifact_id or attachment_artifact_ids):
        raise ValueError("encode request does not accept combined inputs")
    target = _output_target(root, episode_id, operation, output_profile, output_target)
    timestamp = utc_now()
    record = ProductionRequestRecord(
        request_id=uuid.uuid4().hex,
        workspace_path=root,
        episode_id=episode_id,
        operation=operation,
        output_profile=output_profile,
        output_target=target,
        parameters=profile.normalized(),
        status=ProductionRequestStatus.PENDING,
        created_at=timestamp,
        updated_at=timestamp,
        inputs=tuple(inputs),
    )
    return ledger.create_production_request(record)


def validate_request_contract(request: ProductionRequestRecord) -> None:
    profile = normalize_profile(request.operation, request.output_profile, request.parameters)
    _validate_target_location(request)
    roles: dict[str, list[ProductionRequestInput]] = {}
    for item in request.inputs:
        roles.setdefault(item.input_role, []).append(item)
    for values in roles.values():
        if [item.ordinal for item in values] != list(range(len(values))):
            raise ValueError("production request input ordinals must be contiguous from zero")
    if request.operation is ProductionOperation.ENCODE:
        if set(roles) != {"video"} or len(roles["video"]) != 1:
            raise ValueError("encode request requires exactly one ordered video input")
        if request.output_target.suffix.lower() != ".mkv":
            raise ValueError("hevc-10bit output target must use the .mkv extension")
        return
    if request.operation is ProductionOperation.HARDSUB:
        if set(roles) - {"video", "subtitle", "font"}:
            raise ValueError("hardsub request contains unsupported input roles")
        if len(roles.get("video", ())) != 1 or len(roles.get("subtitle", ())) != 1:
            raise ValueError("hardsub request requires one video and one subtitle input")
        if request.output_target.suffix.lower() != ".mp4":
            raise ValueError("hardsub output target must use the .mp4 extension")
        if not isinstance(profile, H264HardsubProfile):
            raise ValueError("hardsub request requires an H.264 hardsub profile")
        return
    if request.operation is ProductionOperation.MUX_SUBTITLE:
        if set(roles) - {"video", "subtitle", "font", "chapter", "attachment"}:
            raise ValueError("mux_subtitle request contains unsupported input roles")
        if len(roles.get("video", ())) != 1 or not roles.get("subtitle"):
            raise ValueError("mux_subtitle request requires one video and at least one subtitle input")
        if len(roles.get("chapter", ())) > 1:
            raise ValueError("mux_subtitle request accepts at most one chapter input")
        if request.output_target.suffix.lower() != ".mkv":
            raise ValueError("mux_subtitle output target must use the .mkv extension")
        if not isinstance(profile, MKVSubtitleProfile):
            raise ValueError("mux_subtitle request requires an MKV subtitle profile")
        _validate_mux_profile_ordinals(profile, len(roles["subtitle"]))
        return
    raise ValueError("this production operation is not executable in the current Phase C slice")


def _append_unique_inputs(inputs: list[ProductionRequestInput], store: SQLiteJobStore,
                          artifact_ids: Sequence[str], *, role: str, episode_id: str,
                          accepted_types: set[str], require_language: bool = False) -> None:
    seen: set[str] = set()
    for ordinal, artifact_id in enumerate(artifact_ids):
        if artifact_id in seen:
            raise ValueError(f"{role} artifact IDs must be unique")
        seen.add(artifact_id)
        artifact = _require_artifact(
            store, artifact_id, episode_id=episode_id,
            accepted_types=accepted_types, description=role,
        )
        if require_language:
            language = artifact.metadata.get("language")
            if not isinstance(language, str) or not language.strip():
                raise ValueError("mux_subtitle subtitles must declare a language")
        inputs.append(ProductionRequestInput(artifact.artifact_id, role, ordinal))


def _validate_mux_profile_ordinals(profile: MKVSubtitleProfile | object,
                                   subtitle_count: int) -> None:
    if not isinstance(profile, MKVSubtitleProfile):
        raise ValueError("mux_subtitle request requires an MKV subtitle profile")
    ordinals = set(range(subtitle_count))
    if (profile.default_subtitle_ordinal is not None and
            profile.default_subtitle_ordinal not in ordinals):
        raise ValueError("default_subtitle_ordinal does not reference a selected subtitle")
    if set(profile.forced_subtitle_ordinals) - ordinals:
        raise ValueError("forced_subtitle_ordinals reference unselected subtitles")


def _require_artifact(store: SQLiteJobStore, artifact_id: str, *, episode_id: str,
                      accepted_types: set[str], description: str) -> ArtifactRecord:
    artifact = get_current_artifact(store, artifact_id)
    if (artifact is None or artifact.validation_status is not ValidationStatus.VALID or
            artifact.episode_id != episode_id or artifact.artifact_type not in accepted_types):
        raise BmlsubError(
            f"{description} artifact is not a current input for this episode",
            code=ErrorCode.INPUT_MISSING,
        )
    return artifact


def _validate_subtitle_language(subtitle: ArtifactRecord,
                                profile: H264HardsubProfile | object) -> None:
    if not isinstance(profile, H264HardsubProfile):
        raise ValueError("hardsub request requires an H.264 hardsub profile")
    language = subtitle.metadata.get("language")
    if language not in {"zh-hans", "zh-hant"}:
        raise ValueError("hardsub subtitle must declare language zh-hans or zh-hant")
    if language != profile.language:
        raise ValueError("subtitle language does not match hardsub output profile")


def _validate_target_location(request: ProductionRequestRecord) -> None:
    try:
        request.output_target.relative_to(request.workspace_path)
    except ValueError as exc:
        raise ValueError("production output target must be inside workspace") from exc


def _output_target(root: Path, episode_id: str, operation: ProductionOperation,
                   output_profile: str, output_target: Path | str | None) -> Path:
    defaults = {
        (ProductionOperation.ENCODE, HEVC_10BIT_PROFILE): f"{episode_id}_HEVC10bit.mkv",
        (ProductionOperation.HARDSUB, H264_CHS_PROFILE): f"{episode_id}_CHS.mp4",
        (ProductionOperation.HARDSUB, H264_CHT_PROFILE): f"{episode_id}_CHT.mp4",
        (ProductionOperation.MUX_SUBTITLE, MKV_SUBTITLE_PROFILE): f"{episode_id}_SUBS.mkv",
    }
    default_name = defaults.get((operation, output_profile))
    if default_name is None:
        raise ValueError("unsupported production operation and output profile combination")
    target = (root / "outputs" / episode_id / "video" / default_name
              if output_target is None else Path(output_target).expanduser())
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("production output target must be inside workspace") from exc
    expected_suffix = ".mp4" if operation is ProductionOperation.HARDSUB else ".mkv"
    if target.suffix.lower() != expected_suffix:
        raise ValueError(f"{output_profile} output target must use the {expected_suffix} extension")
    return target
