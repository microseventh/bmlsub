"""Explicit media-track candidates and conservative selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any

from ..execution.errors import ReviewRequiredError
from ..state.models import ArtifactRecord
from .models import MediaStreamSummary, VideoPurpose


TRACK_SELECTION_VERSION = "track-selection-v1"
OUTPUT_NAMING_VERSION = "track-naming-v1"
MEDIA_VALIDATOR_VERSION = "media-validator-v1"
ATTACHMENT_NAMING_VERSION = "attachment-naming-v1"
ATTACHMENT_VALIDATOR_VERSION = "attachment-validator-v1"


class TrackKind(str, Enum):
    AUDIO = "audio"
    SUBTITLE = "subtitle"


class AudioOutputMode(str, Enum):
    ARCHIVE = "archive"
    TRANSCRIBE = "transcribe"
    BOTH = "both"


@dataclass(frozen=True)
class AttachmentCandidate:
    index: int
    codec_name: str | None = None
    filename: str | None = None
    mime_type: str | None = None

    @property
    def is_font(self) -> bool:
        suffix = Path(self.filename or "").suffix.lower()
        mime = (self.mime_type or "").lower()
        codec = (self.codec_name or "").lower()
        return suffix in {".ttf", ".otf", ".ttc"} or codec in {"ttf", "otf", "ttc"} or "font" in mime

    def to_dict(self) -> dict[str, Any]:
        values = {
            "index": self.index, "codec_name": self.codec_name,
            "filename": self.filename, "mime_type": self.mime_type,
            "is_font": self.is_font,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class TrackCandidate:
    index: int
    kind: TrackKind
    codec_name: str | None = None
    language: str = "und"
    title: str | None = None
    is_default: bool = False
    is_forced: bool = False
    channels: int | None = None
    sample_rate: int | None = None

    @classmethod
    def from_stream(cls, stream: MediaStreamSummary) -> "TrackCandidate":
        return cls(
            index=stream.index, kind=TrackKind(stream.codec_type),
            codec_name=stream.codec_name,
            language=normalize_language(stream.language), title=stream.title,
            is_default=stream.is_default, is_forced=stream.is_forced,
            channels=stream.channels, sample_rate=stream.sample_rate,
        )

    def to_dict(self) -> dict[str, Any]:
        values = {
            "index": self.index, "kind": self.kind.value,
            "codec_name": self.codec_name, "language": self.language,
            "title": self.title, "is_default": self.is_default,
            "is_forced": self.is_forced, "channels": self.channels,
            "sample_rate": self.sample_rate,
        }
        return {key: value for key, value in values.items() if value is not None}


def attachment_candidates_from_artifact(artifact: ArtifactRecord) -> tuple[AttachmentCandidate, ...]:
    media = artifact.metadata.get("media")
    raw_streams = media.get("streams", []) if isinstance(media, dict) else []
    candidates = []
    for raw in raw_streams:
        if not isinstance(raw, dict) or raw.get("codec_type") != "attachment":
            continue
        candidates.append(AttachmentCandidate(
            index=int(raw.get("index", 0)),
            codec_name=_text(raw.get("codec_name")),
            filename=_text(raw.get("filename"), limit=512),
            mime_type=_text(raw.get("mime_type")),
        ))
    return tuple(sorted(candidates, key=lambda item: item.index))


def attachment_output_directory(workspace: Path, episode_id: str,
                                requested: Path | str | None) -> Path:
    target = (Path(requested).expanduser().resolve() if requested is not None
              else workspace / "outputs" / safe_component(episode_id) / "attachments")
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("attachment output directory must be inside workspace") from exc
    return target


def attachment_output_path(directory: Path, candidate: AttachmentCandidate) -> Path:
    filename = Path((candidate.filename or "").replace("\\", "/")).name
    stem = Path(filename).stem if filename else "attachment"
    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix not in {".ttf", ".otf", ".ttc"} and not re.fullmatch(r"\.[0-9A-Za-z]{1,16}", suffix):
        suffix = _attachment_suffix(candidate)
    return directory / f"s{candidate.index}.{safe_component(stem)}{suffix}"


def _attachment_suffix(candidate: AttachmentCandidate) -> str:
    codec = (candidate.codec_name or "").lower()
    if codec in {"ttf", "otf", "ttc"}:
        return f".{codec}"
    mime_suffixes = {
        "application/x-truetype-font": ".ttf",
        "font/ttf": ".ttf", "font/otf": ".otf", "font/collection": ".ttc",
        "image/jpeg": ".jpg", "image/png": ".png",
    }
    return mime_suffixes.get((candidate.mime_type or "").lower(), ".bin")


def candidates_from_artifact(artifact: ArtifactRecord,
                             kind: TrackKind | None = None) -> tuple[TrackCandidate, ...]:
    media = artifact.metadata.get("media")
    raw_streams = media.get("streams", []) if isinstance(media, dict) else []
    candidates = []
    for raw in raw_streams:
        if not isinstance(raw, dict) or raw.get("codec_type") not in {
            TrackKind.AUDIO.value, TrackKind.SUBTITLE.value,
        }:
            continue
        candidate = TrackCandidate(
            index=int(raw.get("index", 0)), kind=TrackKind(str(raw["codec_type"])),
            codec_name=_text(raw.get("codec_name")),
            language=normalize_language(raw.get("language")),
            title=_text(raw.get("title")), is_default=bool(raw.get("is_default", False)),
            is_forced=bool(raw.get("is_forced", False)),
            channels=_integer(raw.get("channels")), sample_rate=_integer(raw.get("sample_rate")),
        )
        if kind is None or candidate.kind is kind:
            candidates.append(candidate)
    return tuple(sorted(candidates, key=lambda item: item.index))


def select_track(candidates: tuple[TrackCandidate, ...], *, kind: TrackKind,
                 stream_index: int | None = None,
                 language: str | None = None) -> TrackCandidate:
    matching = tuple(item for item in candidates if item.kind is kind)
    if stream_index is not None:
        selected = [item for item in matching if item.index == stream_index]
        if len(selected) != 1:
            raise ReviewRequiredError(
                "requested stream index is not available for this track type",
                details={"stream_index": stream_index, "kind": kind.value},
            )
        return selected[0]
    if language:
        normalized = normalize_language(language)
        matching = tuple(item for item in matching if languages_match(item.language, normalized))
    if len(matching) == 1:
        return matching[0]
    defaults = tuple(item for item in matching if item.is_default)
    if len(defaults) == 1:
        return defaults[0]
    reason = "no matching media track" if not matching else "media track selection is ambiguous"
    raise ReviewRequiredError(
        reason,
        details={"kind": kind.value, "candidates": [item.to_dict() for item in matching[:32]]},
    )


def output_directory(workspace: Path, episode_id: str, requested: Path | str | None) -> Path:
    target = (Path(requested).expanduser().resolve() if requested is not None
              else workspace / "outputs" / safe_component(episode_id) / "tracks")
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("media output directory must be inside workspace") from exc
    return target


def audio_output_path(directory: Path, episode_id: str, track: TrackCandidate,
                      mode: AudioOutputMode) -> Path:
    suffix = ".mka" if mode is AudioOutputMode.ARCHIVE else ".wav"
    return directory / (
        f"{safe_component(episode_id)}.audio.s{track.index}."
        f"{safe_component(track.language)}.{mode.value}{suffix}"
    )


def subtitle_output_path(directory: Path, episode_id: str,
                         track: TrackCandidate) -> tuple[Path, str, str]:
    codec = (track.codec_name or "").lower()
    if codec in {"ass", "ssa"}:
        return directory / (
            f"{safe_component(episode_id)}.subtitle.s{track.index}."
            f"{safe_component(track.language)}.ass"
        ), "ass", "ass"
    if codec in {"subrip", "srt"}:
        return directory / (
            f"{safe_component(episode_id)}.subtitle.s{track.index}."
            f"{safe_component(track.language)}.srt"
        ), "srt", "subrip"
    raise ReviewRequiredError(
        "subtitle codec is not supported for text extraction",
        details={"stream_index": track.index, "codec_name": codec or "unknown"},
    )


def normalize_language(value: object) -> str:
    text = str(value or "und").strip().lower()
    return text[:32] or "und"


def languages_match(left: str, right: str) -> bool:
    aliases = {
        "zh": {"zh", "chi", "zho", "chs", "cht", "zh-hans", "zh-hant"},
        "ja": {"ja", "jpn", "jp"},
        "en": {"en", "eng"},
    }
    for values in aliases.values():
        if left in values and right in values:
            return True
    return left == right


def safe_component(value: object) -> str:
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value).strip()).strip("-.")
    return (normalized or "und")[:64]


def default_purpose(kind: TrackKind, mode: AudioOutputMode | None = None) -> VideoPurpose:
    if kind is TrackKind.AUDIO and mode is AudioOutputMode.TRANSCRIBE:
        return VideoPurpose.TRANSCRIBE_SOURCE
    return VideoPurpose.EXTRACT


def _text(value: object, *, limit: int = 256) -> str | None:
    return str(value)[:limit] if value is not None else None


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
