"""Safe ASS-aware Simplified-to-Traditional Chinese conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Iterable

import requests

from .execution.errors import BmlsubError, ErrorCode, ReviewRequiredError


ASS_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "shift-jis")
ASS_RULE_VERSION = "ass-aware-v2"
_OVERRIDE_RE = re.compile(r"\{[^{}]*\}")
_TOKEN_RE = re.compile(r"(\{[^{}]*\}|\\[Nnh])")
_DRAWING_MODE_RE = re.compile(r"\\p(\d+)", re.IGNORECASE)
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")
_SIMPLIFIED_HINT_RE = re.compile(r"[这测试纯属虚构里面东西为后发台书车门复云广开关]")
_STYLE_TOKEN_RE = re.compile(r"(?:^|[\s_.-])([A-Z]+)(?=$|[\s_.-])", re.IGNORECASE)
_ZH_STYLE_TOKENS = {"cn", "chs", "zh"}
_JA_STYLE_TOKENS = {"jp", "jpn", "ja"}
_UNIT_RE = re.compile(r"\[\[BMLS:([A-Za-z0-9_.-]+)\]\](.*?)\[\[/BMLS:\1\]\]", re.DOTALL)


ConverterProvider = Callable[[str, str, str, int], str]


@dataclass
class _Token:
    value: str
    kind: str
    drawing: bool = False


@dataclass
class _ParsedEvent:
    line_number: int
    event_type: str
    prefix: str
    fields: list[str]
    indexes: dict[str, int]
    raw_text: str
    tokens: list[_Token]

    @property
    def style(self) -> str:
        index = self.indexes.get("style")
        return self.fields[index] if index is not None else ""

    @property
    def text(self) -> str:
        return strip_ass_tags(self.raw_text)

    def render(self) -> str:
        self.fields[self.indexes["text"]] = "".join(token.value for token in self.tokens)
        return f"{self.prefix}{','.join(self.fields)}"


@dataclass
class AssDocument:
    path: Path
    encoding: str
    content: str
    lines: list[str]
    event_format: list[str]
    styles: list[str]
    events: list[_ParsedEvent]


@dataclass
class _ConversionUnit:
    unit_id: str
    event: _ParsedEvent
    tokens: list[_Token]
    source: str
    direct_token: _Token | None = None


@dataclass(frozen=True)
class HanvertResult:
    content: str
    conversion_mode: str
    converted_events: int = 0
    converted_units: int = 0
    length_changed_events: int = 0
    skipped_mixed_groups: int = 0
    no_op_reason: str | None = None


def read_ass(path: Path | str) -> tuple[str, str]:
    target = Path(path)
    raw = target.read_bytes()
    for encoding in ASS_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"cannot decode ASS file: {target}")


def _tokenize_ass_text(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    drawing_mode = 0
    cursor = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > cursor:
            value = text[cursor:match.start()]
            tokens.append(_Token(value, "drawing" if drawing_mode else "text", bool(drawing_mode)))
        value = match.group(0)
        if value.startswith("{"):
            tokens.append(_Token(value, "tag"))
            modes = _DRAWING_MODE_RE.findall(value)
            if modes:
                drawing_mode = int(modes[-1])
        else:
            tokens.append(_Token(value, "escape"))
        cursor = match.end()
    if cursor < len(text):
        value = text[cursor:]
        tokens.append(_Token(value, "drawing" if drawing_mode else "text", bool(drawing_mode)))
    return tokens


def strip_ass_tags(text: str) -> str:
    parts: list[str] = []
    for token in _tokenize_ass_text(text):
        if token.kind == "text":
            parts.append(token.value)
        elif token.kind == "escape":
            parts.append(" " if token.value == r"\h" else "\n")
    return "".join(parts)


def parse_ass_content(content: str, path: Path | str = "__memory__.ass", *, encoding: str = "memory") -> AssDocument:
    lines = content.splitlines(keepends=True)
    in_styles = False
    in_events = False
    style_format: list[str] = []
    event_format: list[str] = []
    styles: list[str] = []
    events: list[_ParsedEvent] = []
    has_events = False

    for line_number, physical_line in enumerate(lines, 1):
        line = physical_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            in_styles = section in {"v4+ styles", "v4 styles"}
            in_events = section == "events"
            has_events = has_events or in_events
            continue
        if in_styles:
            if stripped.lower().startswith("format:"):
                style_format = [item.strip().lower() for item in stripped.split(":", 1)[1].split(",")]
            elif stripped.lower().startswith("style:") and style_format:
                values = stripped.split(":", 1)[1].lstrip().split(",", len(style_format) - 1)
                if "name" in style_format and len(values) == len(style_format):
                    styles.append(values[style_format.index("name")])
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            event_format = [item.strip() for item in stripped.split(":", 1)[1].split(",")]
            continue
        prefix, separator, payload = line.partition(":")
        event_type = prefix.strip().lower()
        if not separator or event_type not in {"dialogue", "comment"}:
            continue
        if not event_format:
            raise ReviewRequiredError("ASS Events Format is missing or appears after event data")
        fields = payload.lstrip().split(",", len(event_format) - 1)
        if len(fields) != len(event_format):
            raise ReviewRequiredError("ASS event fields do not match Events Format")
        indexes = {name.lower(): index for index, name in enumerate(event_format)}
        if "text" not in indexes:
            raise ReviewRequiredError("ASS Events Format has no Text field")
        raw_text = fields[indexes["text"]]
        events.append(_ParsedEvent(
            line_number, event_type.capitalize(),
            f"{prefix}:{payload[:len(payload) - len(payload.lstrip())]}",
            fields, indexes, raw_text, _tokenize_ass_text(raw_text),
        ))

    if not has_events:
        raise ReviewRequiredError("ASS file has no Events section")
    if not event_format:
        raise ReviewRequiredError("ASS Events Format is missing")
    return AssDocument(Path(path), encoding, content, lines, event_format, styles, events)


def parse_ass(path: Path | str) -> AssDocument:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"ASS file does not exist: {target}")
    content, encoding = read_ass(target)
    return parse_ass_content(content, target, encoding=encoding)


def classify_ass_language(style: str, text: str) -> str:
    has_han = bool(_HAN_RE.search(text))
    has_kana = bool(_KANA_RE.search(text))
    has_simplified_hint = bool(_SIMPLIFIED_HINT_RE.search(text))
    has_han_only_line = any(_HAN_RE.search(part) and not _KANA_RE.search(part) for part in text.splitlines() or [text])
    if has_kana and (has_simplified_hint or has_han_only_line):
        return "mixed"
    style_tokens = {match.group(1).lower() for match in _STYLE_TOKEN_RE.finditer(style)}
    if has_kana and style_tokens & _ZH_STYLE_TOKENS:
        return "mixed"
    if style_tokens & _ZH_STYLE_TOKENS:
        return "zh"
    if style_tokens & _JA_STYLE_TOKENS:
        return "ja"
    if has_kana:
        return "ja"
    if has_han:
        return "zh"
    return "other"


def fanhuaji_provider(text: str, converter: str, api_url: str, timeout: int) -> str:
    try:
        response = requests.post(api_url, data={"text": text, "converter": converter}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as exc:
        raise BmlsubError(
            "subtitle conversion provider request failed",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            details={"exception_type": type(exc).__name__},
        ) from exc
    except ValueError as exc:
        raise BmlsubError(
            "subtitle conversion provider returned invalid JSON",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
        ) from exc
    if payload.get("code") != 0:
        raise BmlsubError(
            "subtitle conversion provider returned an error",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
        )
    converted = (payload.get("data") or {}).get("text")
    if not isinstance(converted, str):
        raise BmlsubError(
            "subtitle conversion provider response has no text",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
        )
    return converted


def _visible_text_tokens(tokens: Iterable[_Token]) -> list[_Token]:
    return [token for token in tokens if token.kind == "text" and token.value]


def _token_groups(tokens: list[_Token]) -> list[list[_Token]]:
    groups: list[list[_Token]] = []
    current: list[_Token] = []
    for token in tokens:
        if token.kind == "escape" and token.value in {r"\N", r"\n"}:
            if current:
                groups.append(current)
                current = []
        elif token.kind != "drawing":
            current.append(token)
    if current:
        groups.append(current)
    return groups


def _group_unit(event: _ParsedEvent, tokens: list[_Token], unit_id: str) -> _ConversionUnit | None:
    source = "".join(token.value for token in _visible_text_tokens(tokens))
    if not source or not _HAN_RE.search(source):
        return None
    return _ConversionUnit(unit_id, event, tokens, source)


def _mixed_units(event: _ParsedEvent, tokens: list[_Token], prefix: str) -> list[_ConversionUnit]:
    units: list[_ConversionUnit] = []
    sequence = 0
    for token in list(_visible_text_tokens(tokens)):
        if not _HAN_RE.search(token.value):
            continue
        if not _KANA_RE.search(token.value):
            units.append(_ConversionUnit(f"{prefix}.m{sequence}", event, [token], token.value, token))
            sequence += 1
            continue
        pieces = [piece for piece in re.split(f"({_KANA_RE.pattern}+)", token.value) if piece]
        replacements = [_Token(piece, "text") for piece in pieces]
        group_index = tokens.index(token)
        tokens[group_index:group_index + 1] = replacements
        event_index = event.tokens.index(token)
        event.tokens[event_index:event_index + 1] = replacements
        for replacement in replacements:
            if _HAN_RE.search(replacement.value) and not _KANA_RE.search(replacement.value):
                units.append(_ConversionUnit(
                    f"{prefix}.m{sequence}", event, [replacement], replacement.value, replacement,
                ))
                sequence += 1
    return units


def _regroup_text(tokens: list[_Token], source: str, converted: str) -> bool:
    text_tokens = _visible_text_tokens(tokens)
    if sum(len(token.value) for token in text_tokens) != len(source):
        raise BmlsubError("ASS text token mapping changed unexpectedly", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    length_changed = len(source) != len(converted)
    if length_changed and len(text_tokens) > 1:
        raise BmlsubError(
            "converted text length changed across multiple ASS tag ranges",
            code=ErrorCode.OUTPUT_VALIDATION_FAILED,
        )
    if length_changed:
        text_tokens[0].value = converted
        return True
    cursor = 0
    for token in text_tokens:
        end = cursor + len(token.value)
        token.value = converted[cursor:end]
        cursor = end
    return False


def _encode_units(units: list[_ConversionUnit]) -> str:
    return "\n".join(f"[[BMLS:{unit.unit_id}]]{unit.source}[[/BMLS:{unit.unit_id}]]" for unit in units)


def _decode_units(response: str, expected_ids: set[str]) -> dict[str, str]:
    matches = list(_UNIT_RE.finditer(response))
    values: dict[str, str] = {}
    for match in matches:
        unit_id = match.group(1)
        if unit_id in values:
            raise BmlsubError("conversion response contains a duplicate unit ID", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        values[unit_id] = match.group(2)
    if set(values) != expected_ids or len(matches) != len(expected_ids):
        raise BmlsubError("conversion response unit IDs do not match the request", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    residue = _UNIT_RE.sub("", response).strip()
    if residue:
        raise BmlsubError("conversion response contains unframed content", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
    return values


def convert_ass(
    content: str,
    *,
    converter: str = "Taiwan",
    api_url: str = "https://api.zhconvert.org/convert",
    timeout: int = 60,
    full_file: bool = False,
    provider: ConverterProvider | None = None,
) -> HanvertResult:
    convert = provider or fanhuaji_provider
    if full_file:
        return HanvertResult(convert(content, converter, api_url, timeout), "full_file")

    document = parse_ass_content(content)
    units: list[_ConversionUnit] = []
    skipped_mixed_groups = 0
    for event in document.events:
        if event.event_type != "Dialogue":
            continue
        language = classify_ass_language(event.style, event.text)
        for group_index, group in enumerate(_token_groups(event.tokens)):
            prefix = f"L{event.line_number}.G{group_index}"
            group_text = "".join(token.value for token in _visible_text_tokens(group))
            group_language = classify_ass_language("", group_text)
            if language == "zh":
                unit = _group_unit(event, group, prefix)
                if unit is not None:
                    units.append(unit)
            elif language == "mixed" and group_language == "zh":
                unit = _group_unit(event, group, prefix)
                if unit is not None:
                    units.append(unit)
            elif language == "mixed" and group_language == "mixed":
                mixed = _mixed_units(event, group, prefix)
                units.extend(mixed)
                if not mixed:
                    skipped_mixed_groups += 1

    if not units:
        dialogue_events = [event for event in document.events if event.event_type == "Dialogue"]
        dialogue_text = "\n".join(event.text for event in dialogue_events)
        uncertain = any(
            classify_ass_language(event.style, event.text) == "mixed"
            or (
                classify_ass_language(event.style, event.text) == "ja"
                and _HAN_RE.search(event.text)
                and not _KANA_RE.search(event.text)
            )
            for event in dialogue_events
        )
        if _HAN_RE.search(dialogue_text) and uncertain:
            raise ReviewRequiredError(
                "ASS contains Han text but no reliable conversion candidates",
                details={"reason": "no_reliable_candidates"},
            )
        return HanvertResult(content, "ass_aware", no_op_reason="no_chinese_dialogue")

    request = _encode_units(units)
    converted_by_id = _decode_units(convert(request, converter, api_url, timeout), {unit.unit_id for unit in units})
    changed_events: dict[int, _ParsedEvent] = {}
    length_changed_lines: set[int] = set()
    for unit in units:
        converted = converted_by_id[unit.unit_id]
        if unit.direct_token is not None:
            length_changed = len(unit.source) != len(converted)
            unit.direct_token.value = converted
        else:
            length_changed = _regroup_text(unit.tokens, unit.source, converted)
        changed_events[unit.event.line_number] = unit.event
        if length_changed:
            length_changed_lines.add(unit.event.line_number)

    for line_number, event in changed_events.items():
        original = document.lines[line_number - 1]
        newline = original[len(original.rstrip("\r\n")):]
        document.lines[line_number - 1] = event.render() + newline
    return HanvertResult(
        "".join(document.lines), "ass_aware", len(changed_events), len(units),
        len(length_changed_lines), skipped_mixed_groups,
    )


def convert_plain_text(
    text: str, *, converter: str = "Taiwan",
    api_url: str = "https://api.zhconvert.org/convert", timeout: int = 60,
    provider: ConverterProvider | None = None,
) -> str:
    """Convert one plain-text label without ASS parsing or file side effects."""
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("plain text conversion input is invalid")
    converted = (provider or fanhuaji_provider)(text, converter, api_url, timeout)
    if not isinstance(converted, str) or not converted.strip() or "\x00" in converted:
        raise ValueError("plain text conversion returned invalid text")
    return converted.strip()


# Legacy-friendly name without the unsafe fallback behavior.
def convert_ass_with_fanhuaji(content: str, **kwargs) -> tuple[str, dict]:
    kwargs.pop("fallback_to_full_file", None)
    result = convert_ass(content, **kwargs)
    return result.content, {
        "converted_events": result.converted_events,
        "converted_units": result.converted_units,
        "length_changed_events": result.length_changed_events,
        "skipped_mixed_groups": result.skipped_mixed_groups,
        "conversion_mode": result.conversion_mode,
        "fallback_reason": None,
        "no_op_reason": result.no_op_reason,
    }
