"""Strict, fingerprintable profiles for ASS analysis and normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


_ALLOWED_MATCH = {"exact", "regex"}
_ALLOWED_RECORD_TYPES = {"dialogue", "comment"}
_ALLOWED_ACTIONS = {"keep", "remove", "review"}
_ALLOWED_EVENT_ID_MODES = {"visible_text", "raw_text", "fields"}
_ALLOWED_DUPLICATE_POLICIES = {"promote_to_line_fields"}
_ALLOWED_SONG_ROLES = {"op", "ed", "insert_song"}


@dataclass(frozen=True)
class EventIdPolicy:
    mode: str = "visible_text"
    fields: tuple[str, ...] = ()
    calculate_empty_text: bool = False
    duplicate_policy: str = "promote_to_line_fields"

    def __post_init__(self) -> None:
        if self.mode not in _ALLOWED_EVENT_ID_MODES:
            raise ValueError("Event ID mode must be visible_text, raw_text, or fields")
        if self.duplicate_policy not in _ALLOWED_DUPLICATE_POLICIES:
            raise ValueError("Event ID duplicate policy is invalid")
        normalized = tuple(str(item).strip().casefold() for item in self.fields)
        if any(not item for item in normalized):
            raise ValueError("Event ID fields must not contain empty names")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Event ID fields must be unique")
        if self.mode != "fields" and normalized:
            raise ValueError("Event ID fields can only be set when mode is fields")
        object.__setattr__(self, "fields", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fields": list(self.fields),
            "calculate_empty_text": self.calculate_empty_text,
            "duplicate_policy": self.duplicate_policy,
        }


@dataclass(frozen=True)
class TextSplitRule:
    rule_id: str
    pattern: str
    match: str = "exact"
    record_types: tuple[str, ...] = ("comment",)
    case_sensitive: bool = True
    trim_whitespace: bool = True
    event_kind: str = "marker"
    content_role: str = "unknown"
    group_semantic: str = "boundary"

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.pattern:
            raise ValueError("text split rule_id and pattern must not be empty")
        if self.match not in _ALLOWED_MATCH:
            raise ValueError("text split match must be exact or regex")
        record_types = tuple(item.lower() for item in self.record_types)
        if not record_types or any(item not in _ALLOWED_RECORD_TYPES for item in record_types):
            raise ValueError("text split record_types must contain dialogue/comment")
        if self.match == "regex":
            re.compile(self.pattern)
        object.__setattr__(self, "rule_id", self.rule_id.strip())
        object.__setattr__(self, "record_types", record_types)

    def matches(self, record_type: str, text: str) -> bool:
        if record_type.lower() not in self.record_types:
            return False
        candidate = text.strip() if self.trim_whitespace else text
        pattern = self.pattern.strip() if self.trim_whitespace else self.pattern
        if self.match == "exact":
            if not self.case_sensitive:
                candidate, pattern = candidate.casefold(), pattern.casefold()
            return candidate == pattern
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.fullmatch(pattern, candidate, flags=flags) is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "pattern": self.pattern, "match": self.match,
            "record_types": list(self.record_types), "case_sensitive": self.case_sensitive,
            "trim_whitespace": self.trim_whitespace, "event_kind": self.event_kind,
            "content_role": self.content_role, "group_semantic": self.group_semantic,
        }


@dataclass(frozen=True)
class EffectCollapsePolicy:
    roles: tuple[str, ...] = ("op", "ed", "insert_song")
    minimum_expanded_events: int = 3
    parent_time_tolerance_ms: int = 1000
    cluster_gap_ms: int = 1000
    dominant_time_ratio: float = 0.6

    def __post_init__(self) -> None:
        roles = tuple(str(item).strip().casefold() for item in self.roles)
        if not roles or any(item not in _ALLOWED_SONG_ROLES for item in roles):
            raise ValueError("effect collapse roles must contain op/ed/insert_song")
        if len(roles) != len(set(roles)):
            raise ValueError("effect collapse roles must be unique")
        for name in ("minimum_expanded_events", "parent_time_tolerance_ms", "cluster_gap_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.minimum_expanded_events < 2:
            raise ValueError("minimum_expanded_events must be at least 2")
        if isinstance(self.dominant_time_ratio, bool) or not isinstance(
            self.dominant_time_ratio, (int, float)
        ) or not 0.5 <= float(self.dominant_time_ratio) <= 1.0:
            raise ValueError("dominant_time_ratio must be between 0.5 and 1.0")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "dominant_time_ratio", float(self.dominant_time_ratio))

    def to_dict(self) -> dict[str, Any]:
        return {
            "roles": list(self.roles),
            "minimum_expanded_events": self.minimum_expanded_events,
            "parent_time_tolerance_ms": self.parent_time_tolerance_ms,
            "cluster_gap_ms": self.cluster_gap_ms,
            "dominant_time_ratio": self.dominant_time_ratio,
        }


@dataclass(frozen=True)
class AssMetadataPolicy:
    updates: Mapping[str, str] = field(default_factory=dict)
    value_source: str = "explicit_profile"
    rule_version: str = "ass-metadata-v1"
    require_confirmation: bool = False

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for key, value in self.updates.items():
            if not str(key).strip() or not isinstance(value, str):
                raise ValueError("metadata updates require nonempty string keys and string values")
            normalized[str(key).strip()] = value
        object.__setattr__(self, "updates", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "updates": dict(self.updates), "value_source": self.value_source,
            "rule_version": self.rule_version, "require_confirmation": self.require_confirmation,
        }


@dataclass(frozen=True)
class ProjectGarbagePolicy:
    local_paths: str = "remove"
    ui_state: str = "remove"
    project_tools: str = "review"
    aspect_ratio: str = "remove"
    unknown_fields: str = "keep"
    overrides: Mapping[str, str] = field(default_factory=dict)
    rule_version: str = "ass-project-garbage-v1"

    def __post_init__(self) -> None:
        for value in (self.local_paths, self.ui_state, self.project_tools,
                      self.aspect_ratio, self.unknown_fields, *self.overrides.values()):
            if value not in _ALLOWED_ACTIONS:
                raise ValueError("Project Garbage actions must be keep/remove/review")

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_paths": self.local_paths, "ui_state": self.ui_state,
            "project_tools": self.project_tools, "aspect_ratio": self.aspect_ratio,
            "unknown_fields": self.unknown_fields, "overrides": dict(self.overrides),
            "rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class AssReconstructionProfile:
    play_res_x: int | None = None
    play_res_y: int | None = None
    chinese_variant: str = "simplified"
    cn_font: str | None = None
    jp_font: str = "Hiragino Sans"
    cn_font_size: int = 81
    jp_font_size: int = 48
    main_margin_l: int = 10
    main_margin_r: int = 10
    cn_margin_v: int = 55
    jp_margin_v: int = 17
    note_font_size: int = 42
    sign_font_size: int = 77
    op_cn_font_size: int = 74
    op_jp_font_size: int = 66
    in_cn_font_size: int = 74
    in_jp_font_size: int = 66
    style_languages: Mapping[str, str] = field(default_factory=dict)
    style_roles: Mapping[str, str] = field(default_factory=dict)
    translation_required_languages: tuple[str, ...] = ("ja", "und", "mixed")
    allow_sign_readability_tags: bool = False
    profile_version: str = "ass-reconstruction-profile-v3"

    def __post_init__(self) -> None:
        for name, value in (("play_res_x", self.play_res_x), ("play_res_y", self.play_res_y)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or null")
        if self.chinese_variant not in {"simplified", "traditional"}:
            raise ValueError("chinese_variant must be simplified or traditional")
        for name in (
            "cn_font_size", "jp_font_size", "main_margin_l", "main_margin_r",
            "cn_margin_v", "jp_margin_v", "note_font_size", "sign_font_size",
            "op_cn_font_size", "op_jp_font_size", "in_cn_font_size", "in_jp_font_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.cn_font is not None and not self.cn_font.strip():
            raise ValueError("cn_font must be null or a nonempty string")
        if not self.jp_font.strip():
            raise ValueError("jp_font must not be empty")
        languages = tuple(str(item).strip() for item in self.translation_required_languages)
        if any(not item for item in languages):
            raise ValueError("translation-required languages must not be empty")
        object.__setattr__(self, "style_languages", {
            str(key): str(value) for key, value in self.style_languages.items()
        })
        object.__setattr__(self, "style_roles", {
            str(key): str(value) for key, value in self.style_roles.items()
        })
        object.__setattr__(self, "translation_required_languages", languages)

    @property
    def resolved_cn_font(self) -> str:
        if self.cn_font is not None:
            return self.cn_font
        return "PingFang TC" if self.chinese_variant == "traditional" else "PingFang SC"

    def to_dict(self) -> dict[str, Any]:
        return {
            "play_res_x": self.play_res_x, "play_res_y": self.play_res_y,
            "chinese_variant": self.chinese_variant,
            "cn_font": self.cn_font, "jp_font": self.jp_font,
            "cn_font_size": self.cn_font_size, "jp_font_size": self.jp_font_size,
            "main_margin_l": self.main_margin_l, "main_margin_r": self.main_margin_r,
            "cn_margin_v": self.cn_margin_v, "jp_margin_v": self.jp_margin_v,
            "note_font_size": self.note_font_size, "sign_font_size": self.sign_font_size,
            "op_cn_font_size": self.op_cn_font_size,
            "op_jp_font_size": self.op_jp_font_size,
            "in_cn_font_size": self.in_cn_font_size,
            "in_jp_font_size": self.in_jp_font_size,
            "style_languages": dict(self.style_languages),
            "style_roles": dict(self.style_roles),
            "translation_required_languages": list(self.translation_required_languages),
            "allow_sign_readability_tags": self.allow_sign_readability_tags,
            "profile_version": self.profile_version,
        }

    @classmethod
    def from_value(cls, value: "AssReconstructionProfile | Mapping[str, Any] | None") -> "AssReconstructionProfile":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("ASS reconstruction profile must be a JSON object")
        allowed = {
            "play_res_x", "play_res_y", "chinese_variant", "cn_font", "jp_font",
            "cn_font_size", "jp_font_size", "main_margin_l", "main_margin_r",
            "cn_margin_v", "jp_margin_v", "note_font_size", "sign_font_size",
            "op_cn_font_size", "op_jp_font_size", "in_cn_font_size", "in_jp_font_size", "style_languages", "style_roles",
            "translation_required_languages", "allow_sign_readability_tags",
            "profile_version",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown ASS reconstruction profile fields: {', '.join(sorted(unknown))}")
        kwargs = dict(value)
        for name in (
            "play_res_x", "play_res_y", "cn_font_size", "jp_font_size",
            "main_margin_l", "main_margin_r", "cn_margin_v", "jp_margin_v",
            "note_font_size", "sign_font_size", "op_cn_font_size", "op_jp_font_size",
            "in_cn_font_size", "in_jp_font_size",
        ):
            if name in kwargs and kwargs[name] is not None:
                kwargs[name] = _strict_int(kwargs[name], name)
        if "style_languages" in kwargs:
            kwargs["style_languages"] = _mapping(kwargs["style_languages"], "style_languages")
        if "style_roles" in kwargs:
            kwargs["style_roles"] = _mapping(kwargs["style_roles"], "style_roles")
        if "translation_required_languages" in kwargs:
            kwargs["translation_required_languages"] = _string_tuple(
                kwargs["translation_required_languages"], "translation_required_languages",
            )
        if "allow_sign_readability_tags" in kwargs:
            kwargs["allow_sign_readability_tags"] = _strict_bool(
                kwargs["allow_sign_readability_tags"], "allow_sign_readability_tags",
            )
        return cls(**kwargs)


@dataclass(frozen=True)
class AssAnalysisProfile:
    metadata: AssMetadataPolicy = field(default_factory=AssMetadataPolicy)
    project_garbage: ProjectGarbagePolicy = field(default_factory=ProjectGarbagePolicy)
    event_ids: EventIdPolicy = field(default_factory=EventIdPolicy)
    effect_collapse: EffectCollapsePolicy = field(default_factory=EffectCollapsePolicy)
    text_split_rules: tuple[TextSplitRule, ...] = ()
    style_roles: Mapping[str, str] = field(default_factory=dict)
    style_languages: Mapping[str, str] = field(default_factory=dict)
    default_language: str | None = None
    long_line_characters: int = 80
    overlap_threshold_ms: int = 0
    resolve_registered_fonts: bool = True
    profile_version: str = "ass-analysis-profile-v2"

    def __post_init__(self) -> None:
        rules = tuple(self.text_split_rules)
        if len({item.rule_id for item in rules}) != len(rules):
            raise ValueError("text split rule IDs must be unique")
        if self.long_line_characters <= 0 or self.overlap_threshold_ms < 0:
            raise ValueError("classification thresholds are invalid")
        object.__setattr__(self, "text_split_rules", rules)
        object.__setattr__(self, "style_roles", {str(k): str(v) for k, v in self.style_roles.items()})
        object.__setattr__(self, "style_languages", {str(k): str(v) for k, v in self.style_languages.items()})

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "project_garbage": self.project_garbage.to_dict(),
            "event_ids": self.event_ids.to_dict(),
            "effect_collapse": self.effect_collapse.to_dict(),
            "text_split_rules": [item.to_dict() for item in self.text_split_rules],
            "style_roles": dict(self.style_roles),
            "style_languages": dict(self.style_languages),
            "default_language": self.default_language,
            "long_line_characters": self.long_line_characters,
            "overlap_threshold_ms": self.overlap_threshold_ms,
            "resolve_registered_fonts": self.resolve_registered_fonts,
            "profile_version": self.profile_version,
        }

    @classmethod
    def from_value(cls, value: "AssAnalysisProfile | Mapping[str, Any] | None") -> "AssAnalysisProfile":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("ASS analysis profile must be a JSON object")
        allowed = {
            "metadata", "project_garbage", "event_ids", "effect_collapse",
            "text_split_rules", "style_roles",
            "style_languages", "default_language", "long_line_characters",
            "overlap_threshold_ms", "resolve_registered_fonts", "profile_version",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown ASS analysis profile fields: {', '.join(sorted(unknown))}")
        metadata_raw = value.get("metadata", {})
        garbage_raw = value.get("project_garbage", {})
        event_ids_raw = value.get("event_ids", {})
        effect_collapse_raw = value.get("effect_collapse", {})
        rules_raw = value.get("text_split_rules", [])
        if (not isinstance(metadata_raw, Mapping) or not isinstance(garbage_raw, Mapping) or
                not isinstance(event_ids_raw, Mapping) or
                not isinstance(effect_collapse_raw, Mapping)):
            raise ValueError(
                "metadata, project_garbage, event_ids, and effect_collapse profiles must be objects"
            )
        if not isinstance(rules_raw, list):
            raise ValueError("text_split_rules must be an array")
        return cls(
            metadata=AssMetadataPolicy(**dict(metadata_raw)),
            project_garbage=ProjectGarbagePolicy(**dict(garbage_raw)),
            event_ids=EventIdPolicy(
                mode=str(event_ids_raw.get("mode", "visible_text")),
                fields=_string_tuple(event_ids_raw.get("fields", ()), "event_ids.fields"),
                calculate_empty_text=_strict_bool(
                    event_ids_raw.get("calculate_empty_text", False),
                    "event_ids.calculate_empty_text",
                ),
                duplicate_policy=str(event_ids_raw.get(
                    "duplicate_policy", "promote_to_line_fields"
                )),
            ),
            effect_collapse=EffectCollapsePolicy(
                roles=_string_tuple(
                    effect_collapse_raw.get("roles", ("op", "ed", "insert_song")),
                    "effect_collapse.roles",
                ),
                minimum_expanded_events=_strict_int(
                    effect_collapse_raw.get("minimum_expanded_events", 3),
                    "effect_collapse.minimum_expanded_events",
                ),
                parent_time_tolerance_ms=_strict_int(
                    effect_collapse_raw.get("parent_time_tolerance_ms", 1000),
                    "effect_collapse.parent_time_tolerance_ms",
                ),
                cluster_gap_ms=_strict_int(
                    effect_collapse_raw.get("cluster_gap_ms", 1000),
                    "effect_collapse.cluster_gap_ms",
                ),
                dominant_time_ratio=_strict_number(
                    effect_collapse_raw.get("dominant_time_ratio", 0.6),
                    "effect_collapse.dominant_time_ratio",
                ),
            ),
            text_split_rules=tuple(TextSplitRule(**dict(item)) for item in rules_raw),
            style_roles=_mapping(value.get("style_roles", {}), "style_roles"),
            style_languages=_mapping(value.get("style_languages", {}), "style_languages"),
            default_language=value.get("default_language"),
            long_line_characters=_strict_int(value.get("long_line_characters", 80), "long_line_characters"),
            overlap_threshold_ms=_strict_int(value.get("overlap_threshold_ms", 0), "overlap_threshold_ms"),
            resolve_registered_fonts=_strict_bool(value.get("resolve_registered_fonts", True), "resolve_registered_fonts"),
            profile_version=str(value.get("profile_version", "ass-analysis-profile-v2")),
        )


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    return tuple(value)


def _mapping(value: Any, name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return {str(key): str(item) for key, item in value.items()}


def _strict_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _strict_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _strict_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value
