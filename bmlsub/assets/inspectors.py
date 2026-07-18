"""Bounded inspectors for external subtitle and supporting source files."""

from __future__ import annotations

import codecs
import mimetypes
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..execution.errors import ReviewRequiredError
from ..hanvert import parse_ass, read_ass
from .models import SourceAssetKind


MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_METADATA_ITEMS = 256
_TIME_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<fraction>\d{1,3})"
)
_SRT_RANGE_RE = re.compile(r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})")
_ASS_FONT_RE = re.compile(r"^Style:[^,\r\n]*,([^,\r\n]+)", re.IGNORECASE | re.MULTILINE)
_OGM_RE = re.compile(r"^CHAPTER(\d+)=(\d{2}:\d{2}:\d{2}[.,]\d{3})$", re.MULTILINE)


def inspect_source(path: Path, kind: SourceAssetKind, language: str | None) -> tuple[str, dict[str, object]]:
    if kind is SourceAssetKind.SUBTITLE:
        return inspect_subtitle(path, language)
    if kind is SourceAssetKind.FONT:
        return inspect_font(path)
    if kind is SourceAssetKind.CHAPTER:
        return inspect_chapter(path, language)
    return "source.attachment", inspect_attachment(path)


def inspect_subtitle(path: Path, language: str | None) -> tuple[str, dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix == ".ass":
        content, encoding = read_ass(path)
        document = parse_ass(path)
        event_times = _ass_event_times(content)
        fonts = list(dict.fromkeys(
            match.group(1).strip()[:128] for match in _ASS_FONT_RE.finditer(content)
            if match.group(1).strip()
        ))[:MAX_METADATA_ITEMS]
        metadata: dict[str, object] = {
            "format": "ass", "encoding": encoding,
            "event_count": len(document.events), "style_count": len(document.styles),
            "referenced_fonts": fonts,
        }
        _add_timing(metadata, event_times)
        if language:
            metadata["language"] = language
        return "source.subtitle.ass", metadata
    if suffix == ".srt":
        content, encoding = _read_bounded_text(path)
        ranges = []
        for line in content.splitlines():
            match = _SRT_RANGE_RE.match(line)
            if match:
                start, end = _time_ms(match.group(1)), _time_ms(match.group(2))
                if end < start:
                    raise ReviewRequiredError("SRT cue ends before it starts")
                ranges.append((start, end))
        if not ranges:
            raise ReviewRequiredError("SRT subtitle has no valid timed cues")
        metadata = {"format": "srt", "encoding": encoding, "cue_count": len(ranges)}
        _add_timing(metadata, ranges)
        if language:
            metadata["language"] = language
        return "source.subtitle.srt", metadata
    raise ReviewRequiredError("unsupported subtitle format; expected ASS or SRT")


def inspect_font(path: Path) -> tuple[str, dict[str, object]]:
    suffix = path.suffix.lower()
    if suffix not in {".ttf", ".otf", ".ttc"}:
        raise ReviewRequiredError("unsupported font format; expected TTF, OTF, or TTC")
    with path.open("rb") as handle:
        header = handle.read(12)
    valid = header.startswith((b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true", b"typ1"))
    if not valid:
        raise ReviewRequiredError("font file does not have a recognized sfnt signature")
    format_name = {".ttf": "truetype", ".otf": "opentype", ".ttc": "collection"}[suffix]
    return "source.font", {"format": format_name, "filename_family_hint": path.stem[:128]}


def inspect_chapter(path: Path, language: str | None) -> tuple[str, dict[str, object]]:
    content, encoding = _read_bounded_text(path)
    metadata: dict[str, object]
    if path.suffix.lower() == ".xml":
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ReviewRequiredError("chapter XML is invalid") from exc
        starts = [_time_ms(item.text or "") for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "ChapterTimeStart"]
        if not starts:
            raise ReviewRequiredError("chapter XML contains no chapter start times")
        metadata = {
            "format": "matroska_xml", "encoding": encoding,
            "chapter_count": len(starts), "first_time_ms": min(starts), "last_time_ms": max(starts),
        }
    else:
        starts = [_time_ms(match.group(2)) for match in _OGM_RE.finditer(content)]
        if not starts:
            raise ReviewRequiredError("chapter file contains no OGM chapter timestamps")
        metadata = {
            "format": "ogm", "encoding": encoding,
            "chapter_count": len(starts), "first_time_ms": min(starts), "last_time_ms": max(starts),
        }
    if language:
        metadata["language"] = language
    return "source.chapter", metadata


def inspect_attachment(path: Path) -> dict[str, object]:
    mime, _ = mimetypes.guess_type(path.name)
    return {"extension": path.suffix.lower()[:32], "mime_type": (mime or "application/octet-stream")[:128]}


def _read_bounded_text(path: Path) -> tuple[str, str]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise ReviewRequiredError("text source exceeds the bounded inspection limit")
    data = path.read_bytes()
    encodings = []
    if data.startswith(codecs.BOM_UTF8):
        encodings.append("utf-8-sig")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings.append("utf-16")
    encodings.extend(("utf-8", "utf-16", "shift_jis", "gb18030"))
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ReviewRequiredError("text source encoding could not be determined safely")


def _time_ms(value: str) -> int:
    match = _TIME_RE.search(value.strip())
    if not match:
        raise ReviewRequiredError("media timestamp is invalid")
    fraction_ms = int(match.group("fraction").ljust(3, "0"))
    return (((int(match.group("h")) * 60 + int(match.group("m"))) * 60
             + int(match.group("s"))) * 1000 + fraction_ms)


def _ass_event_times(content: str) -> list[tuple[int, int]]:
    ranges = []
    for line in content.splitlines():
        if not line.lower().startswith(("dialogue:", "comment:")):
            continue
        fields = line.split(":", 1)[1].split(",", 3)
        if len(fields) >= 3:
            try:
                ranges.append((_time_ms(fields[1]), _time_ms(fields[2])))
            except ReviewRequiredError:
                continue
    return ranges


def _add_timing(metadata: dict[str, object], ranges: list[tuple[int, int]]) -> None:
    if ranges:
        metadata["first_time_ms"] = min(start for start, _ in ranges)
        metadata["last_time_ms"] = max(end for _, end in ranges)
        metadata["duration_ms"] = max(end for _, end in ranges) - min(start for start, _ in ranges)
