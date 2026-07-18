"""Bounded argv-only subprocess execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
from typing import Sequence

from .errors import BmlsubError, ErrorCode


PROCESS_RUNNER_VERSION = "process-runner-v1"


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class ProcessRunner:
    def __init__(self, *, timeout: float = 600.0,
                 max_stdout_bytes: int = 2 * 1024 * 1024,
                 max_stderr_bytes: int = 64 * 1024) -> None:
        if timeout <= 0 or max_stdout_bytes <= 0 or max_stderr_bytes <= 0:
            raise ValueError("process limits must be positive")
        self.timeout = timeout
        self.max_stdout_bytes = max_stdout_bytes
        self.max_stderr_bytes = max_stderr_bytes

    def run(self, argv: Sequence[Path | str], *, timeout: float | None = None,
            check: bool = True, max_stdout_bytes: int | None = None,
            max_stderr_bytes: int | None = None) -> ProcessResult:
        normalized = tuple(str(item) for item in argv)
        if not normalized or not normalized[0]:
            raise ValueError("process argv must not be empty")
        stdout_limit = max_stdout_bytes or self.max_stdout_bytes
        stderr_limit = max_stderr_bytes or self.max_stderr_bytes
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                completed = subprocess.run(
                    normalized, stdin=subprocess.DEVNULL,
                    stdout=stdout_file, stderr=stderr_file,
                    timeout=timeout or self.timeout, check=False,
                )
            except FileNotFoundError as exc:
                raise BmlsubError(
                    "process executable was not found", code=ErrorCode.INPUT_MISSING,
                    details={"executable": normalized[0], "exception_type": type(exc).__name__},
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise BmlsubError(
                    "external process timed out", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                    retryable=True,
                    details={"executable": normalized[0], "exception_type": type(exc).__name__},
                ) from exc
            stdout = self._read_bounded(stdout_file, stdout_limit, "stdout")
            stderr = self._read_bounded(stderr_file, stderr_limit, "stderr")
        result = ProcessResult(normalized, completed.returncode, stdout, stderr)
        if check and completed.returncode != 0:
            raise BmlsubError(
                "external process failed", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                details={
                    "executable": normalized[0], "returncode": completed.returncode,
                    "diagnostic": result.stderr_text().strip()[:stderr_limit],
                },
            )
        return result

    def version(self, executable: Path | str) -> str:
        result = self.run(
            [executable, "-version"], timeout=min(self.timeout, 30.0),
            max_stdout_bytes=65536, max_stderr_bytes=8192,
        )
        first = result.stdout_text().splitlines()
        return (first[0] if first else "unknown")[:256]

    @staticmethod
    def _read_bounded(handle, limit: int, stream: str) -> bytes:
        handle.seek(0, 2)
        size = handle.tell()
        if size > limit:
            raise BmlsubError(
                f"process {stream} exceeded the configured limit",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                details={"stream": stream, "output_bytes": size, "limit_bytes": limit},
            )
        handle.seek(0)
        return handle.read(limit)
