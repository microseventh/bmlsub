"""Local subtitle-to-text editing for ``bmlsub build editsub``.

Subtitle parsing and refinement are provided by the vendored SubsRefine core:
https://github.com/MingYSub/SubsRefine/tree/c47cec799fb5615d74a6561a7b6a91054c669c4b.
SubsRefine is Copyright
(c) 2025 MingYSub and licensed under MIT; see
``LICENSES/SubsRefine-MIT.txt`` and ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import unicodedata
from typing import Any

from ._vendor.subsrefine import ProcessingConfig, Processor, Subtitle
from .execution.errors import BmlsubError, ErrorCode, OutputValidationError


SUPPORTED_SUFFIXES = frozenset({".ass", ".srt", ".vtt"})
JAPANESE_SPACE = "\u3000"
SUBSREFINE_COMMIT = "c47cec799fb5615d74a6561a7b6a91054c669c4b"


@dataclass(frozen=True)
class PreparedEdit:
    source: Path
    output: Path
    text: str
    checks: dict[str, Any]


def _input_error(message: str, *, path: Path, **details: Any) -> BmlsubError:
    return BmlsubError(
        message,
        code=ErrorCode.INPUT_MISSING,
        details={"path": str(path), **details},
    )


def resolve_edit_input(value: str | Path, *, launch_directory: Path | None = None) -> Path:
    """Resolve an editsub input relative to the command launch directory."""

    launch = (launch_directory or Path.cwd()).resolve()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = launch / candidate
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _input_error("editsub input does not exist", path=candidate) from exc


def discover_edit_sources(path: Path) -> list[Path]:
    """Return stable, non-recursive subtitle inputs for one file or directory."""

    if path.is_file():
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise _input_error(
                "editsub input format is not supported",
                path=path,
                supported_suffixes=sorted(SUPPORTED_SUFFIXES),
            )
        return [path]
    if not path.is_dir():
        raise _input_error("editsub input is not a regular file or directory", path=path)

    sources = sorted(
        (
            item.resolve()
            for item in path.iterdir()
            if item.is_file()
            and item.suffix.lower() in SUPPORTED_SUFFIXES
            and not item.stem.casefold().endswith("_processed")
        ),
        key=lambda item: (item.name.casefold(), item.name),
    )
    if not sources:
        raise _input_error(
            "editsub directory contains no supported subtitle files",
            path=path,
            supported_suffixes=sorted(SUPPORTED_SUFFIXES),
        )
    return sources


def _load_subtitle(path: Path) -> Subtitle:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _input_error("editsub input is not valid UTF-8 text", path=path) from exc
    except OSError as exc:
        raise _input_error("editsub input could not be read", path=path, reason=str(exc)) from exc

    suffix = path.suffix.lower()
    try:
        if suffix == ".ass":
            document = Subtitle.from_ass_text(text)
        elif suffix == ".srt":
            document = Subtitle.from_srt_text(text)
        elif suffix == ".vtt":
            document = Subtitle.from_vtt_text(text)
        else:  # Protected by discovery; retained as a defensive boundary.
            raise _input_error("editsub input format is not supported", path=path)
    except (IndexError, TypeError, ValueError) as exc:
        raise _input_error(
            "editsub input could not be parsed as a subtitle",
            path=path,
            reason=str(exc),
        ) from exc

    if not document.events:
        raise _input_error("editsub input contains no subtitle events", path=path)
    return document


def canonicalize_edited_text(text: str) -> str:
    """Apply the fixed bmlsub text contract after SubsRefine processing."""

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        raw_line = unicodedata.normalize("NFC", raw_line)
        characters: list[str] = []
        pending_space = False
        for character in raw_line:
            category = unicodedata.category(character)
            if character.isspace():
                pending_space = bool(characters)
            elif category[0] in {"P", "S", "C"} or _is_variation_selector(character):
                continue
            else:
                if pending_space and characters:
                    characters.append(JAPANESE_SPACE)
                pending_space = False
                characters.append(character)
        line = "".join(characters).rstrip(JAPANESE_SPACE)
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines) + ("\n" if cleaned_lines else "")


def _is_variation_selector(character: str) -> bool:
    """Return whether a combining mark only controls glyph presentation."""

    codepoint = ord(character)
    return 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF


def validate_edited_text(text: str) -> dict[str, Any]:
    """Return the six requested invariants plus encoding metadata."""

    lines = text.splitlines()
    content_characters = (character for line in lines for character in line)
    punctuation_and_symbols_removed = all(
        unicodedata.category(character)[0] not in {"P", "S", "C"}
        and not _is_variation_selector(character)
        for character in content_characters
    )
    internal_spaces_are_japanese = all(
        not character.isspace() or character == JAPANESE_SPACE
        for line in lines
        for character in line
    )
    no_trailing_spaces = all(not line or not line[-1].isspace() for line in lines)
    no_leading_spaces = all(not line or not line[0].isspace() for line in lines)
    no_blank_lines = all(bool(line) for line in lines)

    try:
        utf8_text = text.encode("utf-8").decode("utf-8") == text
    except UnicodeError:
        utf8_text = False

    checks: dict[str, Any] = {
        "utf8_text": utf8_text,
        "line_count": len(lines),
        "punctuation_and_symbols_removed": punctuation_and_symbols_removed,
        "internal_spaces_are_japanese": internal_spaces_are_japanese,
        "no_ascii_spaces": " " not in text,
        "no_trailing_spaces": no_trailing_spaces,
        "no_blank_lines": no_blank_lines,
        "no_leading_spaces": no_leading_spaces,
        "single_final_newline": not text or (text.endswith("\n") and not text.endswith("\n\n")),
    }
    checks["valid"] = all(
        value for key, value in checks.items()
        if key not in {"line_count", "valid"}
    )
    return checks


def _prepare_edit(source: Path, output: Path, processor: Processor) -> PreparedEdit:
    document = _load_subtitle(source)
    try:
        processor.process_subtitle(document)
    except Exception as exc:
        raise BmlsubError(
            "SubsRefine could not process the subtitle",
            code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            details={"path": str(source), "reason": str(exc)},
        ) from exc

    text = canonicalize_edited_text(document.to_txt())
    checks = validate_edited_text(text)
    if not checks["valid"]:
        raise OutputValidationError(
            "edited text failed validation",
            details={"source": str(source), "output": str(output), "checks": checks},
        )
    return PreparedEdit(source=source, output=output, text=text, checks=checks)


def _write_atomic(artifact: PreparedEdit) -> None:
    artifact.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=artifact.output.parent,
        prefix=f".{artifact.output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(artifact.text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        written = temporary.read_bytes()
        if written.startswith(b"\xef\xbb\xbf") or written.decode("utf-8") != artifact.text:
            raise OutputValidationError(
                "temporary editsub output failed UTF-8 validation",
                details={"output": str(artifact.output)},
            )
        os.replace(temporary, artifact.output)
    finally:
        temporary.unlink(missing_ok=True)


def run_edit(
    value: str | Path = ".",
    *,
    launch_directory: Path | None = None,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    """Process one subtitle or one directory and write outputs to the launch directory."""

    launch = (launch_directory or Path.cwd()).resolve()
    output_root = (output_directory or launch).resolve()
    if not output_root.is_dir():
        raise BmlsubError(
            "editsub output directory is not available",
            code=ErrorCode.ARTIFACT_COMMIT_FAILED,
            details={"path": str(output_root)},
        )

    requested = resolve_edit_input(value, launch_directory=launch)
    sources = discover_edit_sources(requested)
    output_map: dict[str, tuple[Path, Path]] = {}
    for source in sources:
        output = output_root / f"{source.stem}_processed.txt"
        collision_key = output.name.casefold()
        if collision_key in output_map:
            first_source, first_output = output_map[collision_key]
            raise OutputValidationError(
                "multiple editsub inputs map to the same output name",
                details={
                    "first_source": str(first_source),
                    "second_source": str(source),
                    "output": str(first_output),
                },
            )
        output_map[collision_key] = (source, output)

    processor = Processor(ProcessingConfig())
    prepared = [
        _prepare_edit(source, output, processor)
        for source, output in output_map.values()
    ]
    for artifact in prepared:
        _write_atomic(artifact)

    return {
        "status": "succeeded",
        "operation": "editsub",
        "input": str(requested),
        "output_directory": str(output_root),
        "processed_count": len(prepared),
        "processor": {
            "name": "SubsRefine",
            "source": (
                "https://github.com/MingYSub/SubsRefine/tree/"
                f"{SUBSREFINE_COMMIT}"
            ),
            "commit": SUBSREFINE_COMMIT,
            "license": "MIT",
            "license_file": "LICENSES/SubsRefine-MIT.txt",
        },
        "artifacts": [
            {
                "source": str(artifact.source),
                "path": str(artifact.output),
                "encoding": "utf-8",
                "bytes": artifact.output.stat().st_size,
                "line_count": artifact.checks["line_count"],
                "checks": artifact.checks,
            }
            for artifact in prepared
        ],
    }
