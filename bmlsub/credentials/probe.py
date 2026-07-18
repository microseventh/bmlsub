"""Bounded read-only probes for Keychain-backed external credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .keychain import SecretStore
from .manifest import load_credential_manifest
from .ssh_config import SSHConfigResolver


def probe_credentials(*, manifest_path: Path | str, profile_alias: str,
                      probe: Mapping[str, Any] | None = None,
                      connection_profile: str | None = None,
                      secret_store: SecretStore | None = None,
                      ssh_resolver: SSHConfigResolver | None = None,
                      ssh: Path | str = "ssh") -> dict[str, Any]:
    manifest = load_credential_manifest(manifest_path)
    profile = manifest.profile(profile_alias)
    parameters = dict(probe or {})
    if profile.kind == "r2":
        return _probe_r2(
            manifest_path=manifest_path, profile_alias=profile_alias,
            parameters=parameters, secret_store=secret_store,
        )
    if profile.kind == "qbittorrent":
        if connection_profile is None:
            raise ValueError("qBittorrent probe requires connection_profile")
        return _probe_qbittorrent(
            manifest_path=manifest_path, profile_alias=profile_alias,
            connection_profile=connection_profile, parameters=parameters,
            secret_store=secret_store, ssh_resolver=ssh_resolver, ssh=ssh,
        )
    if profile.kind == "anibt":
        return _probe_anibt(
            manifest_path=manifest_path, profile_alias=profile_alias,
            parameters=parameters, secret_store=secret_store,
        )
    if profile.kind == "ssh":
        if parameters:
            raise ValueError("SSH probe does not accept probe fields")
        return _probe_ssh(
            manifest_path=manifest_path, profile_alias=profile_alias,
            ssh_resolver=ssh_resolver, ssh=ssh,
        )
    raise ValueError("remote_pull profiles are references; probe their SSH profile instead")


def _probe_r2(*, manifest_path: Path | str, profile_alias: str,
              parameters: dict[str, Any], secret_store: SecretStore | None) -> dict[str, Any]:
    allowed = {"bucket", "object_key"}
    unknown = set(parameters) - allowed
    if unknown:
        raise ValueError(f"unknown R2 probe fields: {sorted(unknown)}")
    bucket = parameters.get("bucket")
    object_key = parameters.get("object_key")
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("R2 probe requires bucket")
    if not isinstance(object_key, str) or not object_key.strip():
        raise ValueError("R2 probe requires object_key")

    from ..release.external_profiles import R2UploadProfile
    from ..release.r2 import Boto3R2Client
    from .service import CredentialService

    credentials = CredentialService(
        manifest_path=manifest_path, secret_store=secret_store,
    ).resolve_r2(profile_alias)
    client = Boto3R2Client(credentials)
    target = R2UploadProfile(bucket=bucket, object_key=object_key)
    response = client.head(target)
    metadata = {str(key).lower(): str(value) for key, value in dict(response.get("Metadata") or {}).items()}
    return {
        "status": "succeeded", "read_only": True, "kind": "r2",
        "profile": profile_alias, "reference": credentials.reference,
        "result": {
            "bucket": target.bucket, "object_key": target.object_key,
            "size": int(response.get("ContentLength", -1)),
            "content_type": str(response.get("ContentType") or ""),
            "sha256": metadata.get("bml-sha256") or None,
            "etag": _bounded_header(response.get("ETag")),
            "version_id": _bounded_header(response.get("VersionId")),
        },
    }


def _probe_qbittorrent(*, manifest_path: Path | str, profile_alias: str,
                       connection_profile: str, parameters: dict[str, Any],
                       secret_store: SecretStore | None,
                       ssh_resolver: SSHConfigResolver | None,
                       ssh: Path | str) -> dict[str, Any]:
    allowed = {"host", "port", "webui_origin"}
    unknown = set(parameters) - allowed
    if unknown:
        raise ValueError(f"unknown qBittorrent probe fields: {sorted(unknown)}")
    from ..release.external_profiles import QBittorrentSeedProfile
    from ..release.qbittorrent import SSHQBittorrentClient
    from .service import CredentialService

    service = CredentialService(
        manifest_path=manifest_path, secret_store=secret_store,
        ssh_resolver=ssh_resolver,
    )
    ssh_alias, ssh_reference = service.resolve_ssh(connection_profile)
    credentials = service.resolve_qbittorrent(profile_alias)
    qb_profile = QBittorrentSeedProfile(
        ssh_alias=ssh_alias,
        host=str(parameters.get("host", "127.0.0.1")),
        port=parameters.get("port", 8080),
        webui_origin=parameters.get("webui_origin"),
    )
    version = SSHQBittorrentClient(credentials, ssh=ssh).probe(qb_profile)
    return {
        "status": "succeeded", "read_only": True, "kind": "qbittorrent",
        "profile": profile_alias, "reference": credentials.reference,
        "connection_reference": ssh_reference,
        "result": {"version": version, "ssh_alias": ssh_alias},
    }


def _probe_anibt(*, manifest_path: Path | str, profile_alias: str,
                 parameters: dict[str, Any], secret_store: SecretStore | None) -> dict[str, Any]:
    if parameters:
        raise ValueError("Anibt probe does not accept probe fields")
    import requests

    from .service import CredentialService

    credentials = CredentialService(
        manifest_path=manifest_path, secret_store=secret_store,
    ).resolve_anibt(profile_alias)
    base_url = credentials.api_url.split("/api/", 1)[0]
    try:
        response = requests.get(
            f"{base_url}/api/subtitle-groups/me",
            headers={"Authorization": f"Bearer {credentials.token}", "Accept": "application/json"},
            timeout=(10.0, 30.0),
        )
    except requests.RequestException as exc:
        raise ValueError("Anibt read-only connection probe failed") from exc
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"Anibt read-only connection probe returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Anibt read-only connection probe returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if payload.get("ok") is not True or not isinstance(data, Mapping):
        raise ValueError("Anibt read-only connection probe returned an invalid response")
    stats = data.get("stats") if isinstance(data.get("stats"), Mapping) else {}
    return {
        "status": "succeeded", "read_only": True, "kind": "anibt",
        "profile": profile_alias, "reference": credentials.reference,
        "result": {
            "name": _bounded_header(data.get("name")),
            "slug": _bounded_header(data.get("slug")),
            "status": _bounded_header(data.get("status")),
            "total_releases": _bounded_int(stats.get("totalReleases")),
            "total_animes": _bounded_int(stats.get("totalAnimes")),
        },
    }


def _bounded_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _probe_ssh(*, manifest_path: Path | str, profile_alias: str,
               ssh_resolver: SSHConfigResolver | None, ssh: Path | str) -> dict[str, Any]:
    import subprocess

    from .service import CredentialService
    alias, reference = CredentialService(
        manifest_path=manifest_path, ssh_resolver=ssh_resolver,
    ).resolve_ssh(profile_alias)
    result = subprocess.run(
        [str(ssh), "-o", "BatchMode=yes", alias, "true"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if result.returncode != 0:
        raise ValueError("SSH read-only connection probe failed")
    return {
        "status": "succeeded", "read_only": True, "kind": "ssh",
        "profile": profile_alias, "reference": reference,
        "result": {"ssh_alias": alias, "connected": True},
    }


def _bounded_header(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"')
    return text[:256] if text else None
