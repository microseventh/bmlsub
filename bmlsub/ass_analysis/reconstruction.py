"""Rebuild a deterministic standard ASS from versioned analysis JSON."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from .constants import ANALYSIS_SCHEMA_VERSION, RECONSTRUCTOR_VERSION
from .io import load_analysis
from .profiles import AssReconstructionProfile

_STYLE_FORMAT = (
    "Name", "Fontname", "Fontsize", "PrimaryColour", "SecondaryColour",
    "OutlineColour", "BackColour", "Bold", "Italic", "Underline", "StrikeOut",
    "ScaleX", "ScaleY", "Spacing", "Angle", "BorderStyle", "Outline", "Shadow",
    "Alignment", "MarginL", "MarginR", "MarginV", "Encoding",
)
_EVENT_FORMAT = (
    "Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR",
    "MarginV", "Effect", "Text",
)
_SECTION_LABELS = {
    "main_cn": "—— —— 正文 - 中文 —— ——",
    "main_jp": "—— —— 正文 - 日文 —— ——",
    "note": "—— —— 注释 —— ——",
    "sign": "—— —— 屏幕字 —— ——",
    "insert_song_cn": "—— —— IN - 中文 —— ——",
    "insert_song_jp": "—— —— IN - 日文 —— ——",
    "op_cn": "—— —— OP - 中文 —— ——",
    "op_jp": "—— —— OP - 日文 —— ——",
    "ed_cn": "—— —— ED - 中文 —— ——",
    "ed_jp": "—— —— ED - 日文 —— ——",
}
_SECTION_ORDER = tuple(_SECTION_LABELS)
_LANGUAGE_ORDER = {"cn": 0, "jp": 1}
_SONG_ROLES = {"op", "ed", "insert_song"}


@dataclass(frozen=True)
class ReconstructionResult:
    content: str
    source_artifact_id: str
    source_analysis_schema: str
    consumed: tuple[str, ...]
    output_sources: tuple[str, ...]
    source_groups: Mapping[str, tuple[str, ...]]
    skipped: tuple[dict[str, Any], ...]
    review: tuple[dict[str, Any], ...]
    statistics: Mapping[str, Any]
    play_res_x: int
    play_res_y: int
    profile: Mapping[str, Any]
    reconstructor_version: str = RECONSTRUCTOR_VERSION


@dataclass(frozen=True)
class _OutputEvent:
    output_ref: str
    source_refs: tuple[str, ...]
    section: str
    language: str | None
    start_ms: int
    end_ms: int
    ordinal: int
    record_type: str
    layer: int
    style: str
    name: str
    text: str


def reconstruct_standard_ass(
    value: Path | str | Mapping[str, Any],
    profile: AssReconstructionProfile | Mapping[str, Any] | None = None,
) -> ReconstructionResult:
    payload = load_analysis(value, allow_legacy=False)
    if payload["schema_version"] != ANALYSIS_SCHEMA_VERSION:
        raise ValueError(f"only current {ANALYSIS_SCHEMA_VERSION} JSON can be reconstructed")
    normalized = AssReconstructionProfile.from_value(profile)
    play_res_x, play_res_y = _resolution(payload, normalized)
    sections: dict[str, list[_OutputEvent]] = {section: [] for section in _SECTION_ORDER}
    review: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    grouped_refs: set[str] = set()
    source_groups: dict[str, tuple[str, ...]] = {}

    for group in payload["events"].get("semantic_groups", []):
        source_refs = tuple(str(item) for item in group.get("source_refs", ()))
        if group.get("review_required"):
            review.extend({
                "source_ref": source_ref, "reason": "semantic_group_requires_review",
                "group_id": group.get("group_id"),
            } for source_ref in source_refs)
            grouped_refs.update(source_refs)
            continue
        role = _normalize_role(str(group.get("role", "")))
        language = _normalize_language(str(group.get("language", "")))
        if role not in _SONG_ROLES or language is None:
            review.extend({
                "source_ref": source_ref, "reason": "semantic_group_classification_invalid",
                "group_id": group.get("group_id"),
            } for source_ref in source_refs)
            grouped_refs.update(source_refs)
            continue
        start_ms, end_ms = group.get("start_ms"), group.get("end_ms")
        if not _valid_time(start_ms, end_ms):
            review.extend({
                "source_ref": source_ref, "reason": "semantic_group_time_invalid",
                "group_id": group.get("group_id"),
            } for source_ref in source_refs)
            grouped_refs.update(source_refs)
            continue
        group_id = str(group["group_id"])
        output_ref = f"source-group:{group_id}"
        source_groups[group_id] = source_refs
        grouped_refs.update(source_refs)
        section = f"{role}_{language}"
        sections[section].append(_OutputEvent(
            output_ref=output_ref, source_refs=source_refs, section=section,
            language=language, start_ms=start_ms, end_ms=end_ms, ordinal=0,
            record_type="dialogue", layer=1 if language == "jp" else 0,
            style=_style_name(role, language), name="",
            text=_clean_text(str(group.get("text", ""))),
        ))

    for item in payload["events"]["items"]:
        source_ref = str(item["source_ref"])
        if source_ref in grouped_refs:
            continue
        classification = item.get("classification", {})
        event_kind = str(classification.get("event_kind", ""))
        if event_kind == "templater_control":
            skipped.append({"source_ref": source_ref, "reason": "templater_control"})
            continue
        if event_kind == "karaoke_parent":
            review.append({
                "source_ref": source_ref, "reason": "karaoke_parent_not_grouped",
                "line": item.get("line"), "ordinal": item.get("ordinal"),
            })
            continue
        plain = str(item.get("text", {}).get("plain_text", ""))
        features = item.get("text", {})
        if not plain:
            skipped.append({"source_ref": source_ref, "reason": "empty_visible_text"})
            continue
        if features.get("has_drawing"):
            skipped.append({"source_ref": source_ref, "reason": "drawing_not_reconstructed"})
            continue
        role = _role(item, normalized)
        language = _language(item, normalized)
        if role not in {"main", "note", "sign", *_SONG_ROLES}:
            role = "note" if item.get("record_type") == "comment" else "sign"
        if role in {"main", *_SONG_ROLES} and language is None:
            review.append({
                "source_ref": source_ref, "reason": "language_unresolved",
                "line": item.get("line"), "ordinal": item.get("ordinal"),
            })
            continue
        start_ms, end_ms = item.get("start_ms"), item.get("end_ms")
        if not _valid_time(start_ms, end_ms):
            review.append({
                "source_ref": source_ref, "reason": "invalid_time",
                "line": item.get("line"), "ordinal": item.get("ordinal"),
            })
            continue
        section = f"{role}_{language}" if role in {"main", *_SONG_ROLES} else role
        sections[section].append(_OutputEvent(
            output_ref=f"source:{source_ref}", source_refs=(source_ref,),
            section=section, language=language, start_ms=start_ms, end_ms=end_ms,
            ordinal=int(item.get("ordinal", 0)),
            record_type="comment" if role == "note" else "dialogue",
            layer=1 if section.endswith("_jp") else 0,
            style=_style_name(role, language),
            name=_name(item, role, language, normalized), text=_clean_text(plain),
        ))

    for events in sections.values():
        events.sort(key=lambda event: (
            event.start_ms, event.end_ms,
            _LANGUAGE_ORDER.get(event.language or "", 2), event.ordinal, event.output_ref,
        ))
    lines = _header(play_res_x, play_res_y, normalized)
    consumed: list[str] = []
    output_sources: list[str] = []
    output_counts: dict[str, int] = {}
    for section in _SECTION_ORDER:
        lines.append(_separator(section))
        for event in sections[section]:
            lines.append(_event_line(event))
            consumed.extend(event.source_refs)
            output_sources.append(event.output_ref)
        output_counts[section] = len(sections[section])
    content = "\n".join(lines) + "\n"
    source_event_count = len(payload["events"]["items"])
    return ReconstructionResult(
        content=content, source_artifact_id=str(payload["source"]["artifact_id"]),
        source_analysis_schema=str(payload["schema_version"]),
        consumed=tuple(consumed), output_sources=tuple(output_sources),
        source_groups=source_groups, skipped=tuple(skipped), review=tuple(review),
        statistics={
            "source_event_count": source_event_count,
            "consumed_source_count": len(consumed),
            "output_event_count": len(output_sources),
            "collapsed_source_count": sum(len(refs) for refs in source_groups.values()),
            "semantic_group_count": len(source_groups),
            "skipped_event_count": len(skipped), "review_event_count": len(review),
            "sections": output_counts,
        },
        play_res_x=play_res_x, play_res_y=play_res_y,
        profile=normalized.to_dict(),
    )


def encode_reconstructed(result: ReconstructionResult) -> bytes:
    return result.content.encode("utf-8")


def _resolution(payload: Mapping[str, Any], profile: AssReconstructionProfile) -> tuple[int, int]:
    raw = payload.get("script_info", {}).get("normalized", {})
    x = profile.play_res_x or _positive_int(raw.get("PlayResX")) or 1920
    y = profile.play_res_y or _positive_int(raw.get("PlayResY")) or 1080
    return x, y


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _valid_time(start: Any, end: Any) -> bool:
    return isinstance(start, int) and not isinstance(start, bool) and isinstance(
        end, int
    ) and not isinstance(end, bool) and end >= start


def _lookup(values: Mapping[str, str], key: str) -> str | None:
    expected = key.casefold()
    for candidate, value in values.items():
        if candidate.casefold() == expected:
            return value
    return None


def _normalize_role(value: str) -> str:
    lowered = value.strip().casefold()
    return "insert_song" if lowered in {"insert", "in", "insert-song"} else lowered


def _role(item: Mapping[str, Any], profile: AssReconstructionProfile) -> str:
    event_kind = str(item.get("classification", {}).get("event_kind", ""))
    if event_kind == "templater_control":
        return "templater_control"
    if item.get("record_type") == "comment" and event_kind != "karaoke_parent":
        return "note"
    style = str(item.get("style", ""))
    explicit = _lookup(profile.style_roles, style)
    if explicit:
        return _normalize_role(explicit)
    role = _normalize_role(str(item.get("classification", {}).get("content_role", "unknown")))
    if role in {"main", "note", "sign", *_SONG_ROLES}:
        return role
    if role in {"decorative", "credit"}:
        return "sign"
    return "note" if item.get("record_type") == "comment" else "sign"


def _language(item: Mapping[str, Any], profile: AssReconstructionProfile) -> str | None:
    style = str(item.get("style", ""))
    explicit = _lookup(profile.style_languages, style)
    if explicit:
        return _normalize_language(explicit)
    lowered = style.casefold()
    if re.search(r"(^|[-_. ])(cn|chs|cht|zh|sc|tc)($|[-_. ])", lowered):
        return "cn"
    if re.search(r"(^|[-_. ])(jp|jpn|ja)($|[-_. ])", lowered):
        return "jp"
    classified = str(item.get("classification", {}).get("language", ""))
    return _normalize_language(classified)


def _normalize_language(value: str) -> str | None:
    lowered = value.strip().casefold()
    if lowered in {"zh", "zh-hans", "zh-hant", "cn", "chs", "cht"}:
        return "cn"
    if lowered in {"ja", "jp", "jpn"}:
        return "jp"
    return None


def _style_name(role: str, language: str | None) -> str:
    if role == "main":
        return "Text - CN" if language == "cn" else "Text - JP"
    if role == "note":
        return "Note"
    if role == "sign":
        return "Sign"
    prefix = "IN" if role == "insert_song" else role.upper()
    return f"{prefix} - {'CN' if language == 'cn' else 'JP'}"


def _name(item: Mapping[str, Any], role: str, language: str | None,
          profile: AssReconstructionProfile) -> str:
    original = str(item.get("fields", {}).get("name", "")).strip()
    if role != "note":
        return original
    classified = str(item.get("classification", {}).get("language", "und"))
    needs_translation = language == "jp" or (
        language is None and classified in profile.translation_required_languages
    )
    marker = "注释-需翻译" if needs_translation else "注释"
    return f"{marker}｜{original}" if original else marker


def _clean_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")


def _header(x: int, y: int, profile: AssReconstructionProfile) -> list[str]:
    cn_font = profile.resolved_cn_font
    styles = [
        _style("Text - CN", cn_font, profile.cn_font_size, -1, 2,
               profile.main_margin_l, profile.main_margin_r, profile.cn_margin_v),
        _style("Text - JP", profile.jp_font, profile.jp_font_size, -1, 2,
               profile.main_margin_l, profile.main_margin_r, profile.jp_margin_v),
        _style("Note", cn_font, profile.note_font_size, 0, 2, 80, 80, 35),
        _style("Sign", cn_font, profile.sign_font_size, -1, 8, 10, 10, 10),
        _style("IN - CN", cn_font, profile.in_cn_font_size, -1, 2, 10, 10, 21),
        _style("IN - JP", profile.jp_font, profile.in_jp_font_size, -1, 8, 10, 10, 21),
        _style("OP - CN", cn_font, profile.op_cn_font_size, -1, 2, 10, 10, 21),
        _style("OP - JP", profile.jp_font, profile.op_jp_font_size, -1, 8, 10, 10, 21),
        _style("ED - CN", cn_font, profile.op_cn_font_size, -1, 2, 10, 10, 21),
        _style("ED - JP", profile.jp_font, profile.op_jp_font_size, -1, 8, 10, 10, 21),
    ]
    return [
        "[Script Info]", f"; Reconstructed from {ANALYSIS_SCHEMA_VERSION} JSON by bmlsub",
        "ScriptType: v4.00+", f"PlayResX: {x}", f"PlayResY: {y}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]", f"Format: {', '.join(_STYLE_FORMAT)}", *styles, "",
        "[Events]", f"Format: {', '.join(_EVENT_FORMAT)}",
    ]


def _style(name: str, font: str, size: int, bold: int, alignment: int,
           margin_l: int, margin_r: int, margin_v: int) -> str:
    values = (
        name, font, str(size), "&H00FFFFFF", "&H000000FF", "&H00000000",
        "&H00000000", str(bold), "0", "0", "0", "100", "100", "0", "0", "1",
        "2", "0", str(alignment), str(margin_l), str(margin_r), str(margin_v), "1",
    )
    return f"Style: {','.join(values)}"


def _separator(section: str) -> str:
    return f"Comment: 0,0:00:00.00,0:00:00.00,Note,注释,0,0,0,section:{section},{_SECTION_LABELS[section]}"


def _event_line(event: _OutputEvent) -> str:
    values = (
        str(event.layer), _ass_time(event.start_ms), _ass_time(event.end_ms), event.style,
        event.name, "0", "0", "0", event.output_ref, event.text,
    )
    prefix = "Comment" if event.record_type == "comment" else "Dialogue"
    return f"{prefix}: {','.join(values)}"


def _ass_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, remainder = divmod(remainder, 1_000)
    centiseconds = remainder // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"
