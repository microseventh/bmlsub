"""qBittorrent Web API adapter reached through an SSH local tunnel."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

import requests

from ..execution.errors import BmlsubError, ErrorCode
from .credentials import QBittorrentCredentials
from .external_profiles import QBittorrentSeedProfile


QB_ADAPTER_VERSION = "qbittorrent-ssh-tunnel-v5"
QB_VALIDATOR_VERSION = "qbittorrent-seed-validator-v1"
QB_SEED_RECEIPT_SCHEMA = "qb-seed-receipt-v1"
_ALLOWED_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP", "pausedUP"}
_CHECKING_STATES = {"checkingUP", "checkingDL", "checkingResumeData", "moving"}


@dataclass(frozen=True)
class SeedIdentity:
    torrent_hash: str
    name: str
    total_size: int
    save_path: str
    progress: float
    amount_left: int
    state: str
    used_magnet_fallback: bool = False

    def bounded(self) -> dict[str, Any]:
        return {
            "schema_version": QB_SEED_RECEIPT_SCHEMA,
            "torrent_hash": self.torrent_hash, "name": self.name,
            "total_size": self.total_size, "save_path": self.save_path,
            "progress": self.progress, "amount_left": self.amount_left,
            "state": self.state, "used_magnet_fallback": self.used_magnet_fallback,
        }


class QBittorrentClient(Protocol):
    @property
    def version(self) -> str: ...
    def add_and_verify(self, *, torrent_path: Path, magnet_uri: str, expected_hash: str,
                       expected_name: str, expected_size: int,
                       profile: QBittorrentSeedProfile,
                       alternate_hashes: tuple[str, ...] = ()) -> SeedIdentity: ...
    def inspect(self, *, torrent_hash: str, profile: QBittorrentSeedProfile) -> SeedIdentity: ...


class SSHQBittorrentClient:
    def __init__(self, credentials: QBittorrentCredentials, *, ssh: Path | str = "ssh") -> None:
        self.credentials = credentials
        self.ssh = str(ssh)

    @property
    def version(self) -> str:
        return f"{QB_ADAPTER_VERSION}/requests-{requests.__version__}"

    def probe(self, profile: QBittorrentSeedProfile) -> str:
        """Authenticate and read the qBittorrent version without changing tasks."""
        with self._session(profile) as (session, base_url):
            response = session.get(f"{base_url}/api/v2/app/version", timeout=30)
            if response.status_code != 200:
                raise BmlsubError(
                    "qBittorrent version query failed", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                    retryable=response.status_code >= 500,
                    details={"status": response.status_code},
                )
            version = response.text.strip()
            if not version or len(version) > 128:
                raise BmlsubError(
                    "qBittorrent version response was invalid",
                    code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                )
            return version

    def add_and_verify(self, *, torrent_path: Path, magnet_uri: str, expected_hash: str,
                       expected_name: str, expected_size: int,
                       profile: QBittorrentSeedProfile,
                       alternate_hashes: tuple[str, ...] = ()) -> SeedIdentity:
        used_fallback = False
        query_hashes = (expected_hash, *alternate_hashes)
        torrent_name = torrent_path.name
        torrent_bytes = torrent_path.read_bytes()
        with self._session(profile) as (session, base_url):
            existing = self._query_any(session, base_url, query_hashes)
            if existing is not None:
                mapping = self._mapping_status(
                    existing, query_hashes, expected_name, expected_size,
                    profile.save_path, legacy_save_path=profile.legacy_host_save_path,
                )
                if mapping == "legacy":
                    self._delete_task(session, base_url, existing.torrent_hash)
                    existing = None
                elif (not self._is_complete(existing)
                      and existing.state not in _CHECKING_STATES):
                    self._start_task(session, base_url, existing.torrent_hash)
                    self._recheck_task(session, base_url, existing.torrent_hash)
            if existing is None:
                try:
                    response = session.post(
                        f"{base_url}/api/v2/torrents/add",
                        files={"torrents": (torrent_name, torrent_bytes, "application/x-bittorrent")},
                        data=self._add_fields(profile), timeout=60,
                    )
                    self._expect_add(response)
                except BmlsubError as exc:
                    fallback_status = exc.details.get("status")
                    if (not profile.allow_magnet_fallback
                            or fallback_status not in {400, 404, 405, 415, 422}):
                        raise
                    response = session.post(
                        f"{base_url}/api/v2/torrents/add",
                        data={**self._add_fields(profile), "urls": magnet_uri}, timeout=60,
                    )
                    self._expect_add(response)
                    used_fallback = True
            deadline = time.monotonic() + profile.poll_timeout
            while True:
                identity = self._query_any(session, base_url, query_hashes)
                if identity is not None and identity.state not in _CHECKING_STATES:
                    if self._is_complete(identity):
                        result = SeedIdentity(**{**identity.__dict__, "used_magnet_fallback": used_fallback})
                        self._validate(result, query_hashes, expected_name, expected_size, profile.save_path)
                        return result
                if time.monotonic() >= deadline:
                    raise BmlsubError(
                        "qBittorrent did not finish content checking before timeout",
                        code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=True,
                        details=(identity.bounded() if identity is not None else {}),
                    )
                time.sleep(profile.poll_interval)

    def inspect(self, *, torrent_hash: str, profile: QBittorrentSeedProfile) -> SeedIdentity:
        with self._session(profile) as (session, base_url):
            identity = self._query(session, base_url, torrent_hash)
            if identity is None:
                raise BmlsubError("qBittorrent task is missing", code=ErrorCode.INPUT_MISSING)
            return identity

    @staticmethod
    def _delete_task(session: requests.Session, base_url: str, torrent_hash: str) -> None:
        response = session.post(
            f"{base_url}/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": "false"}, timeout=30,
        )
        if response.status_code != 200:
            raise BmlsubError(
                "qBittorrent could not replace the incomplete task",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=response.status_code >= 500,
                details={"status": response.status_code},
            )

    @staticmethod
    def _start_task(session: requests.Session, base_url: str, torrent_hash: str) -> None:
        response = session.post(
            f"{base_url}/api/v2/torrents/start",
            data={"hashes": torrent_hash}, timeout=30,
        )
        if response.status_code == 404:
            response = session.post(
                f"{base_url}/api/v2/torrents/resume",
                data={"hashes": torrent_hash}, timeout=30,
            )
        if response.status_code != 200:
            raise BmlsubError(
                "qBittorrent rejected the task start request",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=response.status_code >= 500,
                details={"status": response.status_code},
            )

    @staticmethod
    def _recheck_task(session: requests.Session, base_url: str, torrent_hash: str) -> None:
        response = session.post(
            f"{base_url}/api/v2/torrents/recheck",
            data={"hashes": torrent_hash}, timeout=30,
        )
        if response.status_code != 200:
            raise BmlsubError(
                "qBittorrent rejected the content recheck",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=response.status_code >= 500,
                details={"status": response.status_code},
            )

    @staticmethod
    def _is_complete(identity: SeedIdentity) -> bool:
        return (identity.progress >= 1.0 and identity.amount_left == 0
                and identity.state in _ALLOWED_STATES)

    @staticmethod
    def _mapping_status(identity: SeedIdentity, expected_hashes: tuple[str, ...],
                        expected_name: str, expected_size: int,
                        expected_save_path: str,
                        legacy_save_path: str | None = None) -> str:
        hashes = {value.lower() for value in expected_hashes}
        if identity.torrent_hash not in hashes or identity.name != expected_name:
            raise BmlsubError("qBittorrent torrent identity does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        if identity.total_size != expected_size:
            raise BmlsubError("qBittorrent content mapping does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        actual_path = identity.save_path.rstrip("/")
        if actual_path == expected_save_path.rstrip("/"):
            return "expected"
        if legacy_save_path and actual_path == legacy_save_path.rstrip("/"):
            return "legacy"
        raise BmlsubError("qBittorrent content mapping does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)

    @contextmanager
    def _session(self, profile: QBittorrentSeedProfile) -> Iterator[tuple[requests.Session, str]]:
        local_port = _free_port()
        tunnel = subprocess.Popen(
            [self.ssh, "-N", "-o", "ExitOnForwardFailure=yes", "-L",
             f"127.0.0.1:{local_port}:{profile.host}:{profile.port}", profile.ssh_alias],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        session = requests.Session()
        base_url = f"http://127.0.0.1:{local_port}"
        headers = _request_headers(profile)
        if headers:
            session.headers.update(headers)
        try:
            _wait_for_tunnel(tunnel, local_port)
            response = session.post(
                f"{base_url}/api/v2/auth/login",
                data={"username": self.credentials.username, "password": self.credentials.password},
                timeout=30,
            )
            if not _login_succeeded(response, session):
                raise BmlsubError("qBittorrent authentication failed", code=ErrorCode.EXTERNAL_SERVICE_ERROR)
            if response.status_code == 204:
                verification = session.get(f"{base_url}/api/v2/app/version", timeout=30)
                if verification.status_code != 200:
                    raise BmlsubError(
                        "qBittorrent authenticated session verification failed",
                        code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                        retryable=verification.status_code >= 500,
                        details={"status": verification.status_code},
                    )
            yield session, base_url
        finally:
            try:
                session.post(f"{base_url}/api/v2/auth/logout", timeout=5)
            except Exception:
                pass
            session.close()
            if tunnel.poll() is None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=5)
            if tunnel.stderr:
                tunnel.stderr.close()

    @staticmethod
    def _add_fields(profile: QBittorrentSeedProfile) -> dict[str, str]:
        return {
            "savepath": profile.save_path, "category": profile.category,
            "tags": ",".join(profile.tags), "paused": "false",
            "skip_checking": "false", "sequentialDownload": "false",
            "firstLastPiecePrio": "false", "root_folder": "false",
        }

    @staticmethod
    def _expect_add(response: requests.Response) -> None:
        if response.status_code != 200:
            raise BmlsubError(
                "qBittorrent rejected the add request", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=response.status_code >= 500,
                details={"status": response.status_code},
            )
        text = response.text.strip()
        if text not in {"", "Ok."}:
            try:
                data = response.json()
            except ValueError as exc:
                raise BmlsubError("qBittorrent add response was invalid", code=ErrorCode.EXTERNAL_SERVICE_ERROR) from exc
            if not data.get("success_count") and not data.get("added_torrent_ids"):
                raise BmlsubError("qBittorrent did not add the torrent", code=ErrorCode.EXTERNAL_SERVICE_ERROR)

    @classmethod
    def _query_any(cls, session: requests.Session, base_url: str,
                   torrent_hashes: tuple[str, ...]) -> SeedIdentity | None:
        for torrent_hash in torrent_hashes:
            identity = cls._query(session, base_url, torrent_hash)
            if identity is not None:
                return identity
        return None

    @staticmethod
    def _query(session: requests.Session, base_url: str, torrent_hash: str) -> SeedIdentity | None:
        response = session.get(
            f"{base_url}/api/v2/torrents/info", params={"hashes": torrent_hash}, timeout=30,
        )
        if response.status_code != 200:
            raise BmlsubError(
                "qBittorrent status query failed", code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=response.status_code >= 500, details={"status": response.status_code},
            )
        try:
            rows = response.json()
        except ValueError as exc:
            raise BmlsubError("qBittorrent status response was invalid", code=ErrorCode.EXTERNAL_SERVICE_ERROR) from exc
        if not isinstance(rows, list) or not rows:
            return None
        row: Mapping[str, Any] = rows[0]
        return SeedIdentity(
            torrent_hash=str(row.get("hash", "")).lower(), name=str(row.get("name", "")),
            total_size=int(row.get("total_size", -1)), save_path=str(row.get("save_path", "")),
            progress=float(row.get("progress", -1)), amount_left=int(row.get("amount_left", -1)),
            state=str(row.get("state", "")),
        )

    @staticmethod
    def _validate(identity: SeedIdentity, expected_hashes: str | tuple[str, ...], expected_name: str,
                  expected_size: int, expected_save_path: str) -> None:
        hashes = (expected_hashes,) if isinstance(expected_hashes, str) else expected_hashes
        if identity.torrent_hash not in {value.lower() for value in hashes} or identity.name != expected_name:
            raise BmlsubError("qBittorrent torrent identity does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        if identity.total_size != expected_size or identity.save_path.rstrip("/") != expected_save_path.rstrip("/"):
            raise BmlsubError("qBittorrent content mapping does not match", code=ErrorCode.OUTPUT_VALIDATION_FAILED)
        if identity.progress < 1.0 or identity.amount_left != 0 or identity.state not in _ALLOWED_STATES:
            raise BmlsubError(
                "qBittorrent task is not a complete seeding task",
                code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                details={
                    "torrent_hash": identity.torrent_hash,
                    "name": identity.name,
                    "state": identity.state,
                    "progress": identity.progress,
                    "amount_left": identity.amount_left,
                    "total_size": identity.total_size,
                    "save_path": identity.save_path,
                    "expected_save_path": expected_save_path,
                },
            )


def validate_seed_identity(identity: SeedIdentity, *, expected_hash: str,
                           expected_name: str, expected_size: int, save_path: str,
                           alternate_hashes: tuple[str, ...] = ()) -> None:
    SSHQBittorrentClient._validate(
        identity, (expected_hash, *alternate_hashes), expected_name, expected_size, save_path,
    )


def _request_headers(profile: QBittorrentSeedProfile) -> dict[str, str]:
    if profile.webui_origin is None:
        return {}
    host = profile.webui_origin.removeprefix("https://")
    return {
        "Host": host,
        "Origin": profile.webui_origin,
        "Referer": f"{profile.webui_origin}/",
    }


def _login_succeeded(response: requests.Response, session: requests.Session) -> bool:
    if response.status_code == 200 and response.text.strip() == "Ok.":
        return True
    return response.status_code == 204 and not response.content and bool(session.cookies)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_tunnel(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BmlsubError("SSH tunnel failed to start", code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=True)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise BmlsubError("SSH tunnel did not become ready", code=ErrorCode.EXTERNAL_SERVICE_ERROR, retryable=True)
