"""Portable filesystem durability and private-file helpers."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import IO


def fsync_file(file_or_path: IO[object] | Path | str) -> None:
    """Flush a regular file, accepting either an open handle or a path."""
    if hasattr(file_or_path, "fileno"):
        os.fsync(file_or_path.fileno())  # type: ignore[union-attr]
        return
    with Path(file_or_path).open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path | str) -> bool:
    """Flush directory metadata where the host supports directory handles.

    Windows does not expose POSIX directory fsync semantics through ``os.open``.
    The preceding atomic replace is still valid there, so callers can treat a
    false result as an unavailable durability enhancement rather than failure.
    """
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(Path(path), flags)
    except (OSError, TypeError):
        return False
    try:
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        os.close(descriptor)
    return True


def is_owned_by_current_user(stat_result: os.stat_result) -> bool:
    """Apply the numeric-owner check only on platforms that expose it."""
    getuid = getattr(os, "getuid", None)
    owner = getattr(stat_result, "st_uid", None)
    return getuid is None or owner is None or owner == getuid()


def has_private_permissions(stat_result: os.stat_result) -> bool:
    """Check group/other mode bits on POSIX; Windows security is ACL-based."""
    return os.name == "nt" or not bool(stat_result.st_mode & 0o077)


def set_private_permissions(target: int | Path | str, mode: int = 0o600) -> bool:
    """Restrict a descriptor/path on POSIX and avoid misleading chmod on Windows."""
    if os.name == "nt" or sys.platform == "win32":
        return False
    if isinstance(target, int):
        os.fchmod(target, mode)
    else:
        os.chmod(Path(target), mode)
    return True
