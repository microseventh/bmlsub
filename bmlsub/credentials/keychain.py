"""macOS Login Keychain adapter with an injectable test boundary."""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    def get(self, service: str, account: str) -> str | None: ...
    def set(self, service: str, account: str, value: str) -> None: ...
    def delete(self, service: str, account: str) -> None: ...
    def exists(self, service: str, account: str) -> bool: ...


class MacOSKeychainSecretStore:
    """Store generic passwords through the native macOS keyring backend."""

    def __init__(self, backend: object | None = None) -> None:
        if backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError(
                    "macOS Keychain support requires the bmlsub secrets dependency"
                ) from exc
            backend = keyring.get_keyring()
        self._backend = backend
        identity = f"{type(backend).__module__}.{type(backend).__name__}".lower()
        if "macos" not in identity and "os_x" not in identity:
            raise RuntimeError("the active keyring backend is not macOS Keychain")

    def get(self, service: str, account: str) -> str | None:
        try:
            return self._backend.get_password(service, account)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("macOS Keychain item could not be read") from exc

    def set(self, service: str, account: str, value: str) -> None:
        try:
            self._backend.set_password(service, account, value)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("macOS Keychain item could not be written") from exc

    def delete(self, service: str, account: str) -> None:
        try:
            self._backend.delete_password(service, account)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("macOS Keychain item could not be restored") from exc

    def exists(self, service: str, account: str) -> bool:
        return self.get(service, account) is not None
