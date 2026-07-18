"""Versioned production output profiles."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping

from .models import ProductionOperation


HEVC_10BIT_PROFILE = "hevc-10bit"
H264_CHS_PROFILE = "h264-chs"
H264_CHT_PROFILE = "h264-cht"
MKV_SUBTITLE_PROFILE = "mkv-subtitle"
HEVC_PROFILE_VERSION = "hevc-10bit-v1"
HARDSUB_PROFILE_VERSION_V1 = "h264-hardsub-v1"
HARDSUB_PROFILE_VERSION = "h264-hardsub-v2"
MUX_SUBTITLE_PROFILE_VERSION = "mkv-subtitle-v1"
HARDSUB_ARGV_VERSION = "h264-hardsub-argv-v2"
MUX_SUBTITLE_ARGV_VERSION = "mkv-subtitle-argv-v1"
HEVC_NAMING_VERSION = "production-video-naming-v1"
HARDSUB_NAMING_VERSION = "production-hardsub-naming-v1"
MUX_SUBTITLE_NAMING_VERSION = "production-mux-subtitle-naming-v1"
HEVC_VALIDATOR_VERSION = "production-video-validator-v1"
HARDSUB_VALIDATOR_VERSION = "production-hardsub-validator-v1"
MUX_SUBTITLE_VALIDATOR_VERSION = "production-mux-subtitle-validator-v1"

_HARDSUB_BASE_FIELDS = {
    "video_codec", "preset", "crf", "tune", "pixel_format", "audio_codec",
    "audio_bitrate", "include_audio", "strip_metadata",
}
_HARDSUB_ANIMATION_FIELDS = {
    "refs", "bframes", "qcomp", "rc_lookahead", "aq_mode", "aq_strength",
    "deblock", "me_range", "mbtree",
}


@dataclass(frozen=True)
class HEVC10BitProfile:
    video_codec: str = "hevc_videotoolbox"
    pixel_format: str = "p010le"
    quality: int = 60
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    include_audio: bool = True
    strip_metadata: bool = True

    def __post_init__(self) -> None:
        if self.video_codec not in {"hevc_videotoolbox", "libx265"}:
            raise ValueError("unsupported HEVC video codec")
        if self.pixel_format not in {"p010le", "yuv420p10le"}:
            raise ValueError("HEVC 10-bit profile requires a supported 10-bit pixel format")
        if not 1 <= self.quality <= 100:
            raise ValueError("HEVC quality must be between 1 and 100")
        if self.audio_codec != "aac":
            raise ValueError("the initial HEVC profile supports AAC audio only")
        if self.audio_bitrate not in {"128k", "160k", "192k", "256k", "320k"}:
            raise ValueError("unsupported AAC bitrate")

    def normalized(self) -> dict[str, Any]:
        return {
            "video_codec": self.video_codec,
            "pixel_format": self.pixel_format,
            "quality": self.quality,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "include_audio": self.include_audio,
            "strip_metadata": self.strip_metadata,
            "profile_version": HEVC_PROFILE_VERSION,
        }

    def video_argv(self) -> list[str]:
        if self.video_codec == "hevc_videotoolbox":
            return [
                "-c:v", self.video_codec,
                "-allow_sw", "1",
                "-profile:v", "main10",
                "-pix_fmt", self.pixel_format,
                "-q:v", str(self.quality),
            ]
        return [
            "-c:v", self.video_codec,
            "-preset", "slow",
            "-profile:v", "main10",
            "-pix_fmt", self.pixel_format,
            "-crf", str(self.quality),
        ]

    def audio_argv(self) -> list[str]:
        if not self.include_audio:
            return ["-an"]
        return ["-c:a", self.audio_codec, "-b:a", self.audio_bitrate]


@dataclass(frozen=True)
class MKVSubtitleProfile:
    include_audio: bool = True
    default_subtitle_ordinal: int | None = 0
    forced_subtitle_ordinals: tuple[int, ...] = ()
    profile_version: str = MUX_SUBTITLE_PROFILE_VERSION

    def __post_init__(self) -> None:
        _require_bool("include_audio", self.include_audio)
        if self.profile_version != MUX_SUBTITLE_PROFILE_VERSION:
            raise ValueError("unsupported mux subtitle profile version")
        if self.default_subtitle_ordinal is not None:
            _require_int("default_subtitle_ordinal", self.default_subtitle_ordinal, 0, 255)
        forced = tuple(self.forced_subtitle_ordinals)
        if len(set(forced)) != len(forced):
            raise ValueError("forced_subtitle_ordinals must not contain duplicates")
        for index, ordinal in enumerate(forced):
            _require_int(f"forced_subtitle_ordinals[{index}]", ordinal, 0, 255)
        object.__setattr__(self, "forced_subtitle_ordinals", tuple(sorted(forced)))

    def normalized(self) -> dict[str, Any]:
        return {
            "include_audio": self.include_audio,
            "default_subtitle_ordinal": self.default_subtitle_ordinal,
            "forced_subtitle_ordinals": list(self.forced_subtitle_ordinals),
            "profile_version": self.profile_version,
        }


@dataclass(frozen=True)
class H264HardsubProfile:
    language: str
    video_codec: str = "libx264"
    preset: str = "slow"
    crf: int = 22
    tune: str = "film"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    include_audio: bool = True
    strip_metadata: bool = True
    refs: int | None = None
    bframes: int | None = None
    qcomp: float | None = None
    rc_lookahead: int | None = None
    aq_mode: int | None = None
    aq_strength: float | None = None
    deblock: tuple[int, int] | None = None
    me_range: int | None = None
    mbtree: bool | None = None
    profile_version: str = HARDSUB_PROFILE_VERSION

    def __post_init__(self) -> None:
        if self.language not in {"zh-hans", "zh-hant"}:
            raise ValueError("hardsub language must be zh-hans or zh-hant")
        if self.video_codec != "libx264":
            raise ValueError("the hardsub profile supports libx264 only")
        if self.preset not in {"medium", "slow", "slower", "veryslow"}:
            raise ValueError("unsupported libx264 preset")
        _require_int("crf", self.crf, 0, 51)
        if self.tune != "film":
            raise ValueError("the hardsub profile supports tune=film only")
        if self.pixel_format != "yuv420p":
            raise ValueError("the hardsub profile requires yuv420p")
        if self.audio_codec != "aac":
            raise ValueError("the hardsub profile supports AAC audio only")
        if self.audio_bitrate not in {"128k", "160k", "192k", "256k", "320k"}:
            raise ValueError("unsupported AAC bitrate")
        _require_bool("include_audio", self.include_audio)
        _require_bool("strip_metadata", self.strip_metadata)
        if self.profile_version not in {HARDSUB_PROFILE_VERSION_V1, HARDSUB_PROFILE_VERSION}:
            raise ValueError("unsupported hardsub profile version")
        if self.profile_version == HARDSUB_PROFILE_VERSION_V1 and self._x264_values():
            raise ValueError("h264-hardsub-v1 does not support animation x264 parameters")

    def _x264_values(self) -> tuple[tuple[str, Any], ...]:
        return tuple(
            (name, getattr(self, name))
            for name in (
                "refs", "bframes", "qcomp", "rc_lookahead", "aq_mode",
                "aq_strength", "deblock", "me_range", "mbtree",
            )
            if getattr(self, name) is not None
        )

    def normalized(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "language": self.language,
            "video_codec": self.video_codec,
            "preset": self.preset,
            "crf": self.crf,
            "tune": self.tune,
            "pixel_format": self.pixel_format,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "include_audio": self.include_audio,
            "strip_metadata": self.strip_metadata,
        }
        for name, value in self._x264_values():
            values[name] = list(value) if name == "deblock" else value
        values["profile_version"] = self.profile_version
        return values

    def video_argv(self) -> list[str]:
        argv = [
            "-c:v", self.video_codec,
            "-preset", self.preset,
            "-tune", self.tune,
            "-crf", str(self.crf),
            "-pix_fmt", self.pixel_format,
        ]
        x264_params = self.x264_params()
        if x264_params:
            argv.extend(["-x264-params", x264_params])
        return argv

    def x264_params(self) -> str:
        names = {
            "refs": "ref",
            "bframes": "bframes",
            "qcomp": "qcomp",
            "rc_lookahead": "rc-lookahead",
            "aq_mode": "aq-mode",
            "aq_strength": "aq-strength",
            "deblock": "deblock",
            "me_range": "merange",
            "mbtree": "mbtree",
        }
        encoded = []
        for field, value in self._x264_values():
            if field == "deblock":
                rendered = f"{value[0]},{value[1]}"
            elif field == "mbtree":
                rendered = "1" if value else "0"
            elif isinstance(value, float):
                rendered = format(value, ".15g")
            else:
                rendered = str(value)
            encoded.append(f"{names[field]}={rendered}")
        return ":".join(encoded)

    def audio_argv(self) -> list[str]:
        if not self.include_audio:
            return ["-an"]
        return ["-c:a", self.audio_codec, "-b:a", self.audio_bitrate]


def normalize_h264_parameters(parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate and canonicalize the controlled H.264 hardsub parameter object."""
    values = dict(parameters or {})
    version = values.pop("profile_version", HARDSUB_PROFILE_VERSION)
    if not isinstance(version, str) or version not in {
        HARDSUB_PROFILE_VERSION_V1, HARDSUB_PROFILE_VERSION,
    }:
        raise ValueError("unsupported hardsub profile version")
    if version == HARDSUB_PROFILE_VERSION_V1:
        versioned = sorted(set(values) & _HARDSUB_ANIMATION_FIELDS)
        if versioned:
            raise ValueError("h264-hardsub-v1 does not support animation x264 parameters")
    allowed = set(_HARDSUB_BASE_FIELDS)
    if version == HARDSUB_PROFILE_VERSION:
        allowed.update(_HARDSUB_ANIMATION_FIELDS)
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported hardsub profile parameters: {', '.join(unknown)}")

    normalized = dict(values)
    for name in ("crf", "refs", "bframes", "rc_lookahead", "aq_mode", "me_range"):
        if name in normalized:
            ranges = {
                "crf": (0, 51), "refs": (1, 16), "bframes": (0, 16),
                "rc_lookahead": (0, 250), "aq_mode": (0, 3), "me_range": (4, 64),
            }
            normalized[name] = _require_int(name, normalized[name], *ranges[name])
    for name in ("qcomp", "aq_strength"):
        if name in normalized:
            ranges = {"qcomp": (0.0, 1.0), "aq_strength": (0.0, 3.0)}
            normalized[name] = _require_number(name, normalized[name], *ranges[name])
    for name in ("include_audio", "strip_metadata", "mbtree"):
        if name in normalized:
            normalized[name] = _require_bool(name, normalized[name])
    if "deblock" in normalized:
        value = normalized["deblock"]
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("deblock must be a two-item array [alpha, beta]")
        normalized["deblock"] = tuple(
            _require_int(f"deblock[{index}]", item, -6, 6)
            for index, item in enumerate(value)
        )
    normalized["profile_version"] = version
    return normalized


