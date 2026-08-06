"""Validation for native keyring backends."""

from __future__ import annotations

from dataclasses import dataclass
import platform


@dataclass(frozen=True)
class KeyringBackendInfo:
    identity: str
    kind: str | None
    native: bool


def inspect_keyring_backend(backend: object, *, system: str | None = None) -> KeyringBackendInfo:
    identity = f"{type(backend).__module__}.{type(backend).__name__}".lower()
    host = (system or platform.system()).lower()
    kind = _backend_kind(identity)
    expected = {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(host)
    return KeyringBackendInfo(identity=identity, kind=kind, native=kind == expected)


def _backend_kind(identity: str) -> str | None:
    if "keyring.backends.macos" in identity or "keyring.backends.os_x" in identity:
        return "macos"
    if "keyring.backends.windows" in identity:
        return "windows"
    if any(token in identity for token in (
        "keyring.backends.secretservice", "keyring.backends.kwallet",
        "keyring.backends.libsecret",
    )):
        return "linux"
    return None
