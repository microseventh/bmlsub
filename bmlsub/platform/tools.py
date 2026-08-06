"""Executable discovery without shell-specific assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Iterable


@dataclass(frozen=True)
class ExecutableInfo:
    name: str
    path: Path | None

    @property
    def available(self) -> bool:
        return self.path is not None


def discover_executable(name: str) -> ExecutableInfo:
    resolved = shutil.which(name)
    return ExecutableInfo(name=name, path=Path(resolved).resolve() if resolved else None)


def discover_executables(names: Iterable[str]) -> dict[str, ExecutableInfo]:
    return {name: discover_executable(name) for name in names}