def normalize_mux_subtitle_parameters(parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    values = dict(parameters or {})
    version = values.pop("profile_version", MUX_SUBTITLE_PROFILE_VERSION)
    if version != MUX_SUBTITLE_PROFILE_VERSION:
        raise ValueError("unsupported mux subtitle profile version")
    allowed = {"include_audio", "default_subtitle_ordinal", "forced_subtitle_ordinals"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported mux subtitle profile parameters: {', '.join(unknown)}")
    if "include_audio" in values:
        values["include_audio"] = _require_bool("include_audio", values["include_audio"])
    if "default_subtitle_ordinal" in values and values["default_subtitle_ordinal"] is not None:
        values["default_subtitle_ordinal"] = _require_int(
            "default_subtitle_ordinal", values["default_subtitle_ordinal"], 0, 255,
        )
    if "forced_subtitle_ordinals" in values:
        forced = values["forced_subtitle_ordinals"]
        if not isinstance(forced, (list, tuple)):
            raise ValueError("forced_subtitle_ordinals must be an array")
        normalized = tuple(
            _require_int(f"forced_subtitle_ordinals[{index}]", item, 0, 255)
            for index, item in enumerate(forced)
        )
        if len(set(normalized)) != len(normalized):
            raise ValueError("forced_subtitle_ordinals must not contain duplicates")
        values["forced_subtitle_ordinals"] = tuple(sorted(normalized))
    values["profile_version"] = version
    return values


def normalize_profile(operation: ProductionOperation, output_profile: str,
                      parameters: Mapping[str, Any] | None = None
                      ) -> HEVC10BitProfile | H264HardsubProfile | MKVSubtitleProfile:
    values = dict(parameters or {})
    if operation is ProductionOperation.ENCODE:
        if output_profile != HEVC_10BIT_PROFILE:
            raise ValueError("only the hevc-10bit encode profile is supported")
        version = values.pop("profile_version", HEVC_PROFILE_VERSION)
        if version != HEVC_PROFILE_VERSION:
            raise ValueError("unsupported HEVC profile version")
        allowed = {
            "video_codec", "pixel_format", "quality", "audio_codec", "audio_bitrate",
            "include_audio", "strip_metadata",
        }
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unsupported HEVC profile parameters: {', '.join(unknown)}")
        return HEVC10BitProfile(**values)
    if operation is ProductionOperation.HARDSUB:
        languages = {H264_CHS_PROFILE: "zh-hans", H264_CHT_PROFILE: "zh-hant"}
        language = languages.get(output_profile)
        if language is None:
            raise ValueError("hardsub profile must be h264-chs or h264-cht")
        supplied_language = values.pop("language", language)
        if supplied_language != language:
            raise ValueError("hardsub profile language does not match output profile")
        normalized = normalize_h264_parameters(values)
        return H264HardsubProfile(language=language, **normalized)
    if operation is ProductionOperation.MUX_SUBTITLE:
        if output_profile != MKV_SUBTITLE_PROFILE:
            raise ValueError("mux_subtitle profile must be mkv-subtitle")
        normalized = normalize_mux_subtitle_parameters(values)
        return MKVSubtitleProfile(**normalized)
    raise ValueError("this production operation is not executable in the current Phase C slice")


def immutable_parameters(
    profile: HEVC10BitProfile | H264HardsubProfile | MKVSubtitleProfile,
) -> Mapping[str, Any]:
    return MappingProxyType(profile.normalized())


def _require_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _require_number(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value
