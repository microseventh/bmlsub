"""ASS reader with dynamic Format mappings and source line preservation."""

from __future__ import annotations

import codecs
from pathlib import Path
import re

from .models import AssDiagnostic, AssDocument, AssEvent, AssRecord, AssSection, AssStyle


MAX_ASS_BYTES = 32 * 1024 * 1024
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "shift_jis", "utf-16")
_KNOWN_SECTIONS = {
    "script info", "aegisub project garbage", "v4+ styles", "v4 styles", "events",
    "fonts", "graphics", "aegisub extradata",
}
_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def read_ass_document(path: Path | str) -> AssDocument:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"ASS file does not exist: {target}")
    data = target.read_bytes()
    if not data or len(data) > MAX_ASS_BYTES:
        raise ValueError("ASS input is empty or exceeds the bounded analysis limit")
    content, encoding = decode_ass(data)
    return parse_ass_document(content, path=target, encoding=encoding, bom=data.startswith(codecs.BOM_UTF8))


def decode_ass(data: bytes) -> tuple[str, str]:
    candidates = []
    if data.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    candidates.extend(_ENCODINGS)
    for encoding in dict.fromkeys(candidates):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("ASS input encoding could not be determined safely")


def parse_ass_document(content: str, *, path: Path | str = "__memory__.ass",
                       encoding: str = "memory", bom: bool = False) -> AssDocument:
    newline = _detect_newline(content)
    trailing_newline = content.endswith(("\n", "\r"))
    physical = tuple(content.splitlines())
    sections_data: list[dict] = []
    current: dict | None = None
    diagnostics: list[AssDiagnostic] = []
    style_format: tuple[str, ...] = ()
    event_format: tuple[str, ...] = ()
    styles: list[AssStyle] = []
    events: list[AssEvent] = []
    script_info: list[AssRecord] = []
    project_garbage: list[AssRecord] = []
    duplicate_sections: dict[str, int] = {}

    for line_number, raw in enumerate(physical, 1):
        header = _HEADER_RE.match(raw)
        if header:
            name = header.group(1).strip()
            normalized = name.lower()
            duplicate_sections[normalized] = duplicate_sections.get(normalized, 0) + 1
            current = {
                "name": name, "normalized": normalized, "header": line_number,
                "records": [],
            }
            sections_data.append(current)
            if duplicate_sections[normalized] > 1:
                diagnostics.append(AssDiagnostic(
                    "duplicate_section", "ASS section appears more than once",
                    section=name, line=line_number,
                ))
            continue
        if current is None:
            if raw.strip():
                diagnostics.append(AssDiagnostic(
                    "content_before_section", "content appears before the first ASS section",
                    line=line_number,
                ))
            continue
        section = current["normalized"]
        record = _generic_record(raw, line_number)
        current["records"].append(record)
        stripped = raw.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if section == "script info":
            script_info.append(record)
        elif section == "aegisub project garbage":
            project_garbage.append(record)
        elif section in {"v4+ styles", "v4 styles"}:
            prefix, separator, payload = raw.partition(":")
            kind = prefix.strip().lower()
            if separator and kind == "format":
                parsed_format = _parse_format(payload, section, line_number, diagnostics)
                if parsed_format:
                    style_format = parsed_format
            elif separator and kind == "style":
                if not style_format:
                    diagnostics.append(AssDiagnostic(
                        "style_before_format", "Style record appears before a usable Format",
                        section=current["name"], line=line_number,
                    ))
                else:
                    fields = _parse_fields(payload, style_format, text_last=False)
                    parsed = len(fields) == len(style_format)
                    if not parsed:
                        diagnostics.append(AssDiagnostic(
                            "style_field_count", "Style fields do not match the dynamic Format",
                            section=current["name"], line=line_number,
                            evidence={"expected": len(style_format), "actual": len(fields)},
                        ))
                    styles.append(AssStyle(
                        line_number, raw,
                        dict(zip(style_format, fields)) if parsed else {},
                    ))
        elif section == "events":
            prefix, separator, payload = raw.partition(":")
            kind = prefix.strip().lower()
            if separator and kind == "format":
                parsed_format = _parse_format(payload, section, line_number, diagnostics)
                if parsed_format:
                    event_format = parsed_format
                    if "text" not in event_format:
                        diagnostics.append(AssDiagnostic(
                            "events_text_missing", "Events Format has no Text field",
                            section=current["name"], line=line_number, severity="error",
                        ))
            elif separator and kind in {"dialogue", "comment"}:
                if not event_format:
                    diagnostics.append(AssDiagnostic(
                        "event_before_format", "Event record appears before a usable Format",
                        section=current["name"], line=line_number, severity="error",
                    ))
                    events.append(AssEvent(line_number, len(events), kind, raw, {}))
                else:
                    fields = _parse_fields(payload, event_format, text_last=True)
                    parsed = len(fields) == len(event_format)
                    if not parsed:
                        diagnostics.append(AssDiagnostic(
                            "event_field_count", "Event fields do not match the dynamic Format",
                            section=current["name"], line=line_number, severity="error",
                            evidence={"expected": len(event_format), "actual": len(fields)},
                        ))
                    events.append(AssEvent(
                        line_number, len(events), kind, raw,
                        dict(zip(event_format, fields)) if parsed else {},
                    ))

    sections: list[AssSection] = []
    for index, item in enumerate(sections_data):
        end_line = (sections_data[index + 1]["header"] - 1
                    if index + 1 < len(sections_data) else len(physical))
        sections.append(AssSection(
            item["name"], item["normalized"], item["header"], end_line,
            item["normalized"] in _KNOWN_SECTIONS, tuple(item["records"]),
        ))

    present = {item.normalized_name for item in sections}
    for required in ("script info", "v4+ styles", "events"):
        if required not in present and not (required == "v4+ styles" and "v4 styles" in present):
            diagnostics.append(AssDiagnostic(
                "required_section_missing", "required ASS section is missing",
                section=required, severity="error",
            ))
    roundtrip_safe = bool(event_format and "text" in event_format) and not any(
        item.severity == "error" for item in diagnostics
    )
    return AssDocument(
        Path(path), encoding, bom, newline, trailing_newline, content, physical,
        tuple(sections), tuple(script_info), tuple(project_garbage), style_format,
        event_format, tuple(styles), tuple(events), tuple(diagnostics), roundtrip_safe,
    )


