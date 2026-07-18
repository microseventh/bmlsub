"""Restricted SSH+rclone adapter for verified single-object remote pulls."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import shlex
from typing import Protocol

from ..execution.errors import BmlsubError, ErrorCode
from ..execution.process_runner import PROCESS_RUNNER_VERSION, ProcessRunner
from .external_profiles import RemotePullProfile


REMOTE_PULL_ADAPTER_VERSION = "ssh-rclone-pull-v1"
REMOTE_FILE_RECEIPT_SCHEMA = "remote-file-receipt-v1"


@dataclass(frozen=True)
class RemoteFileIdentity:
    path: str
    size: int
    sha256: str

    def bounded(self) -> dict[str, str | int]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


class RemotePullClient(Protocol):
    @property
    def version(self) -> str: ...
    def pull(self, profile: RemotePullProfile, *, run_id: str,
             expected_size: int, expected_sha256: str) -> RemoteFileIdentity: ...
    def inspect(self, profile: RemotePullProfile) -> RemoteFileIdentity: ...


class SSHRclonePullClient:
    def __init__(self, *, ssh: Path | str = "ssh", runner: ProcessRunner | None = None) -> None:
        self.ssh = str(ssh)
        self.runner = runner or ProcessRunner(timeout=3600, max_stdout_bytes=65536, max_stderr_bytes=65536)

    @property
    def version(self) -> str:
        return f"{REMOTE_PULL_ADAPTER_VERSION}/{PROCESS_RUNNER_VERSION}"

    def pull(self, profile: RemotePullProfile, *, run_id: str,
             expected_size: int, expected_sha256: str) -> RemoteFileIdentity:
        target = PurePosixPath(profile.target_path)
        candidate = str(target.with_name(f".{target.name}.bmlsub-{run_id}.part"))
        source = f"{profile.rclone_remote}:{profile.bucket}/{profile.object_key}"
        script = "\n".join((
            "set -eu",
            f"target={shlex.quote(profile.target_path)}",
            f"candidate={shlex.quote(candidate)}",
            "parent=$(dirname -- \"$target\")",
            "mkdir -p -- \"$parent\"",
            "cleanup() { rm -f -- \"$candidate\"; }",
            "trap cleanup EXIT HUP INT TERM",
            f"rclone copyto -- {shlex.quote(source)} \"$candidate\"",
            "size=$(wc -c < \"$candidate\" | tr -d ' ')",
            "if command -v sha256sum >/dev/null 2>&1; then sha=$(sha256sum \"$candidate\" | cut -d ' ' -f 1); else sha=$(shasum -a 256 \"$candidate\" | cut -d ' ' -f 1); fi",

            f"test \"$size\" = {expected_size}",
            f"test \"$sha\" = {shlex.quote(expected_sha256)}",
            "if test -e \"$target\"; then existing_size=$(wc -c < \"$target\" | tr -d ' '); if command -v sha256sum >/dev/null 2>&1; then existing_sha=$(sha256sum \"$target\" | cut -d ' ' -f 1); else existing_sha=$(shasum -a 256 \"$target\" | cut -d ' ' -f 1); fi; test \"$existing_size\" = \"$size\"; test \"$existing_sha\" = \"$sha\"; fi",
            "mv -f -- \"$candidate\" \"$target\"",
            "trap - EXIT HUP INT TERM",
            "size=$(wc -c < \"$target\" | tr -d ' ')",
            "if command -v sha256sum >/dev/null 2>&1; then sha=$(sha256sum \"$target\" | cut -d ' ' -f 1); else sha=$(shasum -a 256 \"$target\" | cut -d ' ' -f 1); fi",

            "printf '{\"size\":%s,\"sha256\":\"%s\"}\\n' \"$size\" \"$sha\"",
        ))
        result = self.runner.run(
            [self.ssh, profile.ssh_alias, f"sh -c {shlex.quote(script)}"], timeout=profile.timeout,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        identity = self._parse(profile, result.stdout_text())
        if identity.size != expected_size or identity.sha256 != expected_sha256:
            raise BmlsubError("remote pulled file failed final integrity validation", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        return identity

    def inspect(self, profile: RemotePullProfile) -> RemoteFileIdentity:
        target = shlex.quote(profile.target_path)
        script = "\n".join((
            "set -eu",
            f"target={target}",
            "test -f \"$target\"",
            "size=$(wc -c < \"$target\" | tr -d ' ')",
            "if command -v sha256sum >/dev/null 2>&1; then sha=$(sha256sum \"$target\" | cut -d ' ' -f 1); else sha=$(shasum -a 256 \"$target\" | cut -d ' ' -f 1); fi",

            "printf '{\"size\":%s,\"sha256\":\"%s\"}\\n' \"$size\" \"$sha\"",
        ))
        result = self.runner.run(
            [self.ssh, profile.ssh_alias, f"sh -c {shlex.quote(script)}"], timeout=min(profile.timeout, 300),
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        return self._parse(profile, result.stdout_text())

    @staticmethod
    def _parse(profile: RemotePullProfile, output: str) -> RemoteFileIdentity:
        try:
            data = json.loads(output.strip())
            return RemoteFileIdentity(profile.target_path, int(data["size"]), str(data["sha256"]))
        except Exception as exc:
            raise BmlsubError(
                "remote integrity response was invalid", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                details={"exception_type": type(exc).__name__},
            ) from exc
