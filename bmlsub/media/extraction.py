"""Reliable extraction of registered video audio and text-subtitle tracks."""

from __future__ import annotations

from ..version import __version__

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactBatchWriter, ArtifactWriteSpec
from ..execution import (
    PROCESS_RUNNER_VERSION, BmlsubError, ErrorCode, ProcessRunner, ReviewRequiredError,
)
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding, StageResult
from ..state.sqlite_store import SQLiteJobStore
from .probe import FFprobeClient
from .tracks import (
    ATTACHMENT_NAMING_VERSION, ATTACHMENT_VALIDATOR_VERSION,
    MEDIA_VALIDATOR_VERSION, OUTPUT_NAMING_VERSION, TRACK_SELECTION_VERSION,
    AttachmentCandidate, AudioOutputMode, TrackCandidate, TrackKind,
    attachment_candidates_from_artifact, attachment_output_directory,
    attachment_output_path, audio_output_path, candidates_from_artifact,
    default_purpose, output_directory, select_track, subtitle_output_path,
)
from .validators import validate_attachment_output, validate_audio_output, validate_subtitle_output
from .video import get_current_artifact, resolve_video


AUDIO_STAGE = "media.extract_audio"
SUBTITLE_STAGE = "media.extract_subtitle"
ATTACHMENT_STAGE = "media.extract_attachments"
AUDIO_PROFILE_VERSION = "audio-profile-v1"
SUBTITLE_PROFILE_VERSION = "subtitle-profile-v1"
ATTACHMENT_PROFILE_VERSION = "attachment-profile-v1"


def list_media_tracks(*, workspace: Path | str, episode_id: str,
                      video_artifact_id: str | None = None,
                      purpose: str | None = None,
                      kind: TrackKind | None = None,
                      store: SQLiteJobStore | None = None,
                      state_dir: Path | str | None = None) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    video = _resolve_video(ledger, episode_id, video_artifact_id, purpose, kind)
    tracks = candidates_from_artifact(video, kind)
    return {
        "status": "succeeded", "needs_review": False,
        "video_artifact_id": video.artifact_id,
        "tracks": [item.to_dict() for item in tracks],
    }


