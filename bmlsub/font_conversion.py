"""Convert ASS font names using the names embedded in local font files."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from collections import defaultdict
from typing import Iterable, Any

from .artifacts.validators import validate_ass_conversion
from .execution.errors import BmlsubError, ErrorCode, OutputValidationError
from .hanvert import read_ass


FONT_EXTENSIONS = {".ttf", ".ttc", ".otf", ".otc"}
ALIAS_NAME_IDS = (16, 1, 17, 2, 4, 6)
TARGET_NAME_IDS = (16, 1, 4, 6)
JAPANESE_LANG_IDS = {11, 1041}
GENERIC_NAME_ALIASES = {"regular", "normal", "standard", "bold", "italic"}
FONT_TAG_RE = re.compile(r"(\\+fn)([^\\}]+)")
STYLE_SECTIONS = {"v4 styles", "v4+ styles"}


@dataclass
class FontFace:
    path: Path
    index: int
    aliases: set[str] = field(default_factory=set)
    english_names: list[str] = field(default_factory=list)
    localized_names: list[str] = field(default_factory=list)
    localized_to_english: dict[str, str] = field(default_factory=dict)
    english_to_localized: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FontConversionResult:
    content: str
    replacements: dict[str, str]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class PreparedFontConversion:
    source: Path
    output: Path
    result: FontConversionResult


def _input_error(message: str, *, path: Path, **details: Any) -> BmlsubError:
    return BmlsubError(
        message,
        code=ErrorCode.INPUT_MISSING,
        details={"path": str(path), **details},
    )


def _normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if value.startswith("@"):
        value = value[1:]
    return value.casefold()


def _is_ascii_name(value: str) -> bool:
    return bool(value) and value.isascii() and any(char.isalpha() for char in value)


def _contains_cjk(value: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in value
    )


def _looks_japanese_font(value: str) -> bool:
    """Return whether an English family name explicitly identifies Japan."""
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return (
        "cjkjp" in compact
        or bool(re.search(r"(?:^|[^a-z])jp(?:$|[^a-z])", value.casefold()))
        or compact.endswith("jp")
        or "japanese" in compact
    )


def _font_faces(path: Path) -> Iterable[tuple[int, object]]:
    from fontTools.ttLib import TTCollection, TTFont

    if path.suffix.lower() in {".ttc", ".otc"}:
        yield from enumerate(TTCollection(str(path)).fonts)
        return
    try:
        # Load the name table eagerly so the temporary font file handle is
        # closed before the next face is scanned.
        yield 0, TTFont(str(path), lazy=False)
    except Exception:
        # Some vendors package a collection with a .ttf suffix.
        yield from enumerate(TTCollection(str(path)).fonts)


def read_font_faces(font_directory: Path) -> tuple[list[FontFace], list[str]]:
    """Read font metadata and return (faces, warnings)."""
    faces: list[FontFace] = []
    warnings: list[str] = []
    for path in sorted(font_directory.rglob("*")):
        if path.suffix.lower() not in FONT_EXTENSIONS:
            continue
        try:
            for index, font in _font_faces(path):
                aliases: set[str] = set()
                by_id: dict[int, list[tuple[int, str]]] = defaultdict(list)
                for record in font["name"].names:
                    if record.nameID not in ALIAS_NAME_IDS:
                        continue
                    try:
                        text = record.toUnicode().strip()
                    except Exception:
                        continue
                    if text:
                        normalized = _normalize_name(text)
                        if normalized not in GENERIC_NAME_ALIASES:
                            aliases.add(normalized)
                        by_id[record.nameID].append((record.langID, text))

                def unique(values: Iterable[str]) -> list[str]:
                    result: list[str] = []
                    for value in values:
                        if value not in result:
                            result.append(value)
                    return result

                english: list[str] = []
                localized: list[str] = []
                english_by_id: dict[int, list[str]] = defaultdict(list)
                localized_by_id: dict[int, list[str]] = defaultdict(list)
                for name_id in TARGET_NAME_IDS:
                    for lang_id, value in by_id.get(name_id, []):
                        generic = value.casefold() in {"regular", "normal", "standard"}
                        if _is_ascii_name(value) and not generic:
                            english_by_id[name_id].append(value)
                        if (
                            _contains_cjk(value)
                            and lang_id not in JAPANESE_LANG_IDS
                            and not generic
                        ):
                            localized_by_id[name_id].append(value)
                for name_id in TARGET_NAME_IDS:
                    english.extend(unique(english_by_id[name_id]))
                    localized.extend(unique(localized_by_id[name_id]))

                # A face is eligible only when it has a non-Japanese CJK name.
                # This prevents fonts such as Dream Han Serif JP from being
                # rewritten merely because their English metadata is present.
                if not aliases or not english or not localized:
                    continue
                if any(_looks_japanese_font(value) for value in english):
                    continue

                english_family = (english_by_id[16] or english_by_id[1] or english_by_id[4] or english_by_id[6])[0]
                localized_family = (localized_by_id[16] or localized_by_id[1] or localized_by_id[4] or localized_by_id[6])[0]
                english_full = (english_by_id[4] or english_by_id[1] or english_by_id[16] or english_by_id[6])[0]
                localized_full = (localized_by_id[4] or localized_by_id[1] or localized_by_id[16] or localized_by_id[6])[0]
                english_postscript = (english_by_id[6] or [english_full])[0]
                localized_postscript = (localized_by_id[6] or [localized_full])[0]

                localized_to_english: dict[str, str] = {}
                english_to_localized: dict[str, str] = {}
                for name_id in TARGET_NAME_IDS:
                    for value in unique(localized_by_id[name_id]):
                        if name_id == 16:
                            target = english_family
                        elif name_id == 4:
                            target = english_full
                        elif name_id == 6:
                            target = english_postscript
                        elif _normalize_name(value) == _normalize_name(localized_family):
                            target = english_family
                        else:
                            target = english_full
                        localized_to_english.setdefault(_normalize_name(value), target)
                    for value in unique(english_by_id[name_id]):
                        if name_id == 16:
                            target = localized_family
                        elif name_id == 4:
                            target = localized_full
                        elif name_id == 6:
                            target = localized_postscript
                        elif _normalize_name(value) == _normalize_name(english_family):
                            target = localized_family
                        else:
                            target = localized_full
                        english_to_localized.setdefault(_normalize_name(value), target)
                faces.append(FontFace(
                    path,
                    index,
                    aliases,
                    english,
                    localized,
                    localized_to_english,
                    english_to_localized,
                ))
        except Exception as exc:
            warnings.append(f"{path}: {exc}")
    return faces, warnings


def _build_index(faces: Iterable[FontFace], *, localized: bool) -> dict[str, str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for face in faces:
        mapping = face.english_to_localized if localized else face.localized_to_english
        if mapping:
            for alias, target in mapping.items():
                if target not in candidates[alias]:
                    candidates[alias].append(target)
            continue
        # Keep manually constructed FontFace values usable for callers and
        # tests, while still requiring a Chinese/localized name as evidence
        # that the face belongs in the conversion index.
        if not face.localized_names or not face.english_names:
            continue
        target = face.localized_names[0] if localized else face.english_names[0]
        source_names = face.aliases or {
            _normalize_name(name)
            for name in (face.localized_names if not localized else face.english_names)
        }
        for alias in source_names:
            if target not in candidates[alias]:
                candidates[alias].append(target)
    return {alias: values[0] for alias, values in candidates.items() if len(values) == 1}


def convert_ass_content(
    content: str,
    faces: Iterable[FontFace],
    *,
    to_chinese: bool,
    explicit_mapping: dict[str, str] | None = None,
    keep_at: bool = True,
) -> FontConversionResult:
    index = _build_index(faces, localized=to_chinese)
    explicit = explicit_mapping or {}
    explicit_normalized = {_normalize_name(key): value for key, value in explicit.items()}
    replacements: dict[str, str] = {}
    unresolved: set[str] = set()

    def convert(original: str) -> str:
        raw = original.strip()
        if not raw:
            return original
        leading_at = raw.startswith("@")
        base = raw[1:] if leading_at else raw
        target = (
            explicit.get(base)
            or explicit.get(raw)
            or explicit_normalized.get(_normalize_name(base))
            or index.get(_normalize_name(base))
            or index.get(_normalize_name(raw))
        )
        if target is None:
            unresolved.add(raw)
            target = raw
        if leading_at and not target.startswith("@"):
            target = "@" + target
        replacements[raw] = target
        left = original[: len(original) - len(original.lstrip())]
        right = original[len(original.rstrip()) :]
        return left + target + right

    lines = content.splitlines(keepends=True)
    output: list[str] = []
    in_styles = False
    style_font_index = 1
    for line in lines:
        section = re.match(r"\s*\[([^]]+)\]", line)
        if section:
            in_styles = section.group(1).casefold() in STYLE_SECTIONS
            style_font_index = 1
        stripped = line.lstrip()
        if in_styles and stripped.casefold().startswith("format:"):
            fields = stripped.split(":", 1)[1].split(",")
            for field_index, field in enumerate(fields):
                if field.strip().casefold() == "fontname":
                    style_font_index = field_index
                    break
        if in_styles and stripped.casefold().startswith("style:"):
            fields = line.split(",")
            if len(fields) > style_font_index:
                fields[style_font_index] = convert(fields[style_font_index])
                line = ",".join(fields)
        line = FONT_TAG_RE.sub(lambda match: match.group(1) + convert(match.group(2)), line)
        output.append(line)
    return FontConversionResult("".join(output), replacements, tuple(sorted(unresolved, key=str.casefold)))


def resolve_font_directory(
    requested: Path | None,
    *,
    subtitle_root: Path,
    launch_directory: Path,
) -> Path:
    directory = requested or (subtitle_root / "fonts")
    if not directory.is_absolute():
        directory = launch_directory / directory
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise _input_error(
            "font directory not found; put font files in the subtitle directory's fonts folder "
            "or pass a font directory path",
            path=directory,
            subtitle_directory=str(subtitle_root),
        )
    if not any(item.is_file() and item.suffix.lower() in FONT_EXTENSIONS for item in directory.rglob("*")):
        raise _input_error(
            "font directory contains no supported font files; put .ttf, .ttc, .otf, or .otc files there",
            path=directory,
            supported_suffixes=sorted(FONT_EXTENSIONS),
        )
    return directory


def resolve_font_input(value: str | Path, *, launch_directory: Path | None = None) -> Path:
    launch = (launch_directory or Path.cwd()).resolve()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = launch / candidate
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _input_error("fonts conversion input does not exist", path=candidate) from exc


def discover_font_sources(path: Path, *, to_chinese: bool) -> list[Path]:
    suffix = ".chinese.ass" if to_chinese else ".english.ass"
    if path.is_file():
        if path.suffix.casefold() != ".ass":
            raise _input_error("fonts conversion only supports ASS subtitle files", path=path)
        if path.name.casefold().endswith(suffix):
            raise _input_error("fonts conversion input is already a generated output", path=path)
        return [path]
    if not path.is_dir():
        raise _input_error("fonts conversion input is not a regular file or directory", path=path)
    sources = sorted(
        (
            item.resolve()
            for item in path.iterdir()
            if item.is_file()
            and item.suffix.casefold() == ".ass"
            and not item.name.casefold().endswith((".english.ass", ".chinese.ass"))
        ),
        key=lambda item: (item.name.casefold(), item.name),
    )
    if not sources:
        raise _input_error(
            "fonts conversion directory contains no source ASS subtitle files",
            path=path,
            supported_suffixes=[".ass"],
        )
    return sources


def _output_path(source: Path, *, to_chinese: bool) -> Path:
    stem = source.stem
    if stem.endswith(".english") or stem.endswith(".chinese"):
        stem = stem.rsplit(".", 1)[0]
    suffix = "chinese" if to_chinese else "english"
    return source.with_name(f"{stem}.{suffix}.ass")


def _write_atomic(artifact: PreparedFontConversion) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=artifact.output.parent,
        prefix=f".{artifact.output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(artifact.result.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        validate_ass_conversion(artifact.source, temporary, allow_full_file=True)
        os.replace(temporary, artifact.output)
    except BmlsubError:
        raise
    except Exception as exc:
        raise OutputValidationError(
            "font conversion output could not be validated or committed",
            details={
                "source": str(artifact.source),
                "output": str(artifact.output),
                "reason": str(exc),
            },
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def run_font_conversion(
    value: str | Path = ".",
    *,
    fonts: Path | None = None,
    to_chinese: bool = False,
    launch_directory: Path | None = None,
    explicit_mapping: dict[str, str] | None = None,
    keep_at: bool = True,
) -> dict[str, Any]:
    """Convert one ASS file or a non-recursive directory batch."""
    launch = (launch_directory or Path.cwd()).resolve()
    requested = resolve_font_input(value, launch_directory=launch)
    sources = discover_font_sources(requested, to_chinese=to_chinese)
    font_directory = resolve_font_directory(
        fonts,
        subtitle_root=requested.parent if requested.is_file() else requested,
        launch_directory=launch,
    )
    faces, warnings = read_font_faces(font_directory)
    if not faces:
        raise _input_error(
            "font directory contains no readable font metadata",
            path=font_directory,
        )
    output_map: dict[str, tuple[Path, Path]] = {}
    for source in sources:
        output = _output_path(source, to_chinese=to_chinese).resolve()
        key = str(output).casefold()
        if key in output_map:
            first_source, first_output = output_map[key]
            raise OutputValidationError(
                "multiple ASS inputs map to the same font conversion output",
                details={
                    "first_source": str(first_source),
                    "second_source": str(source),
                    "output": str(first_output),
                },
            )
        output_map[key] = (source, output)

    prepared: list[PreparedFontConversion] = []
    for source, output in output_map.values():
        try:
            content, _ = read_ass(source)
        except (OSError, ValueError) as exc:
            raise _input_error(
                "font conversion input could not be read as an ASS subtitle",
                path=source,
                reason=str(exc),
            ) from exc
        result = convert_ass_content(
            content,
            faces,
            to_chinese=to_chinese,
            explicit_mapping=explicit_mapping,
            keep_at=keep_at,
        )
        prepared.append(PreparedFontConversion(source, output, result))

    for artifact in prepared:
        _write_atomic(artifact)

    artifacts = []
    for artifact in prepared:
        changed = sum(
            old != new for old, new in artifact.result.replacements.items()
        )
        artifacts.append({
            "source": str(artifact.source),
            "path": str(artifact.output),
            "bytes": artifact.output.stat().st_size,
            "changed_font_names": changed,
            "replacements": artifact.result.replacements,
            "unresolved": list(artifact.result.unresolved),
        })
    payload: dict[str, Any] = {
        "status": "succeeded",
        "operation": "fonts2cn" if to_chinese else "fonts2en",
        "input": str(requested),
        "fonts_directory": str(font_directory),
        "processed_count": len(prepared),
        "artifacts": artifacts,
    }
    if warnings:
        payload["diagnostics"] = [
            {
                "code": "font_file_unreadable",
                "message": warning,
                "level": "warning",
            }
            for warning in warnings
        ]
    return payload
