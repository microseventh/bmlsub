"""Compatibility helpers for the vendored SubsRefine core."""

from __future__ import annotations

try:
    from enum import StrEnum as StrEnum
except ImportError:  # pragma: no cover - exercised only on Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset of :class:`enum.StrEnum`."""

        def __str__(self) -> str:
            return str(self.value)
