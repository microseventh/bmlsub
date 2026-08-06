"""Cross-platform advisory file locking."""

from __future__ import annotations

from contextlib import AbstractContextManager
import importlib
from pathlib import Path
import os
from types import ModuleType
from typing import IO

from .filesystem import set_private_permissions


def _portalocker() -> ModuleType | None:
    try:
        return importlib.import_module("portalocker")
    except ImportError:
        return None


def lock_backend_name() -> str:
    if _portalocker() is not None:
        return "portalocker"
    return "msvcrt" if os.name == "nt" else "fcntl"


class FileLock(AbstractContextManager[IO[str]]):
    """An exclusive process lock backed by portalocker when installed."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._handle: IO[str] | None = None
        self._backend: ModuleType | None = None

    def __enter__(self) -> IO[str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        set_private_permissions(self.path)
        backend = _portalocker()
        try:
            if backend is not None:
                backend.lock(handle, backend.LOCK_EX)  # type: ignore[attr-defined]
            elif os.name == "nt":
                self._lock_windows(handle)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                backend = fcntl
        except Exception:
            handle.close()
            raise
        self._handle = handle
        self._backend = backend
        return handle

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        handle = self._handle
        backend = self._backend
        if handle is None:
            return
        try:
            if backend is not None and backend.__name__ == "portalocker":
                backend.unlock(handle)  # type: ignore[attr-defined]
            elif os.name == "nt":
                self._unlock_windows(handle)
            elif backend is not None:
                backend.flock(handle.fileno(), backend.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            handle.close()
            self._handle = None
            self._backend = None

    @staticmethod
    def _lock_windows(handle: IO[str]) -> None:
        msvcrt = importlib.import_module("msvcrt")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    @staticmethod
    def _unlock_windows(handle: IO[str]) -> None:
        msvcrt = importlib.import_module("msvcrt")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def file_lock(path: Path | str) -> FileLock:
    return FileLock(path)
