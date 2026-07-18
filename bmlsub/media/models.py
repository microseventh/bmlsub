"""Normalized media probe models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class VideoPurpose(str, Enum):
    SOURCE = "source"
    INSPECT = "inspect"
    EXTRACT = "extract"
    TRANSCRIBE_SOURCE = "transcribe_source"
    ENCODE_SOURCE = "encode_source"
    HARDSUB_SOURCE = "hardsub_source"
    PACKAGE_SOURCE = "package_source"
    REFERENCE = "reference"


@dataclass(frozen=True)
class MediaStreamSummary:
    index: int
    codec_type: str
    codec_name: str | None = None
    language: str | None = None
    title: str | None = None
    is_default: bool = False
    is_forced: bool = False
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    sample_rate: int | None = None
    profile: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    filename: str | None = None
    mime_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        values = {
            "index": self.index, "codec_type": self.codec_type,
            "codec_name": self.codec_name, "language": self.language,
            "title": self.title, "is_default": self.is_default,
            "is_forced": self.is_forced, "width": self.width,
            "height": self.height, "channels": self.channels,
            "sample_rate": self.sample_rate, "profile": self.profile,
            "pixel_format": self.pixel_format, "bit_depth": self.bit_depth,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }
        return {
            key: value for key, value in values.items()
            if value is not None and not (isinstance(value, bool) and value is False)
        }


@dataclass(frozen=True)
class MediaSummary:
    format_name: str
    duration_ms: int | None
    streams: tuple[MediaStreamSummary, ...]

    @property
    def has_video(self) -> bool:
        return any(item.codec_type == "video" for item in self.streams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_name": self.format_name, "duration_ms": self.duration_ms,
            "streams": [item.to_dict() for item in self.streams],
            "stream_counts": {
                kind: sum(item.codec_type == kind for item in self.streams)
                for kind in ("video", "audio", "subtitle", "attachment")
            },
        }

    @classmethod
    def from_probe(cls, data: Mapping[str, Any], *, max_streams: int = 128) -> "MediaSummary":
        format_data = data.get("format") if isinstance(data.get("format"), Mapping) else {}
        format_name = str(format_data.get("format_name") or "unknown")[:256]
        duration_ms = _duration_ms(format_data.get("duration"))
        raw_streams = data.get("streams")
        if not isinstance(raw_streams, list):
            raw_streams = []
        streams: list[MediaStreamSummary] = []
        for raw in raw_streams[:max_streams]:
            if not isinstance(raw, Mapping):
                continue
            tags = raw.get("tags") if isinstance(raw.get("tags"), Mapping) else {}
            disposition = raw.get("disposition") if isinstance(raw.get("disposition"), Mapping) else {}
            streams.append(MediaStreamSummary(
                index=_integer(raw.get("index"), default=len(streams)),
                codec_type=str(raw.get("codec_type") or "unknown")[:32],
                codec_name=_text(raw.get("codec_name"), 64),
                language=_text(tags.get("language"), 32),
                title=_text(tags.get("title"), 256),
                is_default=bool(_integer(disposition.get("default"), default=0)),
                is_forced=bool(_integer(disposition.get("forced"), default=0)),
                width=_optional_integer(raw.get("width")),
                height=_optional_integer(raw.get("height")),
                channels=_optional_integer(raw.get("channels")),
                sample_rate=_optional_integer(raw.get("sample_rate")),
                profile=_text(raw.get("profile"), 64),
                pixel_format=_text(raw.get("pix_fmt"), 64),
                bit_depth=_bit_depth(raw),
                filename=_text(tags.get("filename"), 512),
                mime_type=_text(tags.get("mimetype"), 128),
            ))
        return cls(format_name=format_name, duration_ms=duration_ms, streams=tuple(streams))


def _text(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value is not None else None


def _integer(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bit_depth(value: Mapping[str, Any]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        depth = _optional_integer(value.get(key))
        if depth is not None and depth > 0:
            return depth
    pixel_format = str(value.get("pix_fmt") or "").lower()
    if "10" in pixel_format or pixel_format.startswith("p010"):
        return 10
    if pixel_format:
        return 8
    return None


def _duration_ms(value: Any) -> int | None:
    try:
        return max(0, round(float(value) * 1000))
    except (TypeError, ValueError):
        return None
