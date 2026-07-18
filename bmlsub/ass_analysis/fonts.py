"""Effective font-state collection and registered-font resolution."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from fontTools.ttLib import TTCollection, TTFont

from ..state.models import ArtifactRecord
from .models import AssDocument
from .text import iter_effective_text_runs


@dataclass
class _Requirement:
    family: str
    bold: bool
    italic: bool
    sources: set[str] = field(default_factory=set)
    styles: set[str] = field(default_factory=set)
    source_refs: set[str] = field(default_factory=set)
    lines: set[int] = field(default_factory=set)
    characters: set[int] = field(default_factory=set)
    drawing: bool = False


def analyze_fonts(document: AssDocument, event_ids: dict[int, dict[str, Any]],
                  artifacts: Iterable[ArtifactRecord] = ()) -> dict[str, Any]:
    style_lookup = {
        item.name: (item.fontname, item.bold, item.italic)
        for item in document.styles if item.name
    }
    requirements: dict[tuple[str, bool, bool], _Requirement] = {}
    references: list[dict[str, Any]] = []
    used_styles = {event.style for event in document.events if event.record_type == "dialogue"}
    for style in document.styles:
        if style.name not in used_styles or not style.fontname:
            continue
        key = (style.fontname, style.bold, style.italic)
        requirement = requirements.setdefault(key, _Requirement(*key))
        requirement.sources.add("style")
        requirement.styles.add(style.name)
        references.append({
            "source": "style", "font": style.fontname, "bold": style.bold,
            "italic": style.italic, "style": style.name, "line": style.line_number,
        })
    for event in document.events:
        if event.record_type != "dialogue" or not event.fields:
            continue
        base = style_lookup.get(event.style)
        if base is None:
            continue
        seen_event_refs: set[tuple[str, bool, bool]] = set()
        for font, bold, italic, drawing, value in iter_effective_text_runs(
            event.text, initial_font=base[0], initial_bold=base[1], initial_italic=base[2],
            style_lookup=style_lookup, base_style=event.style,
        ):
            if not font:
                continue
            key = (font, bold, italic)
            requirement = requirements.setdefault(key, _Requirement(*key))
            requirement.styles.add(event.style)
            source_ref = event_ids[event.ordinal]["source_ref"]
            requirement.source_refs.add(source_ref)
            requirement.lines.add(event.line_number)
            if drawing:
                requirement.drawing = True
            else:
                requirement.characters.update(_visible_codepoints(value))
            if key != base and key not in seen_event_refs:
                reference = {
                    "source": "override", "font": font, "bold": bold, "italic": italic,
                    "style": event.style, "line": event.line_number,
                    "ordinal": event.ordinal,
                }
                reference["source_ref"] = source_ref
                references.append(reference)
                requirement.sources.add("override")
                seen_event_refs.add(key)
    faces = _load_font_faces(tuple(artifacts))
    resolutions = []
    requirement_items = []
    for item in sorted(requirements.values(), key=lambda value: (
        _normalize_name(value.family), value.bold, value.italic
    )):
        candidates = _resolve_faces(item, faces)
        covered: set[int] = set()
        for candidate in candidates:
            covered.update(candidate["codepoints"])
        missing = sorted(item.characters - covered) if candidates else sorted(item.characters)
        variant_match = any(candidate["bold"] == item.bold and candidate["italic"] == item.italic
                            for candidate in candidates)
        requirement_items.append({
            "family": item.family,
            "normalized_name": _normalize_name(item.family),
            "bold": item.bold, "italic": item.italic,
            "sources": sorted(item.sources), "styles": sorted(item.styles),
            "source_refs": sorted(item.source_refs), "lines": sorted(item.lines),
            "character_count": len(item.characters),
            "characters": "".join(chr(value) for value in sorted(item.characters)),
            "codepoints": [f"U+{value:04X}" for value in sorted(item.characters)],
            "drawing": item.drawing,
        })
        resolutions.append({
            "family": item.family, "bold": item.bold, "italic": item.italic,
            "status": ("missing_font" if not candidates else
                       "missing_variant" if not variant_match else
                       "missing_glyph" if missing else "resolved"),
            "artifact_ids": sorted({candidate["artifact_id"] for candidate in candidates}),
            "faces": [{key: candidate[key] for key in (
                "artifact_id", "family", "subfamily", "full_name", "postscript_name",
                "bold", "italic", "font_index",
            )} for candidate in candidates],
            "missing_glyphs": [f"U+{value:04X}" for value in missing],
            "fallback_risk": not candidates or not variant_match or bool(missing),
        })
    return {
        "references": references,
        "requirements": requirement_items,
        "resolution": resolutions,
        "registered_font_count": len(tuple(artifacts)),
        "face_count": len(faces),
    }


def _load_font_faces(artifacts: tuple[ArtifactRecord, ...]) -> list[dict[str, Any]]:
    faces: list[dict[str, Any]] = []
    for artifact in artifacts:
        try:
            if artifact.path.suffix.lower() == ".ttc":
                collection = TTCollection(artifact.path, lazy=True)
                fonts = collection.fonts
            else:
                fonts = [TTFont(artifact.path, lazy=True)]
            for index, font in enumerate(fonts):
                names = _font_names(font)
                style_text = " ".join(filter(None, (
                    names["subfamily"], names["full_name"], names["postscript_name"],
                ))).casefold()
                faces.append({
                    "artifact_id": artifact.artifact_id, "path": str(artifact.path),
                    "font_index": index, **names,
                    "bold": "bold" in style_text,
                    "italic": "italic" in style_text or "oblique" in style_text,
                    "codepoints": set(font.getBestCmap() or {}),
                    "names": {_normalize_name(value) for value in names.values() if value},
                })
                font.close()
        except Exception:
            continue
    return faces


def _font_names(font: TTFont) -> dict[str, str]:
    table = font["name"]
    return {
        "family": _best_name(table, 16) or _best_name(table, 1),
        "subfamily": _best_name(table, 17) or _best_name(table, 2),
        "full_name": _best_name(table, 4),
        "postscript_name": _best_name(table, 6),
    }


def _best_name(table, name_id: int) -> str:
    values = []
    for record in table.names:
        if record.nameID == name_id:
            try:
                values.append(record.toUnicode().strip())
            except UnicodeDecodeError:
                continue
    return values[0] if values else ""


def _resolve_faces(requirement: _Requirement, faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = _normalize_name(requirement.family)
    return [item for item in faces if expected in item["names"]]


def _normalize_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _visible_codepoints(value: str) -> set[int]:
    value = value.replace(r"\N", "").replace(r"\n", "").replace(r"\h", " ")
    return {ord(character) for character in value if character not in "\r\n"}
