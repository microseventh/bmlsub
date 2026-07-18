"""Execution of explicit production requests."""

from __future__ import annotations

from ..version import __version__

from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from typing import Any

from ..artifacts import ArtifactBatchWriter, ArtifactWriteSpec
from ..execution import PROCESS_RUNNER_VERSION, BmlsubError, ErrorCode, ProcessRunner
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media import FFprobeClient, MediaStreamSummary, MediaSummary, get_current_artifact
from ..media.validators import (
    validate_hardsub_video_output, validate_muxed_video_output, validate_video_output,
)
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import (
    ArtifactRecord, Diagnostic, StageInputBinding, StageResult, StageStatus, ValidationStatus,
)
from ..state.sqlite_store import SQLiteJobStore
from .models import ProductionOperation, ProductionRequestInput, ProductionRequestStatus
from .matroska import MKVmergeClient
from .profiles import (
    HARDSUB_ARGV_VERSION,
    HARDSUB_NAMING_VERSION,
    HARDSUB_VALIDATOR_VERSION,
    HEVC_NAMING_VERSION,
    HEVC_PROFILE_VERSION,
    HEVC_VALIDATOR_VERSION,
    MUX_SUBTITLE_ARGV_VERSION,
    MUX_SUBTITLE_NAMING_VERSION,
    MUX_SUBTITLE_VALIDATOR_VERSION,
    H264HardsubProfile,
    HEVC10BitProfile,
    MKVSubtitleProfile,
    normalize_profile,
)
from .requests import validate_request_contract


HEVC_ENCODE_STAGE = "production.encode_hevc"
HARDSUB_ENCODE_STAGE = "production.encode_hardsub"
MUX_SUBTITLE_STAGE = "production.mux_subtitle"
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


