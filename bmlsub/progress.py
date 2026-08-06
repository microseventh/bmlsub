"""Lightweight progress events and reporters for interactive workflows."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import math
import shutil
import sys
import threading
import time
import unicodedata
from typing import IO, Iterator, Mapping, Protocol


_TERMINAL_STATES = {"completed", "reused", "failed"}
_SPINNER_FRAMES = ("|", "/", "-", "\\")


@dataclass(frozen=True)
class ProgressEvent:
    phase: str
    step: str
    label: str
    state: str
    current: int | float | None = None
    total: int | float | None = None
    unit: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("phase", "step", "label", "state"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"progress {field_name} must be a non-empty string")
        for field_name in ("current", "total"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"progress {field_name} must be numeric")
            if not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"progress {field_name} must be finite and non-negative")
        if self.unit is not None and not isinstance(self.unit, str):
            raise ValueError("progress unit must be a string")
        if self.detail is not None and not isinstance(self.detail, str):
            raise ValueError("progress detail must be a string")


class ProgressReporter(Protocol):
    def report(self, event: ProgressEvent) -> None: ...


class NullProgressReporter:
    """Reporter used when progress output is disabled or not configured."""

    def report(self, event: ProgressEvent) -> None:
        return None

    def emit(self, event: ProgressEvent) -> None:
        self.report(event)


class TerminalProgressReporter:
    """Render one dynamic progress line and stable terminal-state lines."""

    def __init__(self, stream: IO[str] | None = None, *, enabled: bool | None = None,
                 clock=time.monotonic, width: int | None = None,
                 refresh_interval: float = 7.0) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = self._isatty() if enabled is None else bool(enabled)
        self._clock = clock
        if width is not None and width < 20:
            raise ValueError("progress terminal width must be at least 20")
        if refresh_interval < 0:
            raise ValueError("progress refresh interval must be non-negative")
        self._width = width
        self.refresh_interval = float(refresh_interval)
        self._lock = threading.RLock()
        self._started: dict[tuple[str, str], float] = {}
        self._frame = 0
        self._active_width = 0
        self._active_key: tuple[str, str] | None = None
        self._last_render_at: float | None = None

    def report(self, event: ProgressEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            key = (event.phase, event.step)
            now = float(self._clock())
            started = self._started.setdefault(key, now)
            if event.state in _TERMINAL_STATES:
                self._clear_dynamic_line()
                self.stream.write(_truncate_display(
                    self._terminal_line(event), self._line_width(),
                ) + "\n")
                self.stream.flush()
                self._started.pop(key, None)
                return
            if (key == self._active_key and self._last_render_at is not None
                    and now - self._last_render_at < self.refresh_interval):
                return
            line = self._dynamic_line(event, elapsed=max(0.0, now - started))
            line = _truncate_display(line, self._line_width())
            line_width = _display_width(line)
            padding = " " * max(0, self._active_width - line_width)
            self.stream.write(f"\r{line}{padding}")
            self.stream.flush()
            self._active_width = line_width
            self._active_key = key
            self._last_render_at = now
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)

    def emit(self, event: ProgressEvent) -> None:
        self.report(event)

    def close(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._clear_dynamic_line()
            self.stream.flush()

    def _isatty(self) -> bool:
        try:
            return bool(self.stream.isatty())
        except (AttributeError, OSError):
            return False

    def _line_width(self) -> int:
        if self._width is not None:
            return self._width
        try:
            columns = shutil.get_terminal_size(fallback=(100, 24)).columns
        except OSError:
            columns = 100
        return max(20, columns - 1)

    def _dynamic_line(self, event: ProgressEvent, *, elapsed: float) -> str:
        has_total = event.total is not None and event.total > 0 and event.current is not None
        fields = ([_one_line(event.label)] if has_total
                  else [f"[{_SPINNER_FRAMES[self._frame]}]", _one_line(event.label)])
        if has_total:
            percentage = min(100.0, max(0.0, float(event.current) / float(event.total) * 100.0))
            fields.extend((
                _progress_bar(percentage),
                f"{percentage:5.1f}%",
                f"{_number(event.current)}/{_number(event.total)}{_unit_suffix(event.unit)}",
            ))
        elif event.current is not None:
            fields.append(f"{_number(event.current)}{_unit_suffix(event.unit)}")
        fields.append(f"elapsed {_duration(elapsed)}")
        if event.detail:
            fields.append(f"- {_one_line(event.detail)}")
        return " ".join(fields)

    @staticmethod
    def _terminal_line(event: ProgressEvent) -> str:
        line = f"[{event.state}] {_one_line(event.label)}"
        if event.detail:
            line += f" - {_one_line(event.detail)}"
        return line

    def _clear_dynamic_line(self) -> None:
        if self._active_width:
            self.stream.write("\r\x1b[2K\r")
            self._active_width = 0
            self._active_key = None
            self._last_render_at = None


class ProgressTask:
    """Keep one indeterminate task alive while a blocking operation runs."""

    def __init__(self, *, phase: str, step: str, label: str,
                 reporter: ProgressReporter | None = None,
                 current: int | float | None = None,
                 total: int | float | None = None,
                 unit: str | None = None, detail: str | None = None,
                 heartbeat: bool = True,
                 refresh_interval: float = 7.0) -> None:
        if refresh_interval <= 0:
            raise ValueError("progress refresh_interval must be positive")
        self.reporter = reporter if reporter is not None else get_progress_reporter()
        self.phase = phase
        self.step = step
        self.label = label
        self.current = current
        self.total = total
        self.unit = unit
        self.detail = detail
        self.heartbeat = bool(heartbeat)
        self.refresh_interval = refresh_interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._finished = False
        self._task_token: Token[ProgressTask | None] | None = None

    def __enter__(self) -> ProgressTask:
        self._task_token = _CURRENT_TASK.set(self)
        self._report("started")
        if self.heartbeat and isinstance(self.reporter, TerminalProgressReporter) and self.reporter.enabled:
            self._thread = threading.Thread(
                target=self._heartbeat, name=f"bmlsub-progress-{self.step}", daemon=True,
            )
            self._thread.start()
        return self

    def update(self, *, current: int | float | None = None,
               total: int | float | None = None,
               unit: str | None = None, detail: str | None = None) -> None:
        with self._lock:
            if current is not None:
                self.current = current
            if total is not None:
                self.total = total
            if unit is not None:
                self.unit = unit
            if detail is not None:
                self.detail = detail
        self._report("running")

    def finish(self, state: str = "completed", *, detail: str | None = None) -> None:
        if state not in _TERMINAL_STATES:
            raise ValueError(f"unsupported terminal progress state: {state}")
        with self._lock:
            if self._finished:
                return
            if detail is not None:
                self.detail = detail
            self._finished = True
        self._stop_heartbeat()
        self._report(state)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if not self._finished:
                self.finish(
                    "failed" if exc_type is not None else "completed",
                    detail=str(exc) if exc is not None else None,
                )
        finally:
            if self._task_token is not None:
                _CURRENT_TASK.reset(self._task_token)
                self._task_token = None
        return False

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.refresh_interval):
            self._report("running")

    def _stop_heartbeat(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.refresh_interval * 4))
        self._thread = None

    def _report(self, state: str) -> None:
        with self._lock:
            event = ProgressEvent(
                phase=self.phase, step=self.step, label=self.label, state=state,
                current=self.current, total=self.total, unit=self.unit,
                detail=self.detail,
            )
        self.reporter.report(event)


def progress_task(*, phase: str, step: str, label: str,
                  reporter: ProgressReporter | None = None,
                  current: int | float | None = None,
                  total: int | float | None = None,
                  unit: str | None = None, detail: str | None = None,
                  heartbeat: bool = True,
                  refresh_interval: float = 7.0) -> ProgressTask:
    return ProgressTask(
        phase=phase, step=step, label=label, reporter=reporter,
        current=current, total=total, unit=unit, detail=detail,
        heartbeat=heartbeat,
        refresh_interval=refresh_interval,
    )


def finish_progress_task(task: ProgressTask, payload: Mapping[str, object], *,
                         detail: str | None = None) -> str:
    """Finish a task using the status vocabulary returned by pipeline stages."""
    status = str(payload.get("status") or "")
    state = (
        "reused" if status == "skipped"
        else "completed" if status in {"succeeded", "partial"}
        else "failed"
    )
    task.finish(state, detail=detail or (None if state != "failed" else status or "failed"))
    return state


_NULL_REPORTER = NullProgressReporter()
_CURRENT_REPORTER: ContextVar[ProgressReporter] = ContextVar(
    "bmlsub_progress_reporter", default=_NULL_REPORTER,
)
_CURRENT_TASK: ContextVar[ProgressTask | None] = ContextVar(
    "bmlsub_progress_task", default=None,
)


def get_progress_reporter() -> ProgressReporter:
    return _CURRENT_REPORTER.get()


def get_progress_task() -> ProgressTask | None:
    """Return the current outer task for nested observable progress."""
    return _CURRENT_TASK.get()


def set_progress_reporter(reporter: ProgressReporter | None) -> Token[ProgressReporter]:
    return _CURRENT_REPORTER.set(reporter if reporter is not None else _NULL_REPORTER)


def reset_progress_reporter(token: Token[ProgressReporter]) -> None:
    _CURRENT_REPORTER.reset(token)


@contextmanager
def progress_reporter(reporter: ProgressReporter | None) -> Iterator[ProgressReporter]:
    selected = reporter if reporter is not None else _NULL_REPORTER
    token = set_progress_reporter(selected)
    try:
        yield selected
    finally:
        reset_progress_reporter(token)


get_current_progress_reporter = get_progress_reporter
set_current_progress_reporter = set_progress_reporter


def _one_line(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def _duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _number(value: int | float) -> str:
    return str(value) if isinstance(value, int) or value.is_integer() else f"{value:g}"


def _unit_suffix(unit: str | None) -> str:
    return f" {unit.strip()}" if unit and unit.strip() else ""


def _progress_bar(percentage: float, *, width: int = 20) -> str:
    """Return a fixed-width ASCII bar suitable for one-line TTY updates."""
    bounded = min(100.0, max(0.0, float(percentage)))
    filled = int(round(bounded / 100.0 * width))
    if filled >= width:
        return "[" + ("=" * width) + "]"
    if filled <= 0:
        return "[>" + ("-" * (width - 1)) + "]"
    return "[" + ("=" * (filled - 1)) + ">" + ("-" * (width - filled)) + "]"


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def _truncate_display(value: str, width: int) -> str:
    value = _one_line(value)
    if _display_width(value) <= width:
        return value
    suffix = "..."
    available = max(0, width - _display_width(suffix))
    output: list[str] = []
    used = 0
    for character in value:
        character_width = 0 if unicodedata.combining(character) else (
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        )
        if used + character_width > available:
            break
        output.append(character)
        used += character_width
    return "".join(output).rstrip() + suffix


__all__ = [
    "NullProgressReporter",
    "ProgressEvent",
    "ProgressReporter",
    "ProgressTask",
    "TerminalProgressReporter",
    "finish_progress_task",
    "get_current_progress_reporter",
    "get_progress_reporter",
    "get_progress_task",
    "progress_reporter",
    "progress_task",
    "reset_progress_reporter",
    "set_current_progress_reporter",
    "set_progress_reporter",
]
