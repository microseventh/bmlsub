"""Semantic ASS analysis, classification, grouping, and decision logs."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any, Iterable

from ..state.models import ArtifactRecord
from .constants import (
    ANALYSIS_SCHEMA_VERSION, CLASSIFIER_VERSION,
    FONT_RESOLVER_VERSION, PARSER_VERSION, TAG_PARSER_VERSION,
)
from .effect_groups import build_effect_groups, effect_expansion_evidence
from .fonts import analyze_fonts
from .event_ids import build_event_ids, event_id_strategy, text_id_strategy
from .models import AssDiagnostic, AssDocument, AssEvent, AssRecord
from .profiles import AssAnalysisProfile
from .text import text_features


_LOCAL_PATHS = {"audio file", "audio uri", "video file", "timecodes file", "keyframes file"}
_UI_STATE = {"video zoom percent", "scroll position", "active line", "video position"}
_PROJECT_TOOLS = {"automation scripts", "export filters", "export encoding", "last style storage"}
_ASPECT = {"video ar mode", "video ar value"}
_KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_HANS_HINT_RE = re.compile(r"[这测试纯属虚构里面东西为后发台书车门复云广开关战爱礼里]")
_HANT_HINT_RE = re.compile(r"[這測試純屬虛構裡東西為後發臺書車門復雲廣開關戰愛禮裏]")


def build_analysis(document: AssDocument, *, source_artifact: ArtifactRecord,
                   profile: AssAnalysisProfile,
                   font_artifacts: Iterable[ArtifactRecord] = (),
                   video_artifact: ArtifactRecord | None = None) -> dict[str, Any]:
    diagnostics = list(document.diagnostics)
    script_info, script_decisions = analyze_script_info(document, profile, diagnostics, video_artifact)
    garbage_raw, garbage_decisions = analyze_project_garbage(document, profile)
    styles, style_usage = analyze_styles(document, diagnostics)
    event_id_result = build_event_ids(document, profile.event_ids)
    diagnostics.extend(event_id_result.diagnostics)
    event_items, groups, event_stats, review_queue = analyze_events(
        document, profile, event_id_result.items, diagnostics,
    )
    semantic_groups, grouping_review = build_effect_groups(
        event_items, profile.effect_collapse,
    )
    review_queue.extend(grouping_review)
    event_stats["semantic_group_count"] = len(semantic_groups)
    event_stats["semantic_group_source_count"] = sum(
        len(item["source_refs"]) for item in semantic_groups
    )
    event_stats["templater_control_count"] = sum(
        item["classification"]["event_kind"] == "templater_control"
        for item in event_items
    )
    fonts = analyze_fonts(document, event_id_result.items, font_artifacts)
    for resolution in fonts["resolution"]:
        if resolution["status"] != "resolved":
            review_queue.append({
                "kind": "font", "reason": resolution["status"],
                "font": resolution["family"], "bold": resolution["bold"],
                "italic": resolution["italic"],
            })
    for event in document.events:
        id_metadata = event_id_result.items[event.ordinal]
        if id_metadata["status"] == "hash_collision":
            review_queue.append({
                "kind": "event_id", "source_ref": id_metadata["source_ref"],
                "event_id": id_metadata["event_id"],
                "line": event.line_number, "ordinal": event.ordinal,
                "reason": id_metadata["status"],
            })
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "source": {
            "artifact_id": source_artifact.artifact_id,
            "sha256": source_artifact.content_hash,
            "path_name": source_artifact.path.name,
            "encoding": document.encoding,
        },
        "document": document.to_document_dict(),
        "script_info": {
            "raw": script_info, "normalized": _normalized_script_info(script_info, profile),
            "decisions": script_decisions,
        },
        "project_garbage": {"raw": garbage_raw, "decisions": garbage_decisions},
        "styles": {
            "format": list(document.style_format), "items": styles, "usage": style_usage,
        },
        "events": {
            "format": list(document.event_format),
            "id_strategy": event_id_strategy(profile.event_ids),
            "text_id_strategy": text_id_strategy(),
            "items": event_items,
            "groups": groups, "semantic_groups": semantic_groups,
            "statistics": event_stats,
            "id_duplicate_groups": list(event_id_result.duplicate_groups),
        },
        "fonts": fonts,
        "review_queue": review_queue,
        "diagnostics": [item.to_dict() for item in diagnostics],
        "toolchain": {
            "parser": PARSER_VERSION, "tag_parser": TAG_PARSER_VERSION,
            "classifier": CLASSIFIER_VERSION, "font_resolver": FONT_RESOLVER_VERSION,
            "profile": profile.profile_version,
        },
    }


def analyze_script_info(document: AssDocument, profile: AssAnalysisProfile,
                        diagnostics: list[AssDiagnostic], video_artifact=None):
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in document.script_info:
        if record.key is None:
            if record.raw.strip() and not record.raw.lstrip().startswith(";"):
                diagnostics.append(AssDiagnostic(
                    "script_info_unparsed", "Script Info line is not a key/value record",
                    section="Script Info", line=record.line_number,
                ))
            continue
        values[record.key].append({"value": record.value or "", "line": record.line_number})
    for key, items in values.items():
        if len(items) > 1:
            diagnostics.append(AssDiagnostic(
                "script_info_duplicate_key", "Script Info key appears more than once",
                section="Script Info", line=items[-1]["line"], field=key,
            ))
    current = {key: items[-1]["value"] for key, items in values.items()}
    lower = {key.casefold(): (key, value) for key, value in current.items()}
    _validate_script_field(lower, "scripttype", lambda value: value.casefold() == "v4.00+",
                           "ScriptType must be v4.00+", diagnostics)
    for name in ("playresx", "playresy"):
        _validate_script_field(lower, name, lambda value: _positive_int(value),
                               f"{name} must be a positive integer", diagnostics)
    _validate_script_field(lower, "wrapstyle", lambda value: _int_range(value, 0, 3),
                           "WrapStyle must be an integer from 0 through 3", diagnostics, required=False)
    _validate_script_field(lower, "scaledborderandshadow", _boolean_value,
                           "ScaledBorderAndShadow must have boolean semantics", diagnostics,
                           required=False)
    known_matrices = {"none", "tv.601", "pc.601", "tv.709", "pc.709", "tv.2020", "pc.2020"}
    _validate_script_field(lower, "ycbcr matrix", lambda value: value.casefold() in known_matrices,
                           "YCbCr Matrix is not a known ASS matrix", diagnostics, required=False)
    decisions = []
    for key, new_value in profile.metadata.updates.items():
        matched = lower.get(key.casefold())
        decisions.append({
            "field": key, "old_value": matched[1] if matched else None,
            "new_value": new_value, "action": "update" if matched else "insert",
            "value_source": profile.metadata.value_source,
            "rule_version": profile.metadata.rule_version,
            "requires_review": profile.metadata.require_confirmation,
        })
    return current, decisions


def analyze_project_garbage(document: AssDocument, profile: AssAnalysisProfile):
    raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions = []
    policy = profile.project_garbage
    for record in document.project_garbage:
        if record.key is None:
            continue
        raw[record.key].append({"value": record.value or "", "line": record.line_number})
        normalized = record.key.casefold()
        if normalized in {key.casefold() for key in policy.overrides}:
            override_key = next(key for key in policy.overrides if key.casefold() == normalized)
            category, action = "override", policy.overrides[override_key]
        elif normalized in _LOCAL_PATHS:
            category, action = "local_path", policy.local_paths
        elif normalized in _UI_STATE:
            category, action = "ui_state", policy.ui_state
        elif normalized in _PROJECT_TOOLS or normalized.startswith("automation settings "):
            category, action = "project_tool", policy.project_tools
        elif normalized in _ASPECT:
            category, action = "aspect_ratio", policy.aspect_ratio
        else:
            category, action = "unknown", policy.unknown_fields
        decisions.append({
            "field": record.key, "value": record.value or "", "line": record.line_number,
            "category": category, "action": action, "rule_version": policy.rule_version,
            "requires_review": action == "review",
        })
    return dict(raw), decisions


def analyze_styles(document: AssDocument, diagnostics: list[AssDiagnostic]):
    names = Counter(item.name for item in document.styles if item.name)
    usage = Counter(event.style for event in document.events if event.style)
    items = []
    for style in document.styles:
        if not style.fields:
            continue
        issues = []
        if not style.name:
            issues.append("empty_name")
        if names[style.name] > 1:
            issues.append("duplicate_name")
        if usage[style.name] == 0:
            issues.append("unused")
        items.append({
            "line": style.line_number, "fields": dict(style.fields),
            "name": style.name, "fontname": style.fontname,
            "bold": style.bold, "italic": style.italic, "issues": issues,
        })
    undefined = sorted({event.style for event in document.events if event.style and event.style not in names})
    for name in undefined:
        diagnostics.append(AssDiagnostic(
            "undefined_style", "Event references an undefined Style", section="Events",
            field="Style", evidence={"style": name},
        ))
    return items, {
        "counts": dict(sorted(usage.items())),
        "undefined": undefined,
        "unused": sorted(name for name in names if usage[name] == 0),
    }


def analyze_events(document: AssDocument, profile: AssAnalysisProfile,
                   event_ids: dict[int, dict[str, Any]], diagnostics: list[AssDiagnostic]):
    items = []
    review = []
    groups: list[dict[str, Any]] = []
    group_index = 0
    current_group = f"sequence-{group_index:04d}"
    record_counts = Counter()
    style_counts = Counter()
    language_counts = Counter()
    kind_counts = Counter()
    role_counts = Counter()
    starts: list[int] = []
    ends: list[int] = []
    parsed_times: dict[int, tuple[int, int]] = {}
    for event in document.events:
        id_metadata = dict(event_ids[event.ordinal])
        event_id = id_metadata["event_id"]
        text_id = id_metadata["text_id"]
        source_ref = id_metadata["source_ref"]
        record_counts[event.record_type] += 1
        style_counts[event.style] += 1
        features = text_features(event.text)
        visible = features["plain_text"]
        matched_rule = next((rule for rule in profile.text_split_rules
                             if rule.matches(event.record_type, visible)), None)
        evidence: list[dict[str, Any]] = [{"type": "record_type", "value": event.record_type}]
        kind, role, confidence = _classify_event(event, features, profile, evidence)
        if matched_rule is not None:
            kind, role, confidence = matched_rule.event_kind, matched_rule.content_role, 1.0
            evidence.append({"type": "text_split_rule", "rule_id": matched_rule.rule_id,
                             "group_semantic": matched_rule.group_semantic})
            marker_group = f"marker-{group_index:04d}"
            groups.append({
                "group_id": marker_group, "kind": "marker", "rule_id": matched_rule.rule_id,
                "source_refs": [source_ref],
                "event_ids": [event_id] if event_id is not None else [],
                "semantic": matched_rule.group_semantic,
            })
            if matched_rule.group_semantic in {"boundary", "start", "split"}:
                group_index += 1
                current_group = f"sequence-{group_index:04d}"
        language, language_evidence = _language(event.style, visible, profile)
        evidence.extend(language_evidence)
        start_ms = _ass_time(event.fields.get("start"))
        end_ms = _ass_time(event.fields.get("end"))
        issues = []
        if start_ms is None or end_ms is None:
            issues.append("invalid_time")
        else:
            starts.append(start_ms)
            ends.append(end_ms)
            parsed_times[event.ordinal] = (start_ms, end_ms)
            if end_ms < start_ms:
                issues.append("end_before_start")
            elif end_ms == start_ms:
                issues.append("zero_duration")
        if not visible and not features["has_drawing"]:
            issues.append("empty_visible_text")
        if len(visible) > profile.long_line_characters:
            issues.append("long_line")
        if confidence < 0.6 or issues:
            review.append({
                "kind": "event", "source_ref": source_ref, "event_id": event_id,
                "line": event.line_number, "ordinal": event.ordinal,
                "reasons": (["low_confidence"] if confidence < 0.6 else []) + issues,
            })
        kind_counts[kind] += 1
        role_counts[role] += 1
        language_counts[language] += 1
        item = {
            "source_ref": source_ref, "event_id": event_id, "text_id": text_id,
            "duplicate_ordinal": id_metadata["duplicate_ordinal"], "id": id_metadata,
            "line": event.line_number, "ordinal": event.ordinal,
            "record_type": event.record_type, "fields": dict(event.fields),
            "text": {"raw": event.text, **features},
            "start_ms": start_ms, "end_ms": end_ms, "style": event.style,
            "classification": {
                "event_kind": kind, "content_role": role, "language": language,
                "effect_level": _effect_level(features), "group_id": current_group,
                "confidence": confidence, "evidence": evidence,
            },
            "issues": issues,
        }
        item["effect_expansion"] = effect_expansion_evidence(item)
        items.append(item)
    sequence_groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        sequence_groups[item["classification"]["group_id"]].append(item["source_ref"])
    groups.extend({"group_id": key, "kind": "sequence", "source_refs": value}
                  for key, value in sequence_groups.items())
    overlaps = _overlap_count(parsed_times, profile.overlap_threshold_ms)
    statistics = {
        "event_count": len(items), "record_types": dict(record_counts),
        "styles": dict(sorted(style_counts.items())), "languages": dict(language_counts),
        "event_kinds": dict(kind_counts), "content_roles": dict(role_counts),
        "first_time_ms": min(starts) if starts else None,
        "last_time_ms": max(ends) if ends else None,
        "overlap_count": overlaps,
        "long_line_count": sum("long_line" in item["issues"] for item in items),
        "event_id_statuses": dict(Counter(item["id"]["status"] for item in items)),
        "event_id_modes": dict(Counter(item["id"]["mode"] for item in items)),
        "event_id_generated_count": sum(item["event_id"] is not None for item in items),
        "event_id_null_count": sum(item["event_id"] is None for item in items),
        "text_id_generated_count": sum(item["text_id"] is not None for item in items),
        "text_id_null_count": sum(item["text_id"] is None for item in items),
        "source_ref_count": len({item["source_ref"] for item in items}),
        "duplicate_identical_event_count": sum(
            item["duplicate_ordinal"] > 0 for item in items
        ),
    }
    return items, groups, statistics, review


def _classify_event(event: AssEvent, features: dict[str, Any], profile: AssAnalysisProfile,
                    evidence: list[dict[str, Any]]):
    style_role = _normalize_role(_lookup_casefold(profile.style_roles, event.style))
    if style_role:
        evidence.append({"type": "profile_style_role", "style": event.style, "value": style_role})
    effect = event.fields.get("effect", "").strip().casefold()
    if event.record_type == "comment" and re.match(r"^(code|template)(?:\s|$)", effect):
        evidence.append({"type": "templater_effect", "value": effect.split(maxsplit=1)[0]})
        return "templater_control", "templater_control", 1.0
    if event.record_type == "comment" and features["plain_text"] and (
        effect == "karaoke" or features["has_karaoke"]
    ):
        evidence.append({"type": "templater_parent", "value": effect or "karaoke-tags"})
        return "karaoke_parent", style_role or _style_song_role(event.style) or "unknown_song", 0.95
    if event.record_type == "comment":
        return "comment", "note", 0.75
    if features["has_drawing"]:
        return "drawing", style_role or "decorative", 0.95
    if features["has_karaoke"]:
        return "karaoke", style_role or _style_song_role(event.style) or "unknown_song", 0.9
    lowered = event.style.casefold()
    if style_role:
        return ("credit" if style_role == "credit" else "dialogue"), style_role, 0.9
    song_role = _style_song_role(event.style)
    if song_role:
        evidence.append({"type": "style_token", "value": song_role})
        return "dialogue", song_role, 0.8
    if any(token in lowered for token in ("sign", "dec", "screen")):
        evidence.append({"type": "style_hint", "value": event.style})
        return "dialogue", "sign", 0.7
    if event.record_type == "dialogue":
        return "dialogue", "main", 0.65
    return "unknown", "unknown", 0.3


def _normalize_role(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.strip().casefold()
    return "insert_song" if lowered in {"insert", "in", "insert-song"} else lowered


def _style_song_role(style: str) -> str | None:
    lowered = re.sub(r"[^a-z0-9]+", "", style.casefold())
    if lowered.startswith("op") or lowered.endswith("op") and len(lowered) <= 6:
        return "op"
    if lowered.startswith("ed"):
        return "ed"
    if lowered.startswith("insert") or re.match(r"^in(?:cn|jp|jpn|ja|zh|chs|cht)?$", lowered):
        return "insert_song"
    return None


def _language(style: str, text: str, profile: AssAnalysisProfile):
    explicit = _lookup_casefold(profile.style_languages, style)
    if explicit:
        return explicit, [{"type": "profile_style_language", "style": style, "value": explicit}]
    has_kana = bool(_KANA_RE.search(text))
    has_han = bool(_HAN_RE.search(text))
    has_hans = bool(_HANS_HINT_RE.search(text))
    has_hant = bool(_HANT_HINT_RE.search(text))
    if has_kana and has_han:
        return "mixed", [{"type": "unicode_script", "value": "kana+han"}]
    if has_kana:
        return "ja", [{"type": "unicode_script", "value": "kana"}]
    if has_hans and has_hant:
        return "mixed", [{"type": "unicode_hint", "value": "hans+hant"}]
    if has_hant:
        return "zh-Hant", [{"type": "unicode_hint", "value": "hant"}]
    if has_hans:
        return "zh-Hans", [{"type": "unicode_hint", "value": "hans"}]
    if has_han:
        style_lower = style.casefold()
        if any(token in style_lower for token in ("jp", "jpn", " ja")):
            return "ja", [{"type": "style_hint", "value": style}]
        if any(token in style_lower for token in ("cn", "chs", "zh")):
            return profile.default_language or "zh-Hans", [{"type": "style_hint", "value": style}]
        return profile.default_language or "und", [{"type": "unicode_script", "value": "han"}]
    return profile.default_language or "und", []


def _normalized_script_info(raw: dict[str, str], profile: AssAnalysisProfile) -> dict[str, str]:
    result = dict(raw)
    indexes = {key.casefold(): key for key in result}
    for key, value in profile.metadata.updates.items():
        existing = indexes.get(key.casefold())
        if existing is not None and existing != key:
            del result[existing]
        result[key] = value
    return result


def _validate_script_field(values, key, validator, message, diagnostics, required=True):
    item = values.get(key)
    if item is None:
        if required:
            diagnostics.append(AssDiagnostic(
                "script_info_required_missing", message, section="Script Info",
                field=key, severity="error",
            ))
        return
    if not validator(item[1]):
        diagnostics.append(AssDiagnostic(
            "script_info_invalid", message, section="Script Info", field=item[0],
        ))


def _positive_int(value: str) -> bool:
    try:
        return int(value) > 0
    except ValueError:
        return False


def _int_range(value: str, minimum: int, maximum: int) -> bool:
    try:
        return minimum <= int(value) <= maximum
    except ValueError:
        return False


def _boolean_value(value: str) -> bool:
    return value.strip().casefold() in {"yes", "no", "true", "false", "0", "1", "-1"}


def _ass_time(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})[.](\d{1,3})\s*", value)
    if not match:
        return None
    fraction = int(match.group(4).ljust(3, "0"))
    return (((int(match.group(1)) * 60 + int(match.group(2))) * 60
             + int(match.group(3))) * 1000 + fraction)


def _effect_level(features: dict[str, Any]) -> str:
    if features["has_drawing"]:
        return "drawing"
    if features["has_animation"]:
        return "animated"
    if features["has_position"] or features["has_clip"]:
        return "positioned"
    if features["has_override"]:
        return "styled"
    return "plain"


def _overlap_count(times: dict[int, tuple[int, int]], threshold: int) -> int:
    ordered = sorted(times.values())
    count = 0
    active_end = -1
    for start, end in ordered:
        if start + threshold < active_end:
            count += 1
        active_end = max(active_end, end)
    return count


def _lookup_casefold(values, key):
    expected = key.casefold()
    for candidate, value in values.items():
        if candidate.casefold() == expected:
            return value
    return None