def run_audio_extraction(*, workspace: Path | str, episode_id: str,
                         video_artifact_id: str | None = None,
                         purpose: str | None = None,
                         stream_index: int | None = None,
                         language: str | None = None,
                         mode: AudioOutputMode = AudioOutputMode.BOTH,
                         output_dir: Path | str | None = None,
                         ffmpeg: Path | str = "ffmpeg",
                         ffprobe: Path | str = "ffprobe",
                         process_timeout: float = 600.0,
                         probe_timeout: float = 30.0,
                         runner: ProcessRunner | None = None,
                         probe: FFprobeClient | None = None,
                         store: SQLiteJobStore | None = None,
                         state_dir: Path | str | None = None,
                         force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    video = _resolve_video(ledger, episode_id, video_artifact_id, purpose,
                           TrackKind.AUDIO, mode)
    track_candidates = candidates_from_artifact(video, TrackKind.AUDIO)
    try:
        track = select_track(track_candidates, kind=TrackKind.AUDIO,
                             stream_index=stream_index, language=language)
    except ReviewRequiredError as exc:
        return _selection_review_result(
            ledger, root, episode_id, AUDIO_STAGE, video,
            {"mode": mode.value, "stream_index": stream_index, "language": language}, exc,
        )
    directory = output_directory(root, episode_id, output_dir)
    modes = ((AudioOutputMode.ARCHIVE, AudioOutputMode.TRANSCRIBE)
             if mode is AudioOutputMode.BOTH else (mode,))
    targets = tuple(audio_output_path(directory, episode_id, track, item) for item in modes)
    process = runner or ProcessRunner(timeout=process_timeout)
    inspector = probe or FFprobeClient(ffprobe, timeout=probe_timeout)
    ffmpeg_version = process.version(ffmpeg)
    ffprobe_version = inspector.version()
    source_duration = _source_duration(video)
    input_fp = hash_json({"artifact_id": video.artifact_id,
                          "fingerprint": video.source_fingerprint})
    parameter_fp = fingerprint_parameters({
        "stream_index": track.index, "language": track.language,
        "source_codec": track.codec_name, "mode": mode.value,
        "targets": [str(path.relative_to(root)) for path in targets],
        "selection_version": TRACK_SELECTION_VERSION,
        "naming_version": OUTPUT_NAMING_VERSION,
        "profile_version": AUDIO_PROFILE_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version,
        "process_runner": PROCESS_RUNNER_VERSION, "validator": MEDIA_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        specs = []
        media_metadata: dict[str, dict[str, Any]] = {}
        for item, target in zip(modes, targets):
            def validator(path: Path, selected=item) -> None:
                media_metadata[selected.value] = validate_audio_output(
                    path, probe=inspector, source_track=track,
                    profile=selected.value, source_duration_ms=source_duration,
                )
            specs.append(ArtifactWriteSpec(
                target=target,
                artifact_type=("generated.audio.archive" if item is AudioOutputMode.ARCHIVE
                               else "generated.audio.transcribe"),
                validator=validator,
                metadata=_artifact_metadata(video.artifact_id, track, item.value),
            ))
        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
        )

        def produce(paths: tuple[Path, ...]) -> None:
            for item, candidate in zip(modes, paths):
                argv = [str(ffmpeg), "-nostdin", "-y", "-v", "error", "-i", str(video.path),
                        "-map", f"0:{track.index}", "-vn", "-sn", "-dn"]
                if item is AudioOutputMode.ARCHIVE:
                    argv.extend(["-c:a", "copy", "-f", "matroska", str(candidate)])
                else:
                    argv.extend(["-c:a", "pcm_s16le", "-ac", "1", "-ar", "16000",
                                 "-f", "wav", str(candidate)])
                process.run(argv, timeout=process_timeout)

        results = writer.write(tuple(specs), produce)
        artifacts = []
        diagnostics = []
        for item, result in zip(modes, results):
            metadata = dict(result.artifact.metadata)
            metadata["media"] = media_metadata[item.value]
            artifacts.append(_replace_metadata(result.artifact, metadata))
            if result.backup_path:
                diagnostics.append(Diagnostic(
                    code="artifact_backup_created", message="existing media output was backed up",
                    context={"path": str(result.backup_path)},
                ))
        diagnostics.append(Diagnostic(
            code="audio_track_extracted", message="selected audio track was extracted",
            context={"stream_index": track.index, "mode": mode.value},
        ))
        return StageOutcome(artifacts=tuple(artifacts), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="media.extract-audio", stage_name=AUDIO_STAGE,
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=(StageInputBinding(video.artifact_id, "video", 0),),
        force=force,
    )


def run_subtitle_extraction(*, workspace: Path | str, episode_id: str,
                            video_artifact_id: str | None = None,
                            purpose: str | None = None,
                            stream_index: int | None = None,
                            language: str | None = None,
                            output_dir: Path | str | None = None,
                            ffmpeg: Path | str = "ffmpeg",
                            ffprobe: Path | str = "ffprobe",
                            process_timeout: float = 300.0,
                            probe_timeout: float = 30.0,
                            runner: ProcessRunner | None = None,
                            probe: FFprobeClient | None = None,
                            store: SQLiteJobStore | None = None,
                            state_dir: Path | str | None = None,
                            force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    video = _resolve_video(ledger, episode_id, video_artifact_id, purpose, TrackKind.SUBTITLE)
    track_candidates = candidates_from_artifact(video, TrackKind.SUBTITLE)
    try:
        track = select_track(track_candidates, kind=TrackKind.SUBTITLE,
                             stream_index=stream_index, language=language)
        directory = output_directory(root, episode_id, output_dir)
        target, extension, expected_codec = subtitle_output_path(directory, episode_id, track)
    except ReviewRequiredError as exc:
        return _selection_review_result(
            ledger, root, episode_id, SUBTITLE_STAGE, video,
            {"stream_index": stream_index, "language": language}, exc,
        )
    process = runner or ProcessRunner(timeout=process_timeout)
    inspector = probe or FFprobeClient(ffprobe, timeout=probe_timeout)
    ffmpeg_version = process.version(ffmpeg)
    ffprobe_version = inspector.version()
    source_duration = _source_duration(video)
    input_fp = hash_json({"artifact_id": video.artifact_id,
                          "fingerprint": video.source_fingerprint})
    parameter_fp = fingerprint_parameters({
        "stream_index": track.index, "language": track.language,
        "source_codec": track.codec_name, "extension": extension,
        "target": str(target.relative_to(root)),
        "selection_version": TRACK_SELECTION_VERSION,
        "naming_version": OUTPUT_NAMING_VERSION,
        "profile_version": SUBTITLE_PROFILE_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version,
        "process_runner": PROCESS_RUNNER_VERSION, "validator": MEDIA_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        media_metadata: dict[str, Any] = {}

        def validator(path: Path) -> None:
            media_metadata.update(validate_subtitle_output(
                path, probe=inspector, expected_codec=expected_codec,
                source_duration_ms=source_duration,
            ))

        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
        )
        spec = ArtifactWriteSpec(
            target=target, artifact_type=f"generated.subtitle.{extension}",
            validator=validator,
            metadata=_artifact_metadata(video.artifact_id, track, "subtitle"),
        )

        def produce(paths: tuple[Path, ...]) -> None:
            process.run([
                str(ffmpeg), "-nostdin", "-y", "-v", "error", "-i", str(video.path),
                "-map", f"0:{track.index}", "-vn", "-an", "-dn", "-c:s", "copy",
                "-f", extension, str(paths[0]),
            ], timeout=process_timeout)

        result = writer.write((spec,), produce)[0]
        metadata = dict(result.artifact.metadata)
        metadata["media"] = media_metadata
        artifact = _replace_metadata(result.artifact, metadata)
        diagnostics = []
        if result.backup_path:
            diagnostics.append(Diagnostic(
                code="artifact_backup_created", message="existing subtitle output was backed up",
                context={"path": str(result.backup_path)},
            ))
        diagnostics.append(Diagnostic(
            code="subtitle_track_extracted", message="selected subtitle track was extracted",
            context={"stream_index": track.index, "format": extension},
        ))
        return StageOutcome(artifacts=(artifact,), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="media.extract-subtitle", stage_name=SUBTITLE_STAGE,
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter, inputs=(StageInputBinding(video.artifact_id, "video", 0),),
        force=force,
    )


def run_attachment_extraction(*, workspace: Path | str, episode_id: str,
                              video_artifact_id: str | None = None,
                              purpose: str | None = None,
                              output_dir: Path | str | None = None,
                              ffmpeg: Path | str = "ffmpeg",
                              ffprobe: Path | str = "ffprobe",
                              process_timeout: float = 300.0,
                              probe_timeout: float = 30.0,
                              runner: ProcessRunner | None = None,
                              probe: FFprobeClient | None = None,
                              store: SQLiteJobStore | None = None,
                              state_dir: Path | str | None = None,
                              force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    video = _resolve_video(ledger, episode_id, video_artifact_id, purpose, TrackKind.SUBTITLE)
    attachments = attachment_candidates_from_artifact(video)
    if not attachments:
        return _attachment_review_result(ledger, root, episode_id, video)
    directory = attachment_output_directory(root, episode_id, output_dir)
    targets = tuple(attachment_output_path(directory, item) for item in attachments)
    if len({str(path).casefold() for path in targets}) != len(targets):
        raise ValueError("attachment output paths must be unique")
    process = runner or ProcessRunner(timeout=process_timeout)
    inspector = probe or FFprobeClient(ffprobe, timeout=probe_timeout)
    ffmpeg_version = process.version(ffmpeg)
    ffprobe_version = inspector.version()
    input_fp = hash_json({"artifact_id": video.artifact_id,
                          "fingerprint": video.source_fingerprint})
    plan = [
        {
            "stream_index": item.index, "codec_name": item.codec_name,
            "filename": item.filename, "mime_type": item.mime_type,
            "is_font": item.is_font, "target": str(target.relative_to(root)),
        }
        for item, target in zip(attachments, targets)
    ]
    parameter_fp = fingerprint_parameters({
        "attachments": plan, "naming_version": ATTACHMENT_NAMING_VERSION,
        "profile_version": ATTACHMENT_PROFILE_VERSION,
        "validator_version": ATTACHMENT_VALIDATOR_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version,
        "process_runner": PROCESS_RUNNER_VERSION,
        "validator": ATTACHMENT_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        validation_metadata: dict[int, dict[str, Any]] = {}
        specs = []
        for attachment, target in zip(attachments, targets):
            def validator(path: Path, selected=attachment) -> None:
                validation_metadata[selected.index] = validate_attachment_output(
                    path, is_font=selected.is_font,
                )
            specs.append(ArtifactWriteSpec(
                target=target,
                artifact_type=("generated.font" if attachment.is_font
                               else "generated.attachment"),
                validator=validator,
                metadata=_attachment_metadata(video.artifact_id, attachment, target.name),
            ))
        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
        )

        def produce(paths: tuple[Path, ...]) -> None:
            for attachment, candidate in zip(attachments, paths):
                process.run([
                    str(ffmpeg), "-nostdin", "-y", "-v", "error",
                    f"-dump_attachment:{attachment.index}", str(candidate),
                    "-i", str(video.path), "-map", "0:v:0", "-frames:v", "0",
                    "-f", "null", "-",
                ], timeout=process_timeout)

        results = writer.write(tuple(specs), produce)
        artifacts = []
        diagnostics = []
        for attachment, result in zip(attachments, results):
            metadata = dict(result.artifact.metadata)
            metadata["validation"] = validation_metadata[attachment.index]
            artifacts.append(_replace_metadata(result.artifact, metadata))
            if result.backup_path:
                diagnostics.append(Diagnostic(
                    code="artifact_backup_created",
                    message="existing attachment output was backed up",
                    context={"path": str(result.backup_path)},
                ))
        diagnostics.append(Diagnostic(
            code="attachments_extracted",
            message="all embedded attachments were extracted",
            context={
                "attachment_count": len(attachments),
                "font_count": sum(item.is_font for item in attachments),
                "other_count": sum(not item.is_font for item in attachments),
            },
        ))
        return StageOutcome(artifacts=tuple(artifacts), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="media.extract-attachments",
        stage_name=ATTACHMENT_STAGE, episode_id=episode_id,
        input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(video.artifact_id, "video", 0),), force=force,
    )


def _attachment_review_result(store: SQLiteJobStore, workspace: Path,
                              episode_id: str, video) -> StageResult:
    input_fp = hash_json({"artifact_id": video.artifact_id,
                          "fingerprint": video.source_fingerprint})
    parameter_fp = fingerprint_parameters({
        "attachments": [], "profile_version": ATTACHMENT_PROFILE_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__, "validator": ATTACHMENT_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        raise ReviewRequiredError("video contains no embedded attachments")

    return StageRunner(store).run(
        workspace=workspace, command_name="media.extract-attachments",
        stage_name=ATTACHMENT_STAGE, episode_id=episode_id,
        input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(video.artifact_id, "video", 0),),
    )


def _selection_review_result(store: SQLiteJobStore, workspace: Path, episode_id: str,
                             stage_name: str, video, parameters: dict[str, Any],
                             error: ReviewRequiredError) -> StageResult:
    input_fp = hash_json({"artifact_id": video.artifact_id,
                          "fingerprint": video.source_fingerprint})
    parameter_fp = fingerprint_parameters({
        **parameters, "selection_version": TRACK_SELECTION_VERSION,
    })
    tool_fp = fingerprint_tools({"bmlsub": __version__, "selection": TRACK_SELECTION_VERSION})

    def adapter(context: StageContext) -> StageOutcome:
        raise error

    return StageRunner(store).run(
        workspace=workspace, command_name=stage_name.replace("_", "-"),
        stage_name=stage_name, episode_id=episode_id,
        input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=(StageInputBinding(video.artifact_id, "video", 0),),
    )


def _resolve_video(store: SQLiteJobStore, episode_id: str,
                   artifact_id: str | None, purpose: str | None,
                   kind: TrackKind | None,
                   mode: AudioOutputMode | None = None):
    if artifact_id:
        artifact = get_current_artifact(store, artifact_id)
        if artifact is None or artifact.episode_id != episode_id or artifact.artifact_type not in {
            "source.video", "reference.video",
        } or artifact.validation_status.value != "valid":
            raise BmlsubError("video artifact is not a current input for this episode",
                              code=ErrorCode.INPUT_MISSING)
        return artifact
    selected_purpose = purpose or default_purpose(kind or TrackKind.AUDIO, mode).value
    from .models import VideoPurpose
    artifact, ambiguous = resolve_video(store, episode_id, VideoPurpose(selected_purpose))
    if ambiguous:
        from ..execution.errors import ReviewRequiredError
        raise ReviewRequiredError("video purpose resolves to multiple candidates")
    if artifact is None:
        raise BmlsubError("no current video is registered for the requested purpose",
                          code=ErrorCode.INPUT_MISSING)
    return artifact


def _source_duration(video) -> int | None:
    media = video.metadata.get("media")
    duration = media.get("duration_ms") if isinstance(media, dict) else None
    return duration if isinstance(duration, int) else None


def _attachment_metadata(video_id: str, attachment: AttachmentCandidate,
                         output_filename: str) -> dict[str, Any]:
    return {
        "source_video_artifact_id": video_id,
        "source_stream_index": attachment.index,
        "source_codec": attachment.codec_name,
        "original_filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "output_filename": output_filename,
        "is_font": attachment.is_font,
        "profile_version": ATTACHMENT_PROFILE_VERSION,
        "naming_version": ATTACHMENT_NAMING_VERSION,
        "validator_version": ATTACHMENT_VALIDATOR_VERSION,
    }


def _artifact_metadata(video_id: str, track: TrackCandidate, profile: str) -> dict[str, Any]:
    return {
        "source_video_artifact_id": video_id,
        "source_stream_index": track.index, "source_codec": track.codec_name,
        "language": track.language, "title": track.title,
        "profile": profile, "selection_version": TRACK_SELECTION_VERSION,
        "naming_version": OUTPUT_NAMING_VERSION,
        "validator_version": MEDIA_VALIDATOR_VERSION,
    }


def _replace_metadata(artifact, metadata):
    from dataclasses import replace
    return replace(artifact, metadata=metadata)
