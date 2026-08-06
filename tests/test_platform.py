from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from bmlsub.credentials.keychain import (
    MacOSKeychainSecretStore, SystemKeyringSecretStore,
)
from bmlsub.platform import (
    file_lock, fsync_directory, has_private_permissions,
    inspect_keyring_backend, runtime_capabilities,
)
from bmlsub.workstation.state import atomic_write_json


def fake_backend(module: str) -> object:
    values: dict[tuple[str, str], str] = {}

    def get_password(self, service, account):
        return values.get((service, account))

    def set_password(self, service, account, value):
        values[(service, account)] = value

    def delete_password(self, service, account):
        values.pop((service, account), None)

    backend_type = type("Keyring", (), {
        "__module__": module,
        "get_password": get_password,
        "set_password": set_password,
        "delete_password": delete_password,
    })
    return backend_type()


class PlatformFoundationTests(unittest.TestCase):
    def test_native_keyring_backends_are_selected_per_platform(self):
        cases = (
            ("keyring.backends.macOS", "Darwin", "macos"),
            ("keyring.backends.Windows", "Windows", "windows"),
            ("keyring.backends.SecretService", "Linux", "linux"),
            ("keyring.backends.kwallet", "Linux", "linux"),
        )
        for module, system, kind in cases:
            with self.subTest(system=system, module=module):
                backend = fake_backend(module)
                info = inspect_keyring_backend(backend, system=system)
                self.assertTrue(info.native)
                self.assertEqual(info.kind, kind)
                store = SystemKeyringSecretStore(backend, platform_system=system)
                store.set("service", "account", "secret")
                self.assertEqual(store.get("service", "account"), "secret")
                store.delete("service", "account")
                self.assertFalse(store.exists("service", "account"))

    def test_non_native_and_plaintext_keyrings_are_rejected(self):
        with self.assertRaises(RuntimeError):
            SystemKeyringSecretStore(
                fake_backend("keyring.backends.macOS"), platform_system="Windows",
            )
        with self.assertRaises(RuntimeError):
            SystemKeyringSecretStore(
                fake_backend("keyrings.alt.file"), platform_system="Linux",
            )

    def test_macos_compatibility_adapter_still_requires_keychain(self):
        store = MacOSKeychainSecretStore(fake_backend("keyring.backends.macOS"))
        store.set("service", "account", "value")
        self.assertEqual(store.get("service", "account"), "value")
        with self.assertRaises(RuntimeError):
            MacOSKeychainSecretStore(fake_backend("keyring.backends.Windows"))

    def test_file_lock_and_durable_json_support_non_ascii_paths(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "字幕 工作区"
            root.mkdir()
            lock_path = root / "状态.lock"
            with file_lock(lock_path) as handle:
                self.assertFalse(handle.closed)
            self.assertTrue(lock_path.is_file())

            target = root / "状态.json"
            atomic_write_json(target, {"标题": "测试", "items": [1, 2]})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["标题"], "测试")
            self.assertIsInstance(fsync_directory(root), bool)

    def test_permission_check_can_represent_posix_and_windows_results(self):
        self.assertTrue(has_private_permissions(SimpleNamespace(st_mode=0o100600)))
        if os.name != "nt":
            self.assertFalse(has_private_permissions(SimpleNamespace(st_mode=0o100644)))

    def test_runtime_capability_report_is_serializable(self):
        report = runtime_capabilities(("python", "bmlsub-command-that-does-not-exist"))
        payload = report.to_dict()
        self.assertIn(payload["system"], {"Darwin", "Linux", "Windows"})
        self.assertIn(payload["lock_backend"], {"portalocker", "fcntl", "msvcrt"})
        self.assertFalse(
            payload["executables"]["bmlsub-command-that-does-not-exist"]["available"]
        )


if __name__ == "__main__":
    unittest.main()
