"""Validated native system-keyring adapters with an injectable test boundary."""

from __future__ import annotations

from typing import Protocol

from ..platform.secrets import inspect_keyring_backend


class SecretStore(Protocol):
    def get(self, service: str, account: str) -> str | None: ...
    def set(self, service: str, account: str, value: str) -> None: ...
    def delete(self, service: str, account: str) -> None: ...
    def exists(self, service: str, account: str) -> bool: ...


class SystemKeyringSecretStore:
    """Store secrets only through the host platform's native keyring backend."""

    def __init__(self, backend: object | None = None, *,
                 platform_system: str | None = None) -> None:
        if backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError(
                    "system keyring support requires the bmlsub secrets dependency"
                ) from exc
            backend = keyring.get_keyring()
        self._backend = backend
        info = inspect_keyring_backend(backend, system=platform_system)
        if not info.native:
            raise RuntimeError(
                f"the active keyring backend is not a native {platform_system or 'system'} keyring"
            )

    def get(self, service: str, account: str) -> str | None:
        try:
            return self._backend.get_password(service, account)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("system keyring item could not be read") from exc

    def set(self, service: str, account: str, value: str) -> None:
        try:
            self._backend.set_password(service, account, value)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("system keyring item could not be written") from exc

    def delete(self, service: str, account: str) -> None:
        try:
            self._backend.delete_password(service, account)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError("system keyring item could not be restored") from exc

    def exists(self, service: str, account: str) -> bool:
        return self.get(service, account) is not None


class MacOSKeychainSecretStore(SystemKeyringSecretStore):
    """Backward-compatible adapter that specifically requires macOS Keychain."""

    def __init__(self, backend: object | None = None) -> None:
        super().__init__(backend, platform_system="Darwin")


def default_secret_store() -> SystemKeyringSecretStore:
    return SystemKeyringSecretStore()