def run_production_request(request_id: str, *, workspace: Path | str,
                           ffmpeg: Path | str = "ffmpeg",
                           ffprobe: Path | str = "ffprobe",
                           mkvmerge: Path | str = "mkvmerge",
                           process_timeout: float = 7200.0,
                           probe_timeout: float = 30.0,
                           runner: ProcessRunner | None = None,
                           probe: FFprobeClient | None = None,
                           mkv_inspector: MKVmergeClient | None = None,
                           store: SQLiteJobStore | None = None,
                           state_dir: Path | str | None = None,
                           force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    request = ledger.get_production_request(request_id)
    if request is None or request.workspace_path != root:
        raise BmlsubError("production request was not found", code=ErrorCode.INPUT_MISSING)
    validate_request_contract(request)
    artifacts = _resolve_inputs(ledger, request)
    profile = normalize_profile(request.operation, request.output_profile, request.parameters)
    process = runner or ProcessRunner(timeout=process_timeout)
    inspector = probe or FFprobeClient(ffprobe, timeout=probe_timeout)
    matroska = mkv_inspector or MKVmergeClient(mkvmerge, timeout=probe_timeout, runner=process)
    ffprobe_version = inspector.version()
    tool_values = {
        "bmlsub": __version__, "ffprobe": ffprobe_version,
        "process_runner": PROCESS_RUNNER_VERSION, "validator": _validator_version(profile),
    }
    if isinstance(profile, MKVSubtitleProfile):
        tool_values["mkvmerge"] = matroska.version()
        tool_values["argv_builder"] = MUX_SUBTITLE_ARGV_VERSION
    else:
        tool_values["ffmpeg"] = process.version(ffmpeg)
        if isinstance(profile, H264HardsubProfile):
            tool_values["argv_builder"] = HARDSUB_ARGV_VERSION
    video = artifacts["video"][0]
    source_summary = _source_media_summary(video)
    source_video = _source_video_stream(video)
    source_duration = _source_duration(video)
    input_fp = hash_json({
        "request_id": request.request_id,
        "inputs": [
            {
                "artifact_id": artifact.artifact_id,
                "fingerprint": artifact.source_fingerprint or artifact.content_hash,
                "role": binding.input_role,
                "ordinal": binding.ordinal,
            }
            for binding, artifact in _ordered_inputs(request.inputs, artifacts)
        ],
    })
    parameter_fp = fingerprint_parameters({
        "operation": request.operation.value,
        "output_profile": request.output_profile,
        "output_target": str(request.output_target.relative_to(root)),
        "profile": profile.normalized(),
        "profile_version": _profile_version(profile),
        "naming_version": _naming_version(profile),
    })
    tool_fp = fingerprint_tools(tool_values)

    def adapter(context: StageContext) -> StageOutcome:
        if isinstance(profile, HEVC10BitProfile):
            return _encode_hevc(
                context, request=request, video=video, profile=profile, root=root,
                ffmpeg=ffmpeg, process_timeout=process_timeout, process=process,
                inspector=inspector, source_video=source_video,
                source_duration=source_duration, input_fp=input_fp,
                parameter_fp=parameter_fp,
            )
        if isinstance(profile, H264HardsubProfile):
            return _encode_hardsub(
                context, request=request, video=video, subtitle=artifacts["subtitle"][0],
                fonts=tuple(artifacts.get("font", ())), profile=profile, root=root,
                ffmpeg=ffmpeg, process_timeout=process_timeout, process=process,
                inspector=inspector, source_video=source_video,
                source_duration=source_duration, input_fp=input_fp,
                parameter_fp=parameter_fp,
            )
        return _mux_subtitles(
            context, request=request, video=video,
            subtitles=tuple(artifacts["subtitle"]), fonts=tuple(artifacts.get("font", ())),
            chapter=(artifacts.get("chapter") or [None])[0],
            attachments=tuple(artifacts.get("attachment", ())), profile=profile, root=root,
            mkvmerge=mkvmerge, process_timeout=process_timeout, process=process,
            inspector=inspector, matroska=matroska, source_summary=source_summary,
            input_fp=input_fp, parameter_fp=parameter_fp,
        )

    ledger.transition_production_request(request_id, ProductionRequestStatus.RUNNING)
    result = StageRunner(ledger).run(
        workspace=root, command_name="production.execute",
        stage_name=(HEVC_ENCODE_STAGE if request.operation is ProductionOperation.ENCODE
                    else HARDSUB_ENCODE_STAGE if request.operation is ProductionOperation.HARDSUB
                    else MUX_SUBTITLE_STAGE),
        episode_id=request.episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter,
        inputs=tuple(
            StageInputBinding(item.artifact_id, item.input_role, item.ordinal)
            for item in request.inputs
        ),
        run_metadata={"production_request_id": request.request_id}, force=force,
    )
    _update_request_status(ledger, request, result)
    return result


def _encode_hevc(context: StageContext, *, request, video: ArtifactRecord,
                 profile: HEVC10BitProfile, root: Path, ffmpeg: Path | str,
                 process_timeout: float, process: ProcessRunner, inspector: FFprobeClient,
                 source_video: dict[str, int | None], source_duration: int | None,
                 input_fp: str, parameter_fp: str) -> StageOutcome:
    validation: dict[str, Any] = {}

    def validator(path: Path) -> None:
        validation.update(validate_video_output(
            path, probe=inspector, source_width=source_video.get("width"),
            source_height=source_video.get("height"), source_duration_ms=source_duration,
            include_audio=profile.include_audio,
        ))

    spec = ArtifactWriteSpec(
        target=request.output_target, artifact_type="generated.video.hevc", validator=validator,
        metadata={
            "production_request_id": request.request_id,
            "source_video_artifact_id": video.artifact_id,
            "operation": request.operation.value, "output_profile": request.output_profile,
            "profile": profile.normalized(), "profile_version": HEVC_PROFILE_VERSION,
            "naming_version": HEVC_NAMING_VERSION,
            "validator_version": HEVC_VALIDATOR_VERSION,
        },
    )

    def produce(paths: tuple[Path, ...]) -> None:
        argv = [
            str(ffmpeg), "-nostdin", "-y", "-v", "error", "-i", str(video.path),
            "-map", "0:v:0",
        ]
        if profile.include_audio:
            argv.extend(["-map", "0:a?"])
        argv.extend(["-sn", "-dn"])
        argv.extend(profile.video_argv())
        argv.extend(profile.audio_argv())
        if profile.strip_metadata:
            argv.extend(["-map_metadata", "-1", "-map_chapters", "-1"])
        argv.extend(["-f", "matroska", str(paths[0])])
        process.run(argv, timeout=process_timeout)

    return _write_video(
        context, request=request, root=root, input_fp=input_fp, parameter_fp=parameter_fp,
        spec=spec, produce=produce, validation=validation,
        message="production request generated a validated HEVC 10-bit video",
    )


def _encode_hardsub(context: StageContext, *, request, video: ArtifactRecord,
                    subtitle: ArtifactRecord, fonts: tuple[ArtifactRecord, ...],
                    profile: H264HardsubProfile, root: Path, ffmpeg: Path | str,
                    process_timeout: float, process: ProcessRunner, inspector: FFprobeClient,
                    source_video: dict[str, int | None], source_duration: int | None,
                    input_fp: str, parameter_fp: str) -> StageOutcome:
    validation: dict[str, Any] = {}

    def validator(path: Path) -> None:
        validation.update(validate_hardsub_video_output(
            path, probe=inspector, source_width=source_video.get("width"),
            source_height=source_video.get("height"), source_duration_ms=source_duration,
            include_audio=profile.include_audio,
        ))

    language_key = "chs" if profile.language == "zh-hans" else "cht"
    spec = ArtifactWriteSpec(
        target=request.output_target,
        artifact_type=f"generated.video.hardsub.{language_key}", validator=validator,
        metadata={
            "production_request_id": request.request_id,
            "source_video_artifact_id": video.artifact_id,
            "subtitle_artifact_id": subtitle.artifact_id,
            "font_artifact_ids": [item.artifact_id for item in fonts],
            "language": profile.language, "operation": request.operation.value,
            "output_profile": request.output_profile, "profile": profile.normalized(),
            "profile_version": profile.profile_version,
            "argv_builder_version": HARDSUB_ARGV_VERSION,
            "naming_version": HARDSUB_NAMING_VERSION,
            "validator_version": HARDSUB_VALIDATOR_VERSION,
            "burn_in_evidence": "ffmpeg_ass_filter_completed",
        },
    )

    def produce(paths: tuple[Path, ...]) -> None:
        with tempfile.TemporaryDirectory(prefix="bmlsub-hardsub-") as directory:
            staging = Path(directory)
            staged_subtitle = staging / "subtitle.ass"
            shutil.copyfile(subtitle.path, staged_subtitle)
            fonts_dir = staging / "fonts"
            fonts_dir.mkdir()
            for ordinal, font in enumerate(fonts):
                suffix = font.path.suffix.lower()
                shutil.copyfile(font.path, fonts_dir / f"font_{ordinal:03d}{suffix}")
            filter_value = (
                f"ass=filename='{_escape_filter_path(staged_subtitle)}':"
                f"fontsdir='{_escape_filter_path(fonts_dir)}'"
            )
            argv = [
                str(ffmpeg), "-nostdin", "-y", "-v", "error", "-i", str(video.path),
                "-map", "0:v:0",
            ]
            if profile.include_audio:
                argv.extend(["-map", "0:a?"])
            argv.extend(["-sn", "-dn", "-vf", filter_value])
            argv.extend(profile.video_argv())
            argv.extend(profile.audio_argv())
            if profile.strip_metadata:
                argv.extend(["-map_metadata", "-1", "-map_chapters", "-1"])
            argv.extend(["-movflags", "+faststart", "-f", "mp4", str(paths[0])])
            process.run(argv, timeout=process_timeout)

    return _write_video(
        context, request=request, root=root, input_fp=input_fp, parameter_fp=parameter_fp,
        spec=spec, produce=produce, validation=validation,
        message="production request generated a validated H.264 hardsub video",
    )


def _mux_subtitles(context: StageContext, *, request, video: ArtifactRecord,
                   subtitles: tuple[ArtifactRecord, ...], fonts: tuple[ArtifactRecord, ...],
                   chapter: ArtifactRecord | None, attachments: tuple[ArtifactRecord, ...],
                   profile: MKVSubtitleProfile, root: Path, mkvmerge: Path | str,
                   process_timeout: float, process: ProcessRunner, inspector: FFprobeClient,
                   matroska: MKVmergeClient, source_summary: MediaSummary,
                   input_fp: str, parameter_fp: str) -> StageOutcome:
    validation: dict[str, Any] = {}

    def validator(path: Path) -> None:
        validation.update(validate_muxed_video_output(
            path, probe=inspector, mkvmerge=matroska, source_summary=source_summary,
            subtitles=subtitles, fonts=fonts, chapter=chapter, attachments=attachments,
            profile=profile,
        ))

    spec = ArtifactWriteSpec(
        target=request.output_target, artifact_type="generated.video.muxed", validator=validator,
        metadata={
            "production_request_id": request.request_id,
            "source_video_artifact_id": video.artifact_id,
            "subtitle_artifact_ids": [item.artifact_id for item in subtitles],
            "font_artifact_ids": [item.artifact_id for item in fonts],
            "chapter_artifact_id": chapter.artifact_id if chapter else None,
            "attachment_artifact_ids": [item.artifact_id for item in attachments],
            "operation": request.operation.value, "output_profile": request.output_profile,
            "profile": profile.normalized(), "profile_version": profile.profile_version,
            "argv_builder_version": MUX_SUBTITLE_ARGV_VERSION,
            "naming_version": MUX_SUBTITLE_NAMING_VERSION,
            "validator_version": MUX_SUBTITLE_VALIDATOR_VERSION,
        },
    )

    def produce(paths: tuple[Path, ...]) -> None:
        argv = [str(mkvmerge), "--output", str(paths[0]), "--no-subtitles", "--no-attachments",
                "--no-chapters"]
        if not profile.include_audio:
            argv.append("--no-audio")
        argv.append(str(video.path))
        for ordinal, subtitle in enumerate(subtitles):
            language = str(subtitle.metadata["language"])
            argv.extend([
                "--language", f"0:{language}",
                "--track-name", f"0:{language}",
                "--default-track-flag", f"0:{'yes' if profile.default_subtitle_ordinal == ordinal else 'no'}",
                "--forced-display-flag", f"0:{'yes' if ordinal in profile.forced_subtitle_ordinals else 'no'}",
                str(subtitle.path),
            ])
        if chapter is not None:
            argv.extend(["--chapters", str(chapter.path)])
        for artifact in (*fonts, *attachments):
            mime_type = str(artifact.metadata.get("mime_type") or _attachment_mime(artifact.path))
            argv.extend([
                "--attachment-name", artifact.path.name,
                "--attachment-mime-type", mime_type,
                "--attach-file", str(artifact.path),
            ])
        process.run(argv, timeout=process_timeout)

    return _write_video(
        context, request=request, root=root, input_fp=input_fp, parameter_fp=parameter_fp,
        spec=spec, produce=produce, validation=validation,
        message="production request generated a validated internal-subtitle MKV",
    )


def _write_video(context: StageContext, *, request, root: Path, input_fp: str,
                 parameter_fp: str, spec: ArtifactWriteSpec, produce,
                 validation: dict[str, Any], message: str) -> StageOutcome:
    writer = ArtifactBatchWriter(
        workspace=root, run_id=context.run_id, stage_id=context.stage_id,
        episode_id=request.episode_id, source_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp,
    )
    result = writer.write((spec,), produce)[0]
    metadata = dict(result.artifact.metadata)
    metadata["media"] = validation
    artifact = replace(result.artifact, metadata=metadata)
    diagnostics = []
    if result.backup_path:
        diagnostics.append(Diagnostic(
            code="artifact_backup_created", message="existing encoded video was backed up",
            context={"path": str(result.backup_path)},
        ))
    diagnostics.append(Diagnostic(
        code="video_encoded", message=message,
        context={"request_id": request.request_id, "output_profile": request.output_profile},
    ))
    return StageOutcome(artifacts=(artifact,), diagnostics=tuple(diagnostics))


def _resolve_inputs(ledger: SQLiteJobStore, request) -> dict[str, list[ArtifactRecord]]:
    accepted = {
        "video": (_DIRECT_VIDEO_TYPES if request.operation is ProductionOperation.HARDSUB
                  else _VIDEO_TYPES),
        "subtitle": _SUBTITLE_TYPES, "font": _FONT_TYPES,
        "chapter": _CHAPTER_TYPES, "attachment": _ATTACHMENT_TYPES,
    }
    artifacts: dict[str, list[ArtifactRecord]] = {}
    for item in request.inputs:
        artifact = get_current_artifact(ledger, item.artifact_id)
        if (artifact is None or artifact.validation_status is not ValidationStatus.VALID or
                artifact.episode_id != request.episode_id or
                artifact.artifact_type not in accepted.get(item.input_role, set())):
            raise BmlsubError(
                f"production request {item.input_role} artifact is not current",
                code=ErrorCode.INPUT_MISSING,
            )
        artifacts.setdefault(item.input_role, []).append(artifact)
    profile = normalize_profile(request.operation, request.output_profile, request.parameters)
    if isinstance(profile, H264HardsubProfile):
        language = artifacts["subtitle"][0].metadata.get("language")
        if language != profile.language:
            raise ValueError("subtitle language does not match hardsub output profile")
    return artifacts


def _ordered_inputs(inputs: tuple[ProductionRequestInput, ...],
                    artifacts: dict[str, list[ArtifactRecord]]):
    indexes = {role: {item.artifact_id: item for item in values} for role, values in artifacts.items()}
    return tuple((binding, indexes[binding.input_role][binding.artifact_id]) for binding in inputs)


def _update_request_status(ledger: SQLiteJobStore, request, result: StageResult) -> None:
    stages = ledger.get_run_stages(result.run_id)
    stage_id = stages[0].stage_id if stages else None
    if result.status in {StageStatus.SUCCEEDED, StageStatus.SKIPPED}:
        artifact_id = result.artifacts[0].artifact_id if result.artifacts else request.artifact_id
        ledger.transition_production_request(
            request.request_id, ProductionRequestStatus.SUCCEEDED, run_id=result.run_id,
            stage_id=stage_id, artifact_id=artifact_id,
        )
    elif result.status is StageStatus.NEEDS_REVIEW:
        ledger.transition_production_request(
            request.request_id, ProductionRequestStatus.NEEDS_REVIEW, run_id=result.run_id,
            stage_id=stage_id, error_code="review_required",
        )
    else:
        error_code = str(result.error.get("code")) if result.error else "unexpected"
        ledger.transition_production_request(
            request.request_id, ProductionRequestStatus.FAILED, run_id=result.run_id,
            stage_id=stage_id, error_code=error_code,
        )


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "\\'").replace(":", "\\:")


