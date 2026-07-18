"""Controlled Script Info and Aegisub Project Garbage serialization."""

from __future__ import annotations

from dataclasses import dataclass

from .models import AssDocument
from .profiles import AssAnalysisProfile


@dataclass(frozen=True)
class SerializationResult:
    content: str
    changes: tuple[dict, ...]


def serialize_normalized(document: AssDocument, profile: AssAnalysisProfile) -> SerializationResult:
    if not document.roundtrip_safe:
        raise ValueError("ASS document is not safe to normalize")
    replacements: dict[int, str] = {}
    removals: set[int] = set()
    changes: list[dict] = []
    inserts, insert_before = _metadata_operations(document, profile, replacements, changes)
    _garbage_operations(document, profile, removals, changes)
    lines = [replacements.get(index, value) for index, value in enumerate(document.lines)
             if index not in removals]
    if inserts and insert_before is not None:
        adjusted = sum(1 for index in range(insert_before) if index not in removals)
        lines[adjusted:adjusted] = inserts
    content = document.newline.join(lines)
    if document.trailing_newline:
        content += document.newline
    return SerializationResult(content, tuple(changes))


def encode_normalized(result: SerializationResult, document: AssDocument) -> bytes:
    encoding = document.encoding
    if encoding == "memory":
        encoding = "utf-8-sig" if document.bom else "utf-8"
    return result.content.encode(encoding)


def _metadata_operations(document: AssDocument, profile: AssAnalysisProfile,
                         replacements: dict[int, str], changes: list[dict]):
    sections = document.section("script info")
    if not sections:
        return [], None
    section = sections[-1]
    key_lines: dict[str, tuple[int, str, str]] = {}
    for record in document.script_info:
        if record.key is not None:
            key_lines[record.key.casefold()] = (
                record.line_number - 1, record.key, record.value or "",
            )
    inserts: list[str] = []
    for key, value in profile.metadata.updates.items():
        existing = key_lines.get(key.casefold())
        if existing:
            index, original_key, old_value = existing
            replacements[index] = f"{original_key}: {value}"
            changes.append({"section": "Script Info", "field": original_key,
                            "action": "update", "old_value": old_value, "new_value": value})
        else:
            inserts.append(f"{key}: {value}")
            changes.append({"section": "Script Info", "field": key,
                            "action": "insert", "old_value": None, "new_value": value})
    return inserts, section.end_line


def _garbage_operations(document: AssDocument, profile: AssAnalysisProfile,
                        removals: set[int], changes: list[dict]) -> None:
    from .analyzer import analyze_project_garbage

    _, decisions = analyze_project_garbage(document, profile)
    for item in decisions:
        if item["action"] != "remove":
            continue
        removals.add(item["line"] - 1)
        changes.append({"section": "Aegisub Project Garbage", "field": item["field"],
                        "action": "remove", "old_value": item["value"], "new_value": None})
