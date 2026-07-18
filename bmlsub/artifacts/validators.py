"""Artifact validation helpers."""

from __future__ import annotations

from pathlib import Path

from ..hanvert import AssDocument, parse_ass, parse_ass_content, read_ass


def validate_nonempty_file(path: Path | str) -> None:
    target = Path(path)
    if not target.is_file():
        raise ValueError(f"output is not a regular file: {target}")
    if target.stat().st_size == 0:
        raise ValueError(f"output is empty: {target}")


def _event_count(document: AssDocument, event_type: str) -> int:
    return sum(event.event_type == event_type for event in document.events)


def _non_event_sections(content: str) -> str:
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    in_events = False
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = stripped == "[events]"
            if in_events:
                continue
        if not in_events:
            result.append(line)
    return "".join(result)


def validate_ass_file(path: Path | str) -> None:
    """Require a readable, nonempty ASS file with a usable Events section."""

    validate_nonempty_file(path)
    document = parse_ass(path)
    required = {"start", "end", "style", "text"}
    actual = {name.lower() for name in document.event_format}
    missing = sorted(required - actual)
    if missing:
        raise ValueError(f"ASS Events Format is missing required fields: {', '.join(missing)}")


def validate_ass_conversion(source: Path | str, candidate: Path | str, *,
                            allow_full_file: bool = False) -> None:
    """Validate that conversion preserved the ASS structure it must not alter."""

    validate_ass_file(candidate)
    source_content, source_encoding = read_ass(source)
    candidate_content, candidate_encoding = read_ass(candidate)
    source_doc = parse_ass_content(source_content, source, encoding=source_encoding)
    candidate_doc = parse_ass_content(candidate_content, candidate, encoding=candidate_encoding)
    if [name.lower() for name in source_doc.event_format] != [name.lower() for name in candidate_doc.event_format]:
        raise ValueError("ASS Events Format changed during conversion")
    for event_type in ("Dialogue", "Comment"):
        if _event_count(source_doc, event_type) != _event_count(candidate_doc, event_type):
            raise ValueError(f"ASS {event_type} count changed during conversion")
    if not allow_full_file:
        if source_doc.styles != candidate_doc.styles:
            raise ValueError("ASS style names changed during conversion")
        if _non_event_sections(source_content) != _non_event_sections(candidate_content):
            raise ValueError("ASS non-Events structure changed during conversion")
