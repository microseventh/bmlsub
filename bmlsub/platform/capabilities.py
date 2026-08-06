"""Runtime capability reporting for CLI and future client integrations."""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
from typing import Iterable

from .locks import lock_backend_name
from .tools import ExecutableInfo, discover_executables


@dataclass(frozen=True)
class PlatformCapabilities:
    system: str
    machine: str
    posix_permissions: bool
    directory_fsync: bool
    lock_backend: str
    mlx_whisper_supported: bool
    executables: dict[str, ExecutableInfo]

    def to_dict(self) -> dict[str, object]:
        return {
            "system": self.system,
            "machine": self.machine,
            "posix_permissions": self.posix_permissions,
            "directory_fsync": self.directory_fsync,
            "lock_backend": self.lock_backend,
            "mlx_whisper_supported": self.mlx_whisper_supported,
            "executables": {
                name: {"available": item.available, "path": str(item.path) if item.path else None}
                for name, item in self.executables.items()
            },
        }


def runtime_capabilities(executables: Iterable[str] = ()) -> PlatformCapabilities:
    system = platform.system()
    return PlatformCapabilities(
        system=system,
        machine=platform.machine(),
        posix_permissions=os.name != "nt",
        directory_fsync=os.name != "nt",
        lock_backend=lock_backend_name(),
        mlx_whisper_supported=system == "Darwin",
        executables=discover_executables(executables),
    )
