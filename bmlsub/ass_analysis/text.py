"""ASS text block and override-tag analysis."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class TextBlock:
    kind: str
    value: str
    drawing_mode: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "drawing_mode": self.drawing_mode}


@dataclass(frozen=True)
class OverrideTag:
    name: str
    argument: str
    raw: str
    nested: tuple["OverrideTag", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": f"\\{self.name}",
            "canonical_name": self.name,
            "argument": self.argument,
            "raw": self.raw,
            "nested": [item.to_dict() for item in self.nested],
        }


_TAG_NAME_RE = re.compile(r"[A-Za-z]+|[1-4][ac]")
_KNOWN_TAG_NAMES = tuple(sorted({
    "alpha", "iclip", "xbord", "ybord", "xshad", "yshad", "fscx", "fscy",
    "frx", "fry", "frz", "fade", "bord", "shad", "blur", "be", "clip",
    "move", "pos", "org", "fad", "fsp", "fs", "fn", "fe", "fax", "fay",
    "an", "a", "b", "i", "u", "s", "pbo", "p", "q", "r", "t", "k",
    "K", "kf", "ko", "kt", "c", "1c", "2c", "3c", "4c", "1a", "2a",
    "3a", "4a",
}, key=len, reverse=True))
_POSITION_TAGS = {"pos", "move", "org", "an", "a"}
_ANIMATION_TAGS = {"t", "fad", "fade", "k", "K", "kf", "ko", "kt"}
_CLIP_TAGS = {"clip", "iclip"}
_KARAOKE_TAGS = {"k", "K", "kf", "ko", "kt"}


def parse_text_blocks(text: str) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    drawing_mode = 0
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        if opening < 0:
            _append_visible(blocks, text[cursor:], drawing_mode)
            break
        if opening > cursor:
            _append_visible(blocks, text[cursor:opening], drawing_mode)
        closing = text.find("}", opening + 1)
        if closing < 0:
            _append_visible(blocks, text[opening:], drawing_mode)
            break
        value = text[opening + 1:closing]
        kind = "override" if value.lstrip().startswith("\\") else "comment"
        blocks.append(TextBlock(kind, value, drawing_mode))
        if kind == "override":
            for tag in parse_override_tags(value):
                if tag.name.lower() == "p":
                    try:
                        drawing_mode = max(0, int(tag.argument.strip() or "0"))
                    except ValueError:
                        pass
        cursor = closing + 1
    if not text:
        return ()
    return tuple(blocks)


def parse_override_tags(value: str) -> tuple[OverrideTag, ...]:
    tags: list[OverrideTag] = []
    cursor = 0
    while cursor < len(value):
        slash = value.find("\\", cursor)
        if slash < 0:
            break
        name_match = _TAG_NAME_RE.match(value, slash + 1)
        if not name_match:
            cursor = slash + 1
            continue
        token = name_match.group(0)
        name = next((candidate for candidate in _KNOWN_TAG_NAMES
                     if token.startswith(candidate)), token)
        arg_start = slash + 1 + len(name)
        if arg_start < len(value) and value[arg_start] == "(":
            end = _matching_paren(value, arg_start)
            if end < 0:
                argument = value[arg_start + 1:]
                raw = value[slash:]
                cursor = len(value)
            else:
                argument = value[arg_start + 1:end]
                raw = value[slash:end + 1]
                cursor = end + 1
        else:
            next_slash = value.find("\\", arg_start)
            end = len(value) if next_slash < 0 else next_slash
            argument = value[arg_start:end]
            raw = value[slash:end]
            cursor = end
        nested: tuple[OverrideTag, ...] = ()
        if name.lower() == "t":
            nested_start = argument.find("\\")
            if nested_start >= 0:
                nested = parse_override_tags(argument[nested_start:])
        tags.append(OverrideTag(name, argument, raw, nested))
    return tuple(tags)


def text_features(text: str) -> dict[str, Any]:
    blocks = parse_text_blocks(text)
    override_blocks = []
    root_tags: list[OverrideTag] = []
    for index, block in enumerate(blocks):
        if block.kind != "override":
            continue
        parsed = parse_override_tags(block.value)
        root_tags.extend(parsed)
        override_blocks.append({
            "block_index": index,
            "raw": "{" + block.value + "}",
            "value": block.value,
            "tags": [tag.to_dict() for tag in parsed],
        })
    tags = tuple(root_tags)
    flattened = tuple(_flatten_tags(tags))
    plain = visible_text(blocks)
    canonical_names = [item.name for item in flattened]
    lowered = {item.lower() for item in canonical_names}
    return {
        "blocks": [item.to_dict() for item in blocks],
        "block_types": sorted({item.kind for item in blocks}),
        "override_blocks": override_blocks,
        "plain_text": plain,
        "plain_length": len(plain),
        "tag_names": [f"\\{item}" for item in canonical_names],
        "has_override": bool(tags),
        "has_drawing": any(item.kind == "drawing" for item in blocks),
        "has_karaoke": any(item in _KARAOKE_TAGS or item.lower() in _KARAOKE_TAGS
                            for item in canonical_names),
        "has_position": bool(lowered & _POSITION_TAGS),
        "has_animation": bool(lowered & {item.lower() for item in _ANIMATION_TAGS}),
        "has_clip": bool(lowered & _CLIP_TAGS),
        "font_overrides": [item.argument.strip() for item in flattened
                           if item.name.lower() == "fn" and item.argument.strip()],
    }


def visible_text(blocks: tuple[TextBlock, ...]) -> str:
    values: list[str] = []
    for block in blocks:
        if block.kind != "plain":
            continue
        value = block.value.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
        values.append(value)
    return "".join(values)


def iter_effective_text_runs(text: str, *, initial_font: str, initial_bold: bool,
                             initial_italic: bool, style_lookup: dict[str, tuple[str, bool, bool]],
                             base_style: str):
    font = initial_font
    bold = initial_bold
    italic = initial_italic
    drawing = 0
    for block in parse_text_blocks(text):
        if block.kind == "override":
            for tag in parse_override_tags(block.value):
                font, bold, italic, drawing = _apply_tag(
                    tag, font=font, bold=bold, italic=italic, drawing=drawing,
                    initial=(initial_font, initial_bold, initial_italic),
                    style_lookup=style_lookup, base_style=base_style,
                )
        elif block.kind == "plain" and block.value:
            yield font, bold, italic, drawing, block.value


def _apply_tag(tag: OverrideTag, *, font: str, bold: bool, italic: bool, drawing: int,
               initial: tuple[str, bool, bool], style_lookup: dict[str, tuple[str, bool, bool]],
               base_style: str) -> tuple[str, bool, bool, int]:
    name = tag.name.lower()
    argument = tag.argument.strip()
    if name == "r":
        chosen = style_lookup.get(argument or base_style, initial)
        font, bold, italic = chosen
    elif name == "fn":
        font = argument or initial[0]
    elif name == "b":
        bold = _tag_bool(argument, initial[1])
    elif name == "i":
        italic = _tag_bool(argument, initial[2])
    elif name == "p":
        try:
            drawing = max(0, int(argument or "0"))
        except ValueError:
            pass
    # Tags nested inside \t describe an animated state which can be rendered during the run.
    for nested in tag.nested:
        font, bold, italic, drawing = _apply_tag(
            nested, font=font, bold=bold, italic=italic, drawing=drawing,
            initial=initial, style_lookup=style_lookup, base_style=base_style,
        )
    return font, bold, italic, drawing


def _tag_bool(value: str, default: bool) -> bool:
    if not value:
        return default
    try:
        return int(value) != 0
    except ValueError:
        return default


def _append_visible(blocks: list[TextBlock], value: str, drawing_mode: int) -> None:
    if value:
        blocks.append(TextBlock("drawing" if drawing_mode else "plain", value, drawing_mode))


def _matching_paren(value: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(value)):
        if value[index] == "(":
            depth += 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _flatten_tags(tags: tuple[OverrideTag, ...]):
    for tag in tags:
        yield tag
        yield from _flatten_tags(tag.nested)
