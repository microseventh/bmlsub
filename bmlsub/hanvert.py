"""ASS text extraction, language analysis, and Fanhuaji conversion.

The parser is intentionally small and Python 3.10 compatible. It understands
ASS event formats, override blocks, escaped line breaks, and drawing mode well
enough to keep non-dialogue content out of text analysis and conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterable

import requests


_ASS_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "shift-jis")
_OVERRIDE_RE = re.compile(r"\{[^{}]*\}")
_TOKEN_RE = re.compile(r"(\{[^{}]*\}|\\[Nnh])")
_DRAWING_MODE_RE = re.compile(r"\\p(\d+)", re.IGNORECASE)
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_KANA_RE = re.compile(r"[ぁ-ゖァ-ヺー]")
_SIMPLIFIED_HINT_RE = re.compile(r"[这测试纯属虚构里面东西为后发台书车门复云广开关]")
_STYLE_TOKEN_RE = re.compile(r"(?:^|[\s_.-])([A-Z]+)(?=$|[\s_.-])", re.IGNORECASE)
_ZH_STYLE_TOKENS = {"cn", "chs", "zh"}
_JA_STYLE_TOKENS = {"jp", "jpn", "ja"}


class HanvertConversionError(Exception):
    """ASS-aware Fanhuaji conversion failed."""


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
        return self._field("style")

    @property
    def text(self) -> str:
        return strip_ass_tags(self.raw_text)

    def _field(self, name: str) -> str:
        index = self.indexes.get(name.lower())
        return self.fields[index] if index is not None else ""

    def to_analysis_dict(self, language: str) -> dict:
        return {
            "line": self.line_number,
            "type": self.event_type,
            "language": language,
            "start": self._field("start"),
            "end": self._field("end"),
            "style": self.style,
            "name": self._field("name"),
            "effect": self._field("effect"),
            "raw_text": self.raw_text,
            "text": self.text,
        }

    def render(self) -> str:
        self.fields[self.indexes["text"]] = "".join(token.value for token in self.tokens)
        return f"{self.prefix}{','.join(self.fields)}"


@dataclass
class _AssDocument:
    path: Path
    encoding: str
    content: str
    lines: list[str]
    event_format: list[str]
    styles: list[str]
    events: list[_ParsedEvent]


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
    """Return visible ASS text without override tags or vector drawings.

    ``\\N`` and ``\\n`` become real newlines, while ``\\h`` becomes a normal
    space. Text emitted while ASS drawing mode (``\\p1`` ... ``\\p0``) is active
    is excluded.
    """

    parts: list[str] = []
    for token in _tokenize_ass_text(text):
        if token.kind == "text":
            parts.append(token.value)
        elif token.kind == "escape":
            parts.append(" " if token.value == r"\h" else "\n")
    return "".join(parts)


def _read_ass(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in _ASS_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法识别 ASS 文件编码: {path}")


def _parse_ass(path: Path | str) -> _AssDocument:
    ass_path = Path(path).expanduser().resolve()
    if not ass_path.exists():
        raise FileNotFoundError(f"ASS 文件不存在: {ass_path}")
    content, encoding = _read_ass(ass_path)
    return _parse_ass_content(ass_path, content, encoding)


def _parse_ass_content(path: Path, content: str, encoding: str = "memory") -> _AssDocument:
    """Parse ASS content for both file analysis and in-memory conversion."""

    lines = content.splitlines(keepends=True)
    in_styles = False
    in_events = False
    style_format: list[str] = []
    event_format: list[str] = []
    styles: list[str] = []
    events: list[_ParsedEvent] = []

    for line_number, physical_line in enumerate(lines, 1):
        line = physical_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            in_styles = section in {"v4+ styles", "v4 styles"}
            in_events = section == "events"
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
        if not separator or event_type not in {"dialogue", "comment"} or not event_format:
            continue
        fields = payload.lstrip().split(",", len(event_format) - 1)
        if len(fields) != len(event_format):
            raise HanvertConversionError(f"第 {line_number} 行 Events 字段数与 Format 不一致")
        indexes = {name.lower(): index for index, name in enumerate(event_format)}
        if "text" not in indexes:
            raise HanvertConversionError("[Events] Format 缺少 Text 字段")
        raw_text = fields[indexes["text"]]
        events.append(_ParsedEvent(
            line_number=line_number,
            event_type=event_type.capitalize(),
            prefix=f"{prefix}:{payload[:len(payload) - len(payload.lstrip())]}",
            fields=fields,
            indexes=indexes,
            raw_text=raw_text,
            tokens=_tokenize_ass_text(raw_text),
        ))

    return _AssDocument(
        path=path,
        encoding=encoding,
        content=content,
        lines=lines,
        event_format=event_format,
        styles=styles,
        events=events,
    )


def classify_ass_language(style: str, text: str) -> str:
    """Classify an ASS event as ``zh``, ``ja``, ``mixed``, or ``other``."""

    has_han = bool(_HAN_RE.search(text))
    has_kana = bool(_KANA_RE.search(text))
    has_simplified_hint = bool(_SIMPLIFIED_HINT_RE.search(text))
    has_han_only_line = any(
        _HAN_RE.search(part) and not _KANA_RE.search(part)
        for part in text.splitlines() or [text]
    )
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


def extract_ass_analysis(
    ass_path: Path | str,
    output_path: Path | str | None = None,
    *,
    include_comments: bool = False,
) -> dict:
    """Extract language-grouped ASS event details and statistics.

    When ``output_path`` is supplied, the returned dictionary is also written
    as UTF-8 JSON without escaping Chinese or Japanese characters.
    """

    document = _parse_ass(ass_path)
    languages: dict[str, list[dict]] = {key: [] for key in ("zh", "ja", "mixed", "other")}
    dialogue_count = sum(event.event_type == "Dialogue" for event in document.events)
    comment_count = sum(event.event_type == "Comment" for event in document.events)
    tagged_count = 0
    line_break_count = 0
    drawing_count = 0
    raw_characters = 0
    text_characters = 0

    for event in document.events:
        if event.event_type == "Comment" and not include_comments:
            continue
        text = event.text
        language = classify_ass_language(event.style, text)
        languages[language].append(event.to_analysis_dict(language))
        raw_characters += len(event.raw_text)
        text_characters += len(text)
        tagged_count += bool(_OVERRIDE_RE.search(event.raw_text))
        line_break_count += bool(re.search(r"\\[Nn]", event.raw_text))
        drawing_count += any(token.kind == "drawing" and token.value.strip() for token in event.tokens)

    language_counts = {key: len(items) for key, items in languages.items()}
    character_counts = {
        key: sum(len(item["text"]) for item in items)
        for key, items in languages.items()
    }
    result = {
        "file": {
            "path": str(document.path),
            "name": document.path.name,
            "encoding": document.encoding,
            "event_format": document.event_format,
            "styles": document.styles,
        },
        "summary": {
            "dialogue_count": dialogue_count,
            "comment_count": comment_count,
            "included_event_count": sum(language_counts.values()),
            "language_counts": language_counts,
            "character_counts": character_counts,
            "raw_character_count": raw_characters,
            "text_character_count": text_characters,
            "tagged_event_count": tagged_count,
            "line_break_event_count": line_break_count,
            "drawing_event_count": drawing_count,
            "comments_included": include_comments,
        },
        "languages": languages,
    }
    if output_path is not None:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _fanhuaji_convert(text: str, converter: str, api_url: str, timeout: int) -> str:
    try:
        response = requests.post(
            api_url,
            data={"text": text, "converter": converter},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.exceptions.RequestException as exc:
        raise HanvertConversionError(f"繁化姬请求失败: {exc}") from exc
    except ValueError as exc:
        raise HanvertConversionError("繁化姬返回了无法解析的 JSON") from exc
    if payload.get("code") != 0:
        message = payload.get("msg") or payload.get("message") or "未知错误"
        raise HanvertConversionError(f"繁化姬返回错误: {message}")
    converted = (payload.get("data") or {}).get("text")
    if not isinstance(converted, str):
        raise HanvertConversionError("繁化姬响应缺少 data.text")
    return converted


def _visible_text_tokens(tokens: Iterable[_Token]) -> list[_Token]:
    return [token for token in tokens if token.kind == "text" and token.value]


def _regroup_text(tokens: list[_Token], source: str, converted: str) -> bool:
    text_tokens = _visible_text_tokens(tokens)
    if not text_tokens:
        return False
    if sum(len(token.value) for token in text_tokens) != len(source):
        raise HanvertConversionError("ASS 文本节点与提取文本长度不一致")

    length_changed = len(source) != len(converted)
    if length_changed and len(text_tokens) > 1:
        raise HanvertConversionError("繁化结果长度变化，无法安全恢复到多个 ASS 标签文本节点")
    if length_changed:
        text_tokens[0].value = converted
        return True

    cursor = 0
    for token in text_tokens:
        end = cursor + len(token.value)
        token.value = converted[cursor:end]
        cursor = end
    return False


@dataclass
class _ConversionJob:
    event: _ParsedEvent
    tokens: list[_Token]
    source: str
    direct_token: _Token | None = None


def _make_group_job(event: _ParsedEvent, tokens: list[_Token]) -> _ConversionJob | None:
    source = "".join(token.value for token in _visible_text_tokens(tokens))
    if not source or not _HAN_RE.search(source):
        return None
    return _ConversionJob(event, tokens, source)


def _make_mixed_jobs(event: _ParsedEvent, tokens: list[_Token]) -> list[_ConversionJob]:
    jobs: list[_ConversionJob] = []
    for token in list(_visible_text_tokens(tokens)):
        if not _HAN_RE.search(token.value):
            continue
        if not _KANA_RE.search(token.value):
            jobs.append(_ConversionJob(event, [token], token.value, direct_token=token))
            continue

        pieces = [piece for piece in re.split(f"({_KANA_RE.pattern}+)", token.value) if piece]
        replacements = [_Token(piece, "text") for piece in pieces]
        group_index = tokens.index(token)
        tokens[group_index:group_index + 1] = replacements
        event_index = event.tokens.index(token)
        event.tokens[event_index:event_index + 1] = replacements
        for replacement in replacements:
            if _HAN_RE.search(replacement.value) and not _KANA_RE.search(replacement.value):
                jobs.append(_ConversionJob(
                    event, [replacement], replacement.value, direct_token=replacement,
                ))
    return jobs


def _token_groups(tokens: list[_Token]) -> list[list[_Token]]:
    groups: list[list[_Token]] = []
    current: list[_Token] = []
    for token in tokens:
        if token.kind == "escape" and token.value in {r"\N", r"\n"}:
            if current:
                groups.append(current)
                current = []
            continue
        if token.kind != "drawing":
            current.append(token)
    if current:
        groups.append(current)
    return groups


def convert_ass_with_fanhuaji(
    content: str,
    *,
    converter: str = "Taiwan",
    api_url: str = "https://api.zhconvert.org/convert",
    timeout: int = 60,
    full_file: bool = False,
    fallback_to_full_file: bool = True,
) -> tuple[str, dict]:
    """Convert Chinese ASS text, with optional full-file passthrough."""

    def convert_full(reason: str) -> tuple[str, dict]:
        converted = _fanhuaji_convert(content, converter, api_url, timeout)
        return converted, {
            "converted_events": 0,
            "length_changed_events": 0,
            "skipped_mixed_groups": 0,
            "conversion_mode": "full_file",
            "fallback_reason": reason,
        }

    if full_file:
        return convert_full("requested")

    temporary = Path("__memory__.ass")
    try:
        document = _parse_ass_content(temporary, content)
    except HanvertConversionError:
        if fallback_to_full_file:
            return convert_full("ass_parse_failed")
        raise
    lines = document.lines
    has_events_section = bool(re.search(r"(?im)^\s*\[Events\]\s*$", content))
    if has_events_section and not document.event_format:
        if fallback_to_full_file:
            return convert_full("events_format_missing")
        raise HanvertConversionError("[Events] 缺少有效 Format，无法进行 ASS 感知繁化")
    jobs: list[_ConversionJob] = []
    skipped_mixed_groups = 0
    for event in document.events:
        if event.event_type != "Dialogue":
            continue
        language = classify_ass_language(event.style, event.text)
        if language == "zh":
            for group in _token_groups(event.tokens):
                job = _make_group_job(event, group)
                if job is not None:
                    jobs.append(job)
        elif language == "mixed":
            for group in _token_groups(event.tokens):
                group_text = "".join(token.value for token in _visible_text_tokens(group))
                group_language = classify_ass_language("", group_text)
                if group_language == "zh":
                    job = _make_group_job(event, group)
                    if job is not None:
                        jobs.append(job)
                elif group_language == "mixed":
                    mixed_jobs = _make_mixed_jobs(event, group)
                    jobs.extend(mixed_jobs)
                    if not mixed_jobs:
                        skipped_mixed_groups += 1

    if not jobs and fallback_to_full_file and _HAN_RE.search(content):
        return convert_full("no_conversion_candidates")
    if not jobs:
        return content, {
            "converted_events": 0,
            "length_changed_events": 0,
            "skipped_mixed_groups": skipped_mixed_groups,
            "conversion_mode": "ass_aware",
            "fallback_reason": None,
        }

    request_text = "\n".join(job.source for job in jobs)
    converted_lines = _fanhuaji_convert(request_text, converter, api_url, timeout).splitlines()
    if len(converted_lines) != len(jobs):
        raise HanvertConversionError("繁化姬改变了待转换文本的行数，无法安全恢复 ASS 结构")

    changed_events: dict[int, _ParsedEvent] = {}
    length_changed_lines: set[int] = set()
    for job, converted in zip(jobs, converted_lines):
        if job.direct_token is not None:
            length_changed = len(job.source) != len(converted)
            job.direct_token.value = converted
        else:
            length_changed = _regroup_text(job.tokens, job.source, converted)
        changed_events[job.event.line_number] = job.event
        if length_changed:
            length_changed_lines.add(job.event.line_number)

    for line_number, event in changed_events.items():
        newline = lines[line_number - 1][len(lines[line_number - 1].rstrip("\r\n")):]
        lines[line_number - 1] = event.render() + newline

    return "".join(lines), {
        "converted_events": len(changed_events),
        "length_changed_events": len(length_changed_lines),
        "skipped_mixed_groups": skipped_mixed_groups,
        "conversion_mode": "ass_aware",
        "fallback_reason": None,
    }
