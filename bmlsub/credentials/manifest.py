"""Credential manifest I/O, migration, and redacted inspection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .keychain import MacOSKeychainSecretStore, SecretStore
from .models import CREDENTIAL_MANIFEST_SCHEMA, CredentialManifest, CredentialProfile
from .ssh_config import SSHConfigResolver


_SECRET_KINDS = {"r2", "qbittorrent", "anibt"}
_APPLICATION_SUPPORT_MANIFEST = Path("Library/Application Support/BMLSub/credentials.json")
_LEGACY_MANIFEST = Path(".config/bml/credentials.json")
_PAYLOAD_FIELDS = {
    "r2": ({"account_id", "access_key_id", "secret_access_key"}, {"endpoint"}),
    "qbittorrent": ({"username", "password"}, set()),
    "anibt": ({"token"}, set()),
}


def default_credential_manifest_path(*, home: Path | str | None = None,
                                     for_write: bool = False) -> Path:
    root = Path(home).expanduser() if home is not None else Path.home()
    preferred = root / _APPLICATION_SUPPORT_MANIFEST
    legacy = root / _LEGACY_MANIFEST
    if not for_write:
        if preferred.exists():
            return preferred
        if legacy.exists():
            return legacy
    return preferred


def resolve_credential_manifest_path(path: Path | str | None = None, *,
                                     home: Path | str | None = None,
                                     for_write: bool = False) -> Path:
    if path is not None:
        return Path(path).expanduser()
    return default_credential_manifest_path(home=home, for_write=for_write)


def load_secure_json(path: Path | str) -> dict[str, Any]:
    source = Path(path).expanduser()
    try:
        stat = source.lstat()
        if source.is_symlink() or not source.is_file():
            raise ValueError("credential JSON must be a regular non-symlink file")
        if stat.st_uid != os.getuid():
            raise ValueError("credential JSON must be owned by the current user")
        if stat.st_mode & 0o077:
            raise ValueError("credential JSON permissions must be 0600 or stricter")
        value = json.loads(source.read_text(encoding="utf-8"))
    except ValueError:
        raise
    except FileNotFoundError as exc:
        raise ValueError("credential JSON does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("credential JSON is unreadable or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("credential JSON must contain a JSON object")
    return value


def load_credential_manifest(path: Path | str) -> CredentialManifest:
    return CredentialManifest.from_mapping(load_secure_json(path))


def validate_secret_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, str]:
    if kind not in _PAYLOAD_FIELDS:
        raise ValueError(f"unsupported secret payload kind: {kind}")
    required, optional = _PAYLOAD_FIELDS[kind]
    unknown = set(payload) - required - optional
    missing = required - set(payload)
    if unknown:
        raise ValueError(f"unknown {kind} secret fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing {kind} secret fields: {sorted(missing)}")
    normalized: dict[str, str] = {}
    for key in sorted(required | optional):
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{kind} field {key} must be a non-empty string")
        normalized[key] = value if key in {"secret_access_key", "password", "token"} else value.strip()
    if kind == "r2" and "endpoint" in normalized and not normalized["endpoint"].startswith("https://"):
        raise ValueError("R2 endpoint must use HTTPS")
    return normalized


def import_credential_json(*, input_path: Path | str, manifest_path: Path | str,
                           secret_store: SecretStore | None = None,
                           replace: bool = False) -> dict[str, Any]:
    source = load_secure_json(input_path)
    allowed = {"schema_version", "backend", "namespace", "profiles"}
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(f"unknown credential import fields: {sorted(unknown)}")
    if source.get("schema_version") != CREDENTIAL_MANIFEST_SCHEMA:
        raise ValueError("unsupported credential import schema_version")
    if source.get("backend") != "macos-keychain":
        raise ValueError("credential import backend must be macos-keychain")
    namespace = source.get("namespace")
    raw_profiles = source.get("profiles")
    if not isinstance(namespace, str) or not isinstance(raw_profiles, Mapping):
        raise ValueError("credential import requires namespace and profiles")

    profiles: dict[str, CredentialProfile] = {}
    secrets: dict[str, dict[str, str]] = {}
    for alias, raw in raw_profiles.items():
        if not isinstance(alias, str) or not isinstance(raw, Mapping):
            raise ValueError("credential import profiles must be named JSON objects")
        data = dict(raw)
        kind = data.pop("kind", None)
        label = data.pop("label", None)
        description = data.pop("description", None)
        if not isinstance(kind, str):
            raise ValueError(f"credential profile {alias} requires kind")
        secret = data.pop("secret", None)
        if kind in _SECRET_KINDS:
            if not isinstance(secret, Mapping):
                raise ValueError(f"credential profile {alias} requires a secret object")
            secrets[alias] = validate_secret_payload(kind, secret)
        elif secret is not None:
            raise ValueError(f"credential profile {alias} must not contain a secret object")
        profiles[alias] = CredentialProfile(
            alias=alias, kind=kind, settings=data,
            label=label, description=description,
        )
    manifest = CredentialManifest(namespace=namespace, profiles=profiles)
    target = Path(manifest_path).expanduser()
    if target.exists() and not replace:
        raise ValueError("credential manifest already exists")
    store = secret_store or MacOSKeychainSecretStore()
    service = manifest.keychain_service()
    if not replace:
        existing = [alias for alias in secrets if store.exists(service, profiles[alias].keychain_account)]
        if existing:
            raise ValueError(f"Keychain profiles already exist: {sorted(existing)}")
    previous = {
        alias: store.get(service, profiles[alias].keychain_account) for alias in secrets
    }
    try:
        for alias, payload in secrets.items():
            store.set(service, profiles[alias].keychain_account,
                      json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        _atomic_write_manifest(target, manifest)
    except Exception:
        for alias, old_value in previous.items():
            account = profiles[alias].keychain_account
            if old_value is None:
                store.delete(service, account)
            else:
                store.set(service, account, old_value)
        raise
    return {
        "status": "succeeded", "manifest": str(target),
        "profiles": [public_profile(manifest, profile, available=True)
                     for profile in manifest.profiles.values()],
    }


def upsert_secret_profile(*, manifest_path: Path | str, alias: str, kind: str,
                          secret: Mapping[str, Any], settings: Mapping[str, Any] | None = None,
                          secret_store: SecretStore | None = None,
                          replace: bool = False) -> dict[str, Any]:
    if kind not in _SECRET_KINDS:
        raise ValueError(f"unsupported secret profile kind: {kind}")
    target = Path(manifest_path).expanduser()
    manifest = load_credential_manifest(target)
    profiles = dict(manifest.profiles)
    if alias in profiles and not replace:
        raise ValueError(f"credential profile already exists: {alias}")
    profile = CredentialProfile(
        alias=alias, kind=kind, settings=dict(settings or {}),
    )
    payload = validate_secret_payload(kind, secret)
    profiles[alias] = profile
    updated = CredentialManifest(namespace=manifest.namespace, profiles=profiles)
    store = secret_store or MacOSKeychainSecretStore()
    service = updated.keychain_service()
    previous = store.get(service, profile.keychain_account)
    if previous is not None and not replace:
        raise ValueError(f"Keychain profile already exists: {alias}")
    try:
        store.set(
            service, profile.keychain_account,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        _atomic_write_manifest(target, updated)
    except Exception:
        if previous is None:
            store.delete(service, profile.keychain_account)
        else:
            store.set(service, profile.keychain_account, previous)
        raise
    return {
        "status": "succeeded",
        "manifest": str(target),
        "profile": public_profile(updated, profile, available=True),
    }


def credential_status(*, manifest_path: Path | str, secret_store: SecretStore | None = None,
                      ssh_resolver: SSHConfigResolver | None = None) -> dict[str, Any]:
    manifest = load_credential_manifest(manifest_path)
    store = secret_store or MacOSKeychainSecretStore()
    resolver = ssh_resolver or SSHConfigResolver()
    profiles = []
    for profile in manifest.profiles.values():
        available = True
        if profile.kind in _SECRET_KINDS:
            available = store.exists(manifest.keychain_service(), profile.keychain_account)
        elif profile.kind == "ssh":
            try:
                resolver.resolve(str(profile.settings["ssh_alias"]))
            except ValueError:
                available = False
        profiles.append(public_profile(manifest, profile, available=available))
    return {"status": "succeeded", "profiles": profiles}


def validate_credentials(*, manifest_path: Path | str, secret_store: SecretStore | None = None,
                         ssh_resolver: SSHConfigResolver | None = None) -> dict[str, Any]:
    manifest = load_credential_manifest(manifest_path)
    store = secret_store or MacOSKeychainSecretStore()
    resolver = ssh_resolver or SSHConfigResolver()
    profiles = []
    for profile in manifest.profiles.values():
        if profile.kind in _SECRET_KINDS:
            raw = store.get(manifest.keychain_service(), profile.keychain_account)
            if raw is None:
                raise ValueError(f"credential profile is unavailable: {profile.alias}")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"credential profile payload is invalid: {profile.alias}") from exc
            if not isinstance(payload, Mapping):
                raise ValueError(f"credential profile payload is invalid: {profile.alias}")
            validate_secret_payload(profile.kind, payload)
        elif profile.kind == "ssh":
            identity = resolver.resolve(str(profile.settings["ssh_alias"]))
            resolver.validate(identity, dict(profile.settings))
        profiles.append(public_profile(manifest, profile, available=True))
    return {"status": "succeeded", "valid": True, "profiles": profiles}


def read_secret_profile(manifest: CredentialManifest, alias: str, kind: str,
                        secret_store: SecretStore | None = None) -> tuple[dict[str, str], str]:
    profile = manifest.profile(alias, kind=kind)
    store = secret_store or MacOSKeychainSecretStore()
    raw = store.get(manifest.keychain_service(), profile.keychain_account)
    if raw is None:
        raise ValueError(f"credential profile is unavailable: {alias}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"credential profile payload is invalid: {alias}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"credential profile payload is invalid: {alias}")
    return validate_secret_payload(kind, value), manifest.reference(alias)


def resolve_ssh_profile(manifest: CredentialManifest, alias: str,
                        ssh_resolver: SSHConfigResolver | None = None) -> tuple[str, str]:
    profile = manifest.profile(alias, kind="ssh")
    resolver = ssh_resolver or SSHConfigResolver()
    identity = resolver.resolve(str(profile.settings["ssh_alias"]))
    resolver.validate(identity, dict(profile.settings))
    return identity.alias, manifest.reference(alias)


def public_profile(manifest: CredentialManifest, profile: CredentialProfile,
                   *, available: bool) -> dict[str, Any]:
    result = {
        "alias": profile.alias, "kind": profile.kind,
        "reference": manifest.reference(profile.alias), "available": available,
    }
    if profile.label is not None:
        result["label"] = profile.label
    if profile.description is not None:
        result["description"] = profile.description
    if profile.kind == "ssh":
        result["ssh_alias"] = profile.settings["ssh_alias"]
    elif profile.kind == "anibt":
        result["api_url"] = profile.settings["api_url"]
    elif profile.kind == "remote_pull":
        result.update({
            "ssh_profile": profile.settings["ssh_profile"],
            "rclone_remote": profile.settings["rclone_remote"],
        })
    return result


def _atomic_write_manifest(path: Path, manifest: CredentialManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
