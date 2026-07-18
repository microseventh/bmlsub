"""Format-level validation for extracted media tracks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..artifacts.validators import validate_ass_file, validate_nonempty_file
from ..assets.inspectors import inspect_font, inspect_subtitle
from ..execution.errors import OutputValidationError
from .models import MediaSummary
from .probe import FFprobeClient
from .tracks import TrackCandidate

if TYPE_CHECKING:
    from ..production.matroska import MKVmergeClient
    from ..production.profiles import MKVSubtitleProfile
    from ..state.models import ArtifactRecord


DURATION_TOLERANCE_MS = 5000


def validate_audio_output(path: Path, *, probe: FFprobeClient,
                          source_track: TrackCandidate, profile: str,
                          source_duration_ms: int | None) -> dict[str, Any]:
    validate_nonempty_file(path)
    summary = probe.inspect_expected(path, stream_type="audio")
    stream = summary.streams[0]
    if profile == "archive" and stream.codec_name != source_track.codec_name:
        raise OutputValidationError("archive audio codec does not match source track")
    if profile == "transcribe":
        if stream.codec_name not in {"pcm_s16le", "pcm_s16be"}:
            raise OutputValidationError("transcription audio is not signed 16-bit PCM")
        if stream.channels != 1 or stream.sample_rate != 16000:
            raise OutputValidationError("transcription audio must be mono 16 kHz")
    _validate_duration(summary, source_duration_ms)
    return summary.to_dict()


def validate_subtitle_output(path: Path, *, probe: FFprobeClient,
                             expected_codec: str,
                             source_duration_ms: int | None) -> dict[str, Any]:
    validate_nonempty_file(path)
    summary = probe.inspect_expected(path, stream_type="subtitle")
    codec = (summary.streams[0].codec_name or "").lower()
    accepted = {"ass", "ssa"} if expected_codec == "ass" else {"subrip", "srt"}
    if codec not in accepted:
        raise OutputValidationError("subtitle output codec does not match the selected profile")
    if path.suffix.lower() == ".ass":
        validate_ass_file(path)
    else:
        inspect_subtitle(path, None)
    _validate_duration(summary, source_duration_ms, required=False)
    return summary.to_dict()


def validate_video_output(path: Path, *, probe: FFprobeClient,
                          source_width: int | None, source_height: int | None,
                          source_duration_ms: int | None,
                          include_audio: bool) -> dict[str, Any]:
    validate_nonempty_file(path)
    summary = probe.inspect_media(path)
    videos = [item for item in summary.streams if item.codec_type == "video"]
    audio = [item for item in summary.streams if item.codec_type == "audio"]
    unexpected = [
        item for item in summary.streams
        if item.codec_type not in {"video", "audio"}
    ]
    if len(videos) != 1:
        raise OutputValidationError("encoded video must contain exactly one video stream")
    stream = videos[0]
    if (stream.codec_name or "").lower() not in {"hevc", "h265"}:
        raise OutputValidationError("encoded video codec is not HEVC")
    if stream.bit_depth != 10 and (stream.pixel_format or "").lower() not in {
        "p010le", "yuv420p10le",
    }:
        raise OutputValidationError("encoded video is not 10-bit")
    if stream.profile and "10" not in stream.profile.lower():
        raise OutputValidationError("encoded video profile is not Main 10")
    if source_width is not None and stream.width != source_width:
        raise OutputValidationError("encoded video width differs from the source")
    if source_height is not None and stream.height != source_height:
        raise OutputValidationError("encoded video height differs from the source")
    if include_audio and not audio:
        raise OutputValidationError("encoded video is missing expected audio streams")
    if not include_audio and audio:
        raise OutputValidationError("encoded video contains unexpected audio streams")
    if unexpected:
        raise OutputValidationError("encoded video contains unexpected non-audio streams")
    _validate_duration(summary, source_duration_ms)
    return summary.to_dict()


def validate_hardsub_video_output(path: Path, *, probe: FFprobeClient,
                                  source_width: int | None, source_height: int | None,
                                  source_duration_ms: int | None,
                                  include_audio: bool) -> dict[str, Any]:
    validate_nonempty_file(path)
    summary = probe.inspect_media(path)
    if "mp4" not in summary.format_name.lower() and "mov" not in summary.format_name.lower():
        raise OutputValidationError("hardsub video container is not MP4")
    videos = [item for item in summary.streams if item.codec_type == "video"]
    audio = [item for item in summary.streams if item.codec_type == "audio"]
    unexpected = [
        item for item in summary.streams
        if item.codec_type not in {"video", "audio"}
    ]
    if len(videos) != 1:
        raise OutputValidationError("hardsub video must contain exactly one video stream")
    stream = videos[0]
    if (stream.codec_name or "").lower() not in {"h264", "avc1"}:
        raise OutputValidationError("hardsub video codec is not H.264")
    if stream.bit_depth not in {None, 8} or (stream.pixel_format or "").lower() != "yuv420p":
        raise OutputValidationError("hardsub video must use 8-bit yuv420p")
    if source_width is not None and stream.width != source_width:
        raise OutputValidationError("hardsub video width differs from the source")
    if source_height is not None and stream.height != source_height:
        raise OutputValidationError("hardsub video height differs from the source")
    if include_audio and not audio:
        raise OutputValidationError("hardsub video is missing expected audio streams")
    if not include_audio and audio:
        raise OutputValidationError("hardsub video contains unexpected audio streams")
    if unexpected:
        raise OutputValidationError("hardsub video contains unexpected non-audio streams")
    _validate_duration(summary, source_duration_ms)
    return summary.to_dict()


def validate_muxed_video_output(path: Path, *, probe: FFprobeClient,
                                mkvmerge: "MKVmergeClient",
                                source_summary: MediaSummary,
                                subtitles: tuple["ArtifactRecord", ...],
                                fonts: tuple["ArtifactRecord", ...],
                                chapter: "ArtifactRecord | None",
                                attachments: tuple["ArtifactRecord", ...],
                                profile: "MKVSubtitleProfile") -> dict[str, Any]:
    validate_nonempty_file(path)
    summary = probe.inspect_media(path)
    if "matroska" not in summary.format_name.lower():
        raise OutputValidationError("muxed video container is not Matroska")
    identified = mkvmerge.identify(path)
    source_video = [item for item in source_summary.streams if item.codec_type == "video"]
    source_audio = [item for item in source_summary.streams if item.codec_type == "audio"]
    videos = [item for item in summary.streams if item.codec_type == "video"]
    audio = [item for item in summary.streams if item.codec_type == "audio"]
    subtitle_streams = [item for item in identified.tracks if item.track_type == "subtitles"]
    if len(videos) != len(source_video) or not videos:
        raise OutputValidationError("muxed video does not preserve the selected source video tracks")
    if [item.codec_name for item in videos] != [item.codec_name for item in source_video]:
        raise OutputValidationError("muxed video codecs differ from the source")
    expected_audio = source_audio if profile.include_audio else []
    if len(audio) != len(expected_audio):
        raise OutputValidationError("muxed video audio stream count does not match the profile")
    if [item.codec_name for item in audio] != [item.codec_name for item in expected_audio]:
        raise OutputValidationError("muxed video audio codecs differ from the source")
    if len(subtitle_streams) != len(subtitles):
        raise OutputValidationError("muxed video subtitle count does not match selected inputs")
    for ordinal, (track, artifact) in enumerate(zip(subtitle_streams, subtitles)):
        expected_language = artifact.metadata.get("language")
        if (not isinstance(expected_language, str) or not isinstance(track.language, str) or
                track.language.lower() != expected_language.lower()):
            raise OutputValidationError("muxed subtitle language does not match selected input")
        expected_codec = "S_TEXT/ASS" if artifact.path.suffix.lower() == ".ass" else "S_TEXT/UTF8"
        if track.codec_id != expected_codec:
            raise OutputValidationError("muxed subtitle codec does not match selected input")
        if track.is_default != (profile.default_subtitle_ordinal == ordinal):
            raise OutputValidationError("muxed subtitle default flag does not match the profile")
        if track.is_forced != (ordinal in profile.forced_subtitle_ordinals):
            raise OutputValidationError("muxed subtitle forced flag does not match the profile")
    expected_attachments = (*fonts, *attachments)
    if len(identified.attachments) != len(expected_attachments):
        raise OutputValidationError("muxed attachment count does not match selected inputs")
    actual_attachments = sorted(
        (item.file_name, item.content_type) for item in identified.attachments
    )
    expected_attachment_values = sorted(
        (
            item.path.name,
            str(item.metadata.get("mime_type") or _expected_attachment_mime(item.path)),
        )
        for item in expected_attachments
    )
    if actual_attachments != expected_attachment_values:
        raise OutputValidationError("muxed attachment names or MIME types do not match selected inputs")
    if bool(identified.chapter_count) != bool(chapter):
        raise OutputValidationError("muxed chapter state does not match the selected input")
    expected_duration = source_summary.duration_ms
    subtitle_ends = [
        item.metadata.get("last_time_ms") for item in subtitles
        if isinstance(item.metadata.get("last_time_ms"), int)
    ]
    if subtitle_ends:
        expected_duration = max([value for value in (expected_duration, *subtitle_ends) if value is not None])
    _validate_duration(summary, expected_duration)
    return {
        "ffprobe": summary.to_dict(),
        "matroska": {
            "track_count": len(identified.tracks),
            "subtitle_count": len(subtitle_streams),
            "attachment_count": len(identified.attachments),
            "chapter_count": identified.chapter_count,
        },
    }


def validate_attachment_output(path: Path, *, is_font: bool) -> dict[str, Any]:
    validate_nonempty_file(path)
    if is_font:
        artifact_type, metadata = inspect_font(path)
        return {"artifact_type": artifact_type, **metadata}
    return {"artifact_type": "source.attachment", "size": path.stat().st_size}


def _expected_attachment_mime(path: Path) -> str:
    return {
        ".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    }.get(path.suffix.lower(), "application/octet-stream")


def _validate_duration(summary: MediaSummary, source_duration_ms: int | None,
                       *, required: bool = True) -> None:
    if source_duration_ms is None or summary.duration_ms is None:
        if required and summary.duration_ms is None:
            raise OutputValidationError("media output duration is unavailable")
        return
    if abs(summary.duration_ms - source_duration_ms) > DURATION_TOLERANCE_MS:
        raise OutputValidationError("media output duration differs from the source beyond tolerance")
