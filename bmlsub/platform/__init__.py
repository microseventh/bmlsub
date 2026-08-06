"""Small platform boundaries used by the portable bmlsub core."""

from .capabilities import PlatformCapabilities, runtime_capabilities
from .filesystem import (
    fsync_directory,
    fsync_file,
    has_private_permissions,
    is_owned_by_current_user,
    set_private_permissions,
)
from .locks import FileLock, file_lock, lock_backend_name
from .secrets import KeyringBackendInfo, inspect_keyring_backend
from .tools import ExecutableInfo, discover_executable, discover_executables

__all__ = [
    "ExecutableInfo",
    "FileLock",
    "KeyringBackendInfo",
    "PlatformCapabilities",
    "discover_executable",
    "discover_executables",
    "file_lock",
    "fsync_directory",
    "fsync_file",
    "has_private_permissions",
    "inspect_keyring_backend",
    "is_owned_by_current_user",
    "lock_backend_name",
    "runtime_capabilities",
    "set_private_permissions",
]
