"""Bounded ffprobe client for source and generated media."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..execution.errors import BmlsubError, ErrorCode
from ..execution.process_runner import ProcessRunner
from .models import MediaSummary


class FFprobeClient:
    def __init__(self, executable: Path | str = "ffprobe", *, timeout: float = 30.0,
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
        return self.runner.version(self.executable)

    def inspect(self, path: Path | str) -> MediaSummary:
        summary = self.inspect_media(path)
        if not summary.has_video:
            raise BmlsubError(
                "input media does not contain a video stream",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            )
        return summary

    def _run(self, argv: list[str], *, output_limit: int) -> str:
        return self.runner.run(
            argv, timeout=self.timeout, max_stdout_bytes=output_limit,
            max_stderr_bytes=self.max_diagnostic_bytes,
        ).stdout_text()

    def inspect_media(self, path: Path | str) -> MediaSummary:
        source = Path(path).expanduser().resolve()
        output = self._run([
            self.executable, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", "--", str(source),
        ], output_limit=self.max_output_bytes)
        try:
            data: Any = json.loads(output)
        except json.JSONDecodeError as exc:
            raise BmlsubError(
                "ffprobe returned invalid JSON", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not isinstance(data, dict):
            raise BmlsubError(
                "ffprobe returned an invalid document",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            )
        return MediaSummary.from_probe(data)

    def inspect_expected(self, path: Path | str, *, stream_type: str,
                         count: int = 1) -> MediaSummary:
        summary = self.inspect_media(path)
        matching = [item for item in summary.streams if item.codec_type == stream_type]
        if len(matching) != count or len(summary.streams) != count:
            raise BmlsubError(
                "media output has unexpected streams",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                details={
                    "expected_type": stream_type, "expected_count": count,
                    "actual_count": len(matching), "total_streams": len(summary.streams),
                },
            )
        return summary
