"""Direct file and directory entry point for ASS traditionalization."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any

from .artifacts.validators import validate_ass_conversion
from .execution.errors import (
    ArtifactCommitError, BmlsubError, ErrorCode, OutputValidationError,
)
from .hanvert import ConverterProvider, HanvertResult, convert_ass, read_ass
from .subtitle import SubtitleConversionOptions, derive_cht_path


@dataclass(frozen=True)
class PreparedTraditionalization:
    source: Path
    output: Path
    result: HanvertResult


def _input_error(message: str, *, path: Path, **details: Any) -> BmlsubError:
    return BmlsubError(
        message,
        code=ErrorCode.INPUT_MISSING,
        details={"path": str(path), **details},
    )


def resolve_fanhua_input(value: str | Path, *, launch_directory: Path | None = None) -> Path:
    """Resolve a fanhua input relative to the command launch directory."""

    launch = (launch_directory or Path.cwd()).resolve()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = launch / candidate
    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise _input_error("fanhua input does not exist", path=candidate) from exc


def _is_traditional_output(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith(".cht.ass") or name.endswith(".cht&jpn.ass")


def discover_fanhua_sources(path: Path) -> list[Path]:
    """Return stable, non-recursive ASS inputs without generated CHT files."""

    if path.is_file():
        if path.suffix.casefold() != ".ass":
            raise _input_error(
                "fanhua only supports ASS subtitle files",
                path=path,
                supported_suffixes=[".ass"],
            )
        if _is_traditional_output(path):
            raise _input_error("fanhua input is already a CHT subtitle", path=path)
        return [path]
    if not path.is_dir():
        raise _input_error("fanhua input is not a regular file or directory", path=path)

    sources = sorted(
        (
            item.resolve()
            for item in path.iterdir()
            if item.is_file()
            and item.suffix.casefold() == ".ass"
            and not _is_traditional_output(item)
        ),
        key=lambda item: (item.name.casefold(), item.name),
    )
    if not sources:
        raise _input_error(
            "fanhua directory contains no source ASS subtitle files",
            path=path,
            supported_suffixes=[".ass"],
        )
    return sources


def _prepare(
    source: Path,
    output: Path,
    *,
    options: SubtitleConversionOptions,
    provider: ConverterProvider | None,
) -> PreparedTraditionalization:
    try:
        content, _ = read_ass(source)
    except (OSError, ValueError) as exc:
        raise _input_error(
            "fanhua input could not be read as an ASS subtitle",
            path=source,
            reason=str(exc),
        ) from exc
    result = convert_ass(
        content,
        converter=options.converter,
        api_url=options.api_url,
        timeout=options.timeout,
        full_file=options.full_file,
        provider=provider,
    )
    return PreparedTraditionalization(source, output, result)


def _write_atomic(artifact: PreparedTraditionalization) -> None:
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
        validate_ass_conversion(
            artifact.source,
            temporary,
            allow_full_file=artifact.result.conversion_mode == "full_file",
        )
        os.chmod(temporary, 0o644)
        os.replace(temporary, artifact.output)
    except Exception as exc:
        if isinstance(exc, BmlsubError):
            raise
        raise ArtifactCommitError(
            "fanhua output could not be validated or committed",
            details={
                "source": str(artifact.source),
                "output": str(artifact.output),
                "reason": str(exc),
            },
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def run_fanhua(
    value: str | Path = ".",
    *,
    launch_directory: Path | None = None,
    options: SubtitleConversionOptions | None = None,
    provider: ConverterProvider | None = None,
) -> dict[str, Any]:
    """Traditionalize one ASS file or a non-recursive directory batch."""

    launch = (launch_directory or Path.cwd()).resolve()
    requested = resolve_fanhua_input(value, launch_directory=launch)
    settings = options or SubtitleConversionOptions()
    sources = discover_fanhua_sources(requested)
    output_map: dict[str, tuple[Path, Path]] = {}
    for source in sources:
        output = derive_cht_path(source).resolve()
        collision_key = str(output).casefold()
        if collision_key in output_map:
            first_source, first_output = output_map[collision_key]
            raise OutputValidationError(
                "multiple fanhua inputs map to the same output path",
                details={
                    "first_source": str(first_source),
                    "second_source": str(source),
                    "output": str(first_output),
                },
            )
        output_map[collision_key] = (source, output)
    prepared = [
        _prepare(source, output, options=settings, provider=provider)
        for source, output in output_map.values()
    ]

    written = [artifact for artifact in prepared if artifact.result.no_op_reason is None]
    for artifact in written:
        _write_atomic(artifact)

    artifacts = []
    for artifact in prepared:
        result = artifact.result
        item: dict[str, Any] = {
            "source": str(artifact.source),
            "status": "skipped" if result.no_op_reason else "succeeded",
            "converted_events": result.converted_events,
            "converted_units": result.converted_units,
            "length_changed_events": result.length_changed_events,
            "skipped_mixed_groups": result.skipped_mixed_groups,
            "conversion_mode": result.conversion_mode,
        }
        if result.no_op_reason:
            item["reason"] = result.no_op_reason
        else:
            item["path"] = str(artifact.output)
            item["bytes"] = artifact.output.stat().st_size
        artifacts.append(item)

    return {
        "status": "succeeded" if written else "skipped",
        "operation": "fanhua",
        "input": str(requested),
        "processed_count": len(written),
        "skipped_count": len(prepared) - len(written),
        "converter": settings.converter,
        "artifacts": artifacts,
    }
