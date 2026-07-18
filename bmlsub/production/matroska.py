"""Bounded mkvmerge identification for Matroska production outputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from ..execution.errors import BmlsubError, ErrorCode
from ..execution.process_runner import ProcessRunner


@dataclass(frozen=True)
class MatroskaTrack:
    track_type: str
    codec_id: str | None
    language: str | None
    name: str | None
    is_default: bool
    is_forced: bool


@dataclass(frozen=True)
class MatroskaAttachment:
    file_name: str
    content_type: str
    size: int | None


@dataclass(frozen=True)
class MatroskaSummary:
    container_type: str
    tracks: tuple[MatroskaTrack, ...]
    attachments: tuple[MatroskaAttachment, ...]
    chapter_count: int


class MKVmergeClient:
    def __init__(self, executable: Path | str = "mkvmerge", *, timeout: float = 30.0,
                 max_output_bytes: int = 2 * 1024 * 1024,
                 max_diagnostic_bytes: int = 8192,
                 runner: ProcessRunner | None = None) -> None:
        self.executable = str(executable)
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.max_diagnostic_bytes = max_diagnostic_bytes
        self.runner = runner or ProcessRunner(
            timeout=timeout, max_stdout_bytes=max_output_bytes,
            max_stderr_bytes=max_diagnostic_bytes,
        )

    def version(self) -> str:
        result = self.runner.run(
            [self.executable, "--version"], timeout=min(self.timeout, 30.0),
            max_stdout_bytes=65536, max_stderr_bytes=self.max_diagnostic_bytes,
        )
        lines = result.stdout_text().splitlines()
        return (lines[0] if lines else "unknown")[:256]

    def identify(self, path: Path | str) -> MatroskaSummary:
        source = Path(path).expanduser().resolve()
        result = self.runner.run([
            self.executable, "--identification-format", "json", "--identify", str(source),
        ], timeout=self.timeout, max_stdout_bytes=self.max_output_bytes,
           max_stderr_bytes=self.max_diagnostic_bytes)
        try:
            document: Any = json.loads(result.stdout_text())
        except json.JSONDecodeError as exc:
            raise BmlsubError(
                "mkvmerge returned invalid JSON", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not isinstance(document, Mapping):
            raise BmlsubError(
                "mkvmerge returned an invalid document", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            )
        container = document.get("container")
        properties = container.get("properties") if isinstance(container, Mapping) else {}
        container_type = str(properties.get("container_type") or "")
        container_name = str(container.get("type") or "") if isinstance(container, Mapping) else ""
        if container_name.lower() != "matroska":
            raise BmlsubError(
                "mkvmerge output is not Matroska", code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            )
        tracks = []
        raw_tracks = document.get("tracks")
        if isinstance(raw_tracks, list):
            for item in raw_tracks[:256]:
                if not isinstance(item, Mapping):
                    continue
                props = item.get("properties") if isinstance(item.get("properties"), Mapping) else {}
                tracks.append(MatroskaTrack(
                    track_type=str(item.get("type") or "unknown")[:32],
                    codec_id=_text(props.get("codec_id"), 128),
                    language=_text(props.get("language_ietf") or props.get("language"), 64),
                    name=_text(props.get("track_name"), 256),
                    is_default=bool(props.get("default_track", False)),
                    is_forced=bool(props.get("forced_track", False)),
                ))
        attachments = []
        raw_attachments = document.get("attachments")
        if isinstance(raw_attachments, list):
            for item in raw_attachments[:256]:
                if not isinstance(item, Mapping):
                    continue
                size = item.get("size")
                attachments.append(MatroskaAttachment(
                    file_name=str(item.get("file_name") or "")[:512],
                    content_type=str(item.get("content_type") or "application/octet-stream")[:128],
                    size=size if isinstance(size, int) else None,
                ))
        chapters = document.get("chapters")
        chapter_count = len(chapters) if isinstance(chapters, list) else 0
        return MatroskaSummary(
            container_type=container_type or container_name,
            tracks=tuple(tracks), attachments=tuple(attachments), chapter_count=chapter_count,
        )


def _text(value: Any, limit: int) -> str | None:
    return str(value)[:limit] if value is not None else None
