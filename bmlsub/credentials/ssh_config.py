"""Read-only OpenSSH alias resolution for credential manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable, Sequence


@dataclass(frozen=True)
class SSHConfigIdentity:
    alias: str
    host: str
    user: str
    port: int
    identity_files: tuple[str, ...]

    def bounded(self) -> dict[str, object]:
        return {
            "alias": self.alias, "host": self.host, "user": self.user,
            "port": self.port, "identity_files": list(self.identity_files),
        }


class SSHConfigResolver:
    def __init__(self, *, ssh: Path | str = "ssh",
                 run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self.ssh = str(ssh)
        self._run = run

    def resolve(self, alias: str) -> SSHConfigIdentity:
        result = self._run(
            [self.ssh, "-G", alias], capture_output=True, text=True,
            check=False, timeout=10,
        )
        if result.returncode != 0:
            raise ValueError("SSH alias could not be resolved")
        values: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(" ")
            if separator:
                values.setdefault(key.lower(), []).append(value.strip())
        try:
            host = values["hostname"][0]
            user = values["user"][0]
            port = int(values["port"][0])
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError("SSH alias resolved to an incomplete configuration") from exc
        identities = tuple(values.get("identityfile", ()))
        return SSHConfigIdentity(alias, host, user, port, identities)

    @staticmethod
    def validate(identity: SSHConfigIdentity, settings: dict[str, object]) -> None:
        expected = {
            "expected_host": identity.host,
            "expected_user": identity.user,
            "expected_port": identity.port,
        }
        for key, actual in expected.items():
            if key in settings and settings[key] != actual:
                raise ValueError(f"SSH alias {identity.alias} does not match {key}")