def _generic_record(raw: str, line_number: int) -> AssRecord:
    prefix, separator, value = raw.partition(":")
    if not separator:
        return AssRecord("raw", line_number, raw, parsed=False)
    return AssRecord(
        prefix.strip().lower(), line_number, raw, prefix.strip(), value.lstrip(), parsed=True,
    )


def _parse_format(payload: str, section: str, line: int,
                  diagnostics: list[AssDiagnostic]) -> tuple[str, ...]:
    fields = tuple(item.strip().lower() for item in payload.split(",") if item.strip())
    if not fields:
        diagnostics.append(AssDiagnostic(
            "format_empty", "Format record has no fields", section=section, line=line,
            severity="error",
        ))
        return ()
    duplicates = sorted({name for name in fields if fields.count(name) > 1})
    if duplicates:
        diagnostics.append(AssDiagnostic(
            "format_duplicate_fields", "Format contains duplicate fields", section=section,
            line=line, severity="error", evidence={"fields": duplicates},
        ))
    return fields


def _parse_fields(payload: str, format_fields: tuple[str, ...], *, text_last: bool) -> list[str]:
    value = payload.lstrip()
    if text_last and "text" in format_fields:
        text_index = format_fields.index("text")
        if text_index == len(format_fields) - 1:
            return value.split(",", len(format_fields) - 1)
    return value.split(",", len(format_fields) - 1)


def _detect_newline(content: str) -> str:
    crlf = content.count("\r\n")
    lf = content.count("\n") - crlf
    cr = content.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if lf >= cr and lf:
        return "\n"
    return "\r" if cr else "\n"
