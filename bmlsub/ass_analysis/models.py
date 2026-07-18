"""Lossless-enough document models for ASS analysis."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class AssDiagnostic:
    code: str
    message: str
    severity: str = "warning"
    section: str | None = None
    line: int | None = None
    field: str | None = None
    evidence: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "section": self.section,
            "line": self.line,
            "field": self.field,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class AssRecord:
    kind: str
    line_number: int
    raw: str
    key: str | None = None
    value: str | None = None
    fields: Mapping[str, str] = dataclass_field(default_factory=dict)
    parsed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "line": self.line_number,
            "raw": self.raw,
            "key": self.key,
            "value": self.value,
            "fields": dict(self.fields),
            "parsed": self.parsed,
        }


@dataclass(frozen=True)
class AssSection:
    name: str
    normalized_name: str
    header_line: int
    end_line: int
    known: bool
    records: tuple[AssRecord, ...] = ()

    def to_dict(self, *, include_records: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "normalized_name": self.normalized_name,
            "header_line": self.header_line,
            "end_line": self.end_line,
            "known": self.known,
            "record_count": len(self.records),
        }
        if include_records:
            result["records"] = [item.to_dict() for item in self.records]
        return result


@dataclass(frozen=True)
class AssStyle:
    line_number: int
    raw: str
    fields: Mapping[str, str]

    @property
    def name(self) -> str:
        return self.fields.get("name", "")

    @property
    def fontname(self) -> str:
        return self.fields.get("fontname", "")

    @property
    def bold(self) -> bool:
        return _ass_bool(self.fields.get("bold"))

    @property
    def italic(self) -> bool:
        return _ass_bool(self.fields.get("italic"))

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line_number, "raw": self.raw, "fields": dict(self.fields)}


@dataclass(frozen=True)
class AssEvent:
    line_number: int
    ordinal: int
    record_type: str
    raw: str
    fields: Mapping[str, str]

    @property
    def style(self) -> str:
        return self.fields.get("style", "")

    @property
    def text(self) -> str:
        return self.fields.get("text", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line_number,
            "ordinal": self.ordinal,
            "record_type": self.record_type,
            "raw": self.raw,
            "fields": dict(self.fields),
        }


@dataclass(frozen=True)
class AssDocument:
    path: Path
    encoding: str
    bom: bool
    newline: str
    trailing_newline: bool
    content: str
    lines: tuple[str, ...]
    sections: tuple[AssSection, ...]
    script_info: tuple[AssRecord, ...]
    project_garbage: tuple[AssRecord, ...]
    style_format: tuple[str, ...]
    event_format: tuple[str, ...]
    styles: tuple[AssStyle, ...]
    events: tuple[AssEvent, ...]
    diagnostics: tuple[AssDiagnostic, ...]
    roundtrip_safe: bool

    def section(self, name: str) -> tuple[AssSection, ...]:
        normalized = name.strip().lower()
        return tuple(item for item in self.sections if item.normalized_name == normalized)

    def to_document_dict(self) -> dict[str, Any]:
        return {
            "encoding": self.encoding,
            "bom": self.bom,
            "newline": {"\n": "lf", "\r\n": "crlf", "\r": "cr"}.get(self.newline, "none"),
            "trailing_newline": self.trailing_newline,
            "sections": [item.to_dict() for item in self.sections],
            "unknown_sections": [
                item.to_dict() for item in self.sections if not item.known
            ],
            "roundtrip_safe": self.roundtrip_safe,
        }


def _ass_bool(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return int(value.strip()) != 0
    except ValueError:
        return value.strip().lower() in {"true", "yes"}
