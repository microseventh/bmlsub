"""Validators for generated analysis JSON and normalized ASS files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import validate_analysis_payload
from .models import AssDocument
from .parser import read_ass_document
from .profiles import AssAnalysisProfile
from .reconstruction import ReconstructionResult
from .text import text_features


def validate_analysis_file(path: Path | str, *, source_artifact_id: str | None = None) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError("analysis output is missing or empty")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("analysis output is not valid UTF-8 JSON") from exc
    return validate_analysis_payload(payload, source_artifact_id=source_artifact_id)


def validate_normalized_ass(path: Path | str, *, source: AssDocument,
                            profile: AssAnalysisProfile) -> AssDocument:
    candidate = read_ass_document(path)
    if not candidate.roundtrip_safe:
        raise ValueError("normalized ASS cannot be parsed safely")
    if tuple(item.name for item in source.styles) != tuple(item.name for item in candidate.styles):
        raise ValueError("normalized ASS changed Style identity or order")
    if len(source.events) != len(candidate.events):
        raise ValueError("normalized ASS changed the event count")
    for before, after in zip(source.events, candidate.events):
        if (before.record_type, dict(before.fields)) != (after.record_type, dict(after.fields)):
            raise ValueError("normalized ASS changed Events semantics")
    source_unknown = [(item.name, tuple(record.raw for record in item.records))
                      for item in source.sections if not item.known]
    candidate_unknown = [(item.name, tuple(record.raw for record in item.records))
                         for item in candidate.sections if not item.known]
    if source_unknown != candidate_unknown:
        raise ValueError("normalized ASS changed unknown section content")
    expected_metadata = {key.casefold(): value for key, value in profile.metadata.updates.items()}
    candidate_info = {record.key.casefold(): record.value for record in candidate.script_info
                      if record.key is not None}
    if any(candidate_info.get(key) != value for key, value in expected_metadata.items()):
        raise ValueError("normalized ASS did not apply the metadata policy")
    return candidate


def validate_reconstructed_ass(path: Path | str, *, result: ReconstructionResult) -> AssDocument:
    candidate = read_ass_document(path)
    if not candidate.roundtrip_safe:
        raise ValueError("reconstructed ASS cannot be parsed safely")
    required_styles = {
        "Text - CN", "Text - JP", "Note", "Sign", "IN - CN", "IN - JP",
        "OP - CN", "OP - JP", "ED - CN", "ED - JP",
    }
    styles = {item.name: item for item in candidate.styles}
    if set(styles) != required_styles:
        raise ValueError("reconstructed ASS does not contain the standard Style set")
    profile = result.profile
    cn_font = profile.get("cn_font") or (
        "PingFang TC" if profile.get("chinese_variant") == "traditional" else "PingFang SC"
    )
    expected_styles = {
        "Text - CN": (cn_font, profile["cn_font_size"], "-1", "2", profile["main_margin_l"], profile["main_margin_r"], profile["cn_margin_v"]),
        "Text - JP": (profile["jp_font"], profile["jp_font_size"], "-1", "2", profile["main_margin_l"], profile["main_margin_r"], profile["jp_margin_v"]),
        "Note": (cn_font, profile["note_font_size"], "0", "2", 80, 80, 35),
        "Sign": (cn_font, profile["sign_font_size"], "-1", "8", 10, 10, 10),
        "IN - CN": (cn_font, profile["in_cn_font_size"], "-1", "2", 10, 10, 21),
        "IN - JP": (profile["jp_font"], profile["in_jp_font_size"], "-1", "8", 10, 10, 21),
        "OP - CN": (cn_font, profile["op_cn_font_size"], "-1", "2", 10, 10, 21),
        "OP - JP": (profile["jp_font"], profile["op_jp_font_size"], "-1", "8", 10, 10, 21),
        "ED - CN": (cn_font, profile["op_cn_font_size"], "-1", "2", 10, 10, 21),
        "ED - JP": (profile["jp_font"], profile["op_jp_font_size"], "-1", "8", 10, 10, 21),
    }
    for name, expected in expected_styles.items():
        fields = styles[name].fields
        actual = (
            fields.get("fontname"), int(fields.get("fontsize", "-1")),
            fields.get("bold"), fields.get("alignment"),
            int(fields.get("marginl", "-1")), int(fields.get("marginr", "-1")),
            int(fields.get("marginv", "-1")),
        )
        if actual != expected:
            raise ValueError(f"reconstructed ASS Style {name} is invalid")
    if int(styles["Text - CN"].fields.get("marginv", "-1")) <= int(
        styles["Text - JP"].fields.get("marginv", "-1")
    ):
        raise ValueError("reconstructed Chinese text must be positioned above Japanese text")
    expected_sections = [
        "main_cn", "main_jp", "note", "sign", "insert_song_cn", "insert_song_jp",
        "op_cn", "op_jp", "ed_cn", "ed_jp",
    ]
    separators = [
        item for item in candidate.events
        if item.record_type == "comment" and item.fields.get("effect", "").startswith("section:")
    ]
    if [item.fields.get("effect") for item in separators] != [
        f"section:{name}" for name in expected_sections
    ]:
        raise ValueError("reconstructed ASS section separators are invalid")
    source_events = [
        item for item in candidate.events
        if item.fields.get("effect", "").startswith(("source:", "source-group:"))
    ]
    if len(source_events) != result.statistics["output_event_count"]:
        raise ValueError("reconstructed ASS output event count is inconsistent")
    output_refs = [item.fields.get("effect", "") for item in source_events]
    if output_refs != list(result.output_sources) or len(output_refs) != len(set(output_refs)):
        raise ValueError("reconstructed ASS source Event mapping is inconsistent")
    expanded_sources = []
    for output_ref in output_refs:
        if output_ref.startswith("source-group:"):
            group_id = output_ref.removeprefix("source-group:")
            if group_id not in result.source_groups:
                raise ValueError("reconstructed ASS references an unknown source group")
            expanded_sources.extend(result.source_groups[group_id])
        else:
            expanded_sources.append(output_ref.removeprefix("source:"))
    if expanded_sources != list(result.consumed) or len(expanded_sources) != len(set(expanded_sources)):
        raise ValueError("reconstructed ASS expanded source mapping is inconsistent")
    section_order = {name: index for index, name in enumerate(expected_sections)}
    current_section = -1
    ordered_sections = {
        name: [] for name in (
            "main_cn", "main_jp", "insert_song_cn", "insert_song_jp",
            "op_cn", "op_jp", "ed_cn", "ed_jp",
        )
    }
    classified_sections = {
        "main_cn": ("Text - CN", "0"),
        "main_jp": ("Text - JP", "1"),
        "insert_song_cn": ("IN - CN", "0"),
        "insert_song_jp": ("IN - JP", "1"),
        "op_cn": ("OP - CN", "0"),
        "op_jp": ("OP - JP", "1"),
        "ed_cn": ("ED - CN", "0"),
        "ed_jp": ("ED - JP", "1"),
    }
    for event in candidate.events:
        effect = event.fields.get("effect", "")
        if event.record_type == "comment" and effect.startswith("section:"):
            section = effect.removeprefix("section:")
            current_section = section_order[section]
            continue
        if not effect.startswith(("source:", "source-group:")):
            continue
        if current_section < 0:
            raise ValueError("reconstructed ASS event appears before a section separator")
        section = expected_sections[current_section]
        features = text_features(event.text)
        if section in classified_sections:
            expected_style, expected_layer = classified_sections[section]
            if event.record_type != "dialogue" or event.style != expected_style:
                raise ValueError("reconstructed language section contains the wrong event type or Style")
            if event.fields.get("layer") != expected_layer:
                raise ValueError("reconstructed language section Layer is invalid")
            if (
                section.startswith("main_") or section.startswith("insert_song_") or
                section.startswith("op_") or section.startswith("ed_")
            ) and (features["has_position"] or features["has_animation"] or
                   features["has_clip"] or features["has_drawing"]):
                raise ValueError("reconstructed text contains disallowed effect tags")
            ordered_sections[section].append(
                (event.fields.get("start", ""), event.fields.get("end", ""))
            )
        if section == "note":
            if event.record_type != "comment":
                raise ValueError("reconstructed notes must be disabled Comment events")
            if not event.fields.get("name", "").startswith("注释"):
                raise ValueError("reconstructed note does not use the annotation Name marker")
    if any(order != sorted(order) for order in ordered_sections.values()):
        raise ValueError("reconstructed language section is not sorted by time")
    if result.statistics["source_event_count"] != (
        result.statistics["consumed_source_count"] + result.statistics["skipped_event_count"] +
        result.statistics["review_event_count"]
    ):
        raise ValueError("reconstruction accounting is incomplete")
    return candidate