def _profile_version(profile: HEVC10BitProfile | H264HardsubProfile | MKVSubtitleProfile) -> str:
    if isinstance(profile, HEVC10BitProfile):
        return HEVC_PROFILE_VERSION
    return profile.profile_version


def _naming_version(profile: HEVC10BitProfile | H264HardsubProfile | MKVSubtitleProfile) -> str:
    if isinstance(profile, HEVC10BitProfile):
        return HEVC_NAMING_VERSION
    if isinstance(profile, H264HardsubProfile):
        return HARDSUB_NAMING_VERSION
    return MUX_SUBTITLE_NAMING_VERSION


def _validator_version(profile: HEVC10BitProfile | H264HardsubProfile | MKVSubtitleProfile) -> str:
    if isinstance(profile, HEVC10BitProfile):
        return HEVC_VALIDATOR_VERSION
    if isinstance(profile, H264HardsubProfile):
        return HARDSUB_VALIDATOR_VERSION
    return MUX_SUBTITLE_VALIDATOR_VERSION


def _source_media_summary(video: ArtifactRecord) -> MediaSummary:
    media = video.metadata.get("media")
    if not isinstance(media, dict):
        raise BmlsubError("source video artifact has no normalized media summary", code=ErrorCode.INPUT_MISSING)
    raw_streams = media.get("streams")
    if not isinstance(raw_streams, list):
        raise BmlsubError("source video artifact has no normalized streams", code=ErrorCode.INPUT_MISSING)
    streams = []
    for item in raw_streams:
        if not isinstance(item, dict):
            continue
        streams.append(MediaStreamSummary(
            index=int(item.get("index", len(streams))),
            codec_type=str(item.get("codec_type") or "unknown"),
            codec_name=item.get("codec_name") if isinstance(item.get("codec_name"), str) else None,
            language=item.get("language") if isinstance(item.get("language"), str) else None,
            width=item.get("width") if isinstance(item.get("width"), int) else None,
            height=item.get("height") if isinstance(item.get("height"), int) else None,
            channels=item.get("channels") if isinstance(item.get("channels"), int) else None,
            sample_rate=item.get("sample_rate") if isinstance(item.get("sample_rate"), int) else None,
        ))
    duration = media.get("duration_ms")
    return MediaSummary(
        format_name=str(media.get("format_name") or "unknown"),
        duration_ms=duration if isinstance(duration, int) else None,
        streams=tuple(streams),
    )


def _attachment_mime(path: Path) -> str:
    return {
        ".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    }.get(path.suffix.lower(), "application/octet-stream")


def _source_duration(video: ArtifactRecord) -> int | None:
    media = video.metadata.get("media")
    duration = media.get("duration_ms") if isinstance(media, dict) else None
    return duration if isinstance(duration, int) else None


def _source_video_stream(video: ArtifactRecord) -> dict[str, int | None]:
    media = video.metadata.get("media")
    streams = media.get("streams") if isinstance(media, dict) else None
    if isinstance(streams, list):
        for item in streams:
            if isinstance(item, dict) and item.get("codec_type") == "video":
                width = item.get("width")
                height = item.get("height")
                return {
                    "width": width if isinstance(width, int) else None,
                    "height": height if isinstance(height, int) else None,
                }
    raise BmlsubError(
        "source video artifact has no normalized video stream",
        code=ErrorCode.INPUT_MISSING,
    )
