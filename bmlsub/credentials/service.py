"""High-level credential profile management and resolution."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .keychain import MacOSKeychainSecretStore, SecretStore
from .manifest import (
    _atomic_write_manifest,
    load_credential_manifest,
    public_profile,
    read_secret_profile,
    resolve_credential_manifest_path,
    resolve_ssh_profile,
    validate_secret_payload,
)
from .models import CredentialManifest, CredentialProfile
from .ssh_config import SSHConfigResolver


_SECRET_KINDS = {"r2", "qbittorrent", "anibt"}


@dataclass(frozen=True)
class R2Credentials:
    account_id: str
    access_key_id: str
    secret_access_key: str
    endpoint_url: str
    reference: str


@dataclass(frozen=True)
class QBittorrentCredentials:
    username: str
    password: str
    reference: str


@dataclass(frozen=True)
class AnibtCredentials:
    token: str
    api_url: str
    reference: str


@dataclass(frozen=True)
class RemotePullCredentials:
    ssh_alias: str
    rclone_remote: str
    reference: str
    ssh_reference: str


ReferenceChecker = Callable[[str], list[str]]


class CredentialService:
    def __init__(self, *, manifest_path: Path | str | None = None,
                 secret_store: SecretStore | None = None,
                 ssh_resolver: SSHConfigResolver | None = None,
                 reference_checker: ReferenceChecker | None = None,
                 home: Path | str | None = None) -> None:
        self.manifest_path = resolve_credential_manifest_path(
            manifest_path, home=home,
        )
        self.secret_store = secret_store
        self.ssh_resolver = ssh_resolver
        self.reference_checker = reference_checker

    def _store(self) -> SecretStore:
        return self.secret_store or MacOSKeychainSecretStore()

    def _ssh(self) -> SSHConfigResolver:
        return self.ssh_resolver or SSHConfigResolver()

    def _manifest(self) -> CredentialManifest:
        return load_credential_manifest(self.manifest_path)

    def initialize_manifest(self, *, namespace: str = "main") -> dict[str, Any]:
        """Create an empty non-secret manifest without replacing an existing one."""
        if self.manifest_path.exists():
            manifest = self._manifest()
            return {
                "status": "skipped", "reused": True,
                "manifest": str(self.manifest_path),
                "namespace": manifest.namespace,
                "keychain_service": manifest.keychain_service(),
            }
        manifest = CredentialManifest(namespace=namespace, profiles={})
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.manifest_path.with_name(f".{self.manifest_path.name}.lock")
        with lock_path.open("a+") as handle:
            lock_path.chmod(0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                if self.manifest_path.exists():
                    current = self._manifest()
                    return {
                        "status": "skipped", "reused": True,
                        "manifest": str(self.manifest_path),
                        "namespace": current.namespace,
                        "keychain_service": current.keychain_service(),
                    }
                _atomic_write_manifest(self.manifest_path, manifest)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {
            "status": "succeeded", "reused": False,
            "manifest": str(self.manifest_path), "namespace": namespace,
            "keychain_service": manifest.keychain_service(),
        }

    def list_profiles(self) -> dict[str, Any]:
        manifest = self._manifest()
        return {
            "status": "succeeded",
            "manifest": str(self.manifest_path),
            "profiles": [self._profile_status(manifest, item) for item in manifest.profiles.values()],
        }

    def get_profile(self, alias: str) -> dict[str, Any]:
        manifest = self._manifest()
        return {
            "status": "succeeded", "manifest": str(self.manifest_path),
            "profile": self._profile_status(manifest, manifest.profile(alias)),
        }

    def status(self) -> dict[str, Any]:
        return self.list_profiles()

    def status_profile(self, alias: str) -> dict[str, Any]:
        return self.get_profile(alias)

    def validate(self) -> dict[str, Any]:
        manifest = self._manifest()
        profiles = [self._validate_profile(manifest, item) for item in manifest.profiles.values()]
        return {
            "status": "succeeded", "valid": True,
            "manifest": str(self.manifest_path), "profiles": profiles,
        }

    def validate_profile(self, alias: str) -> dict[str, Any]:
        manifest = self._manifest()
        return {
            "status": "succeeded", "valid": True,
            "manifest": str(self.manifest_path),
            "profile": self._validate_profile(manifest, manifest.profile(alias)),
        }

    def create_profile(self, *, alias: str, kind: str,
                       settings: Mapping[str, Any] | None = None,
                       secret: Mapping[str, Any] | None = None,
                       label: str | None = None,
                       description: str | None = None) -> dict[str, Any]:
        return self._write_profile(
            current_alias=None, alias=alias, kind=kind, settings=settings,
            secret=secret, label=label, description=description,
        )

    def update_profile(self, alias: str, *, new_alias: str | None = None,
                       kind: str | None = None,
                       settings: Mapping[str, Any] | None = None,
                       secret: Mapping[str, Any] | None = None,
                       label: str | None = None,
                       description: str | None = None) -> dict[str, Any]:
        return self._write_profile(
            current_alias=alias, alias=new_alias or alias, kind=kind,
            settings=settings, secret=secret, label=label,
            description=description,
        )

    def delete_profile(self, alias: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("credential profile deletion requires explicit confirmation")
        with self._locked_manifest() as manifest:
            profile = manifest.profile(alias)
            references = self._references(manifest, alias)
            if references:
                raise ValueError(
                    f"credential profile is still referenced: {', '.join(references)}"
                )
            profiles = dict(manifest.profiles)
            profiles.pop(alias)
            updated = CredentialManifest(namespace=manifest.namespace, profiles=profiles)
            old_secret = None
            store = self._store() if profile.kind in _SECRET_KINDS else None
            if store is not None:
                old_secret = store.get(manifest.keychain_service(), profile.keychain_account)
            _atomic_write_manifest(self.manifest_path, updated)
            if store is not None and old_secret is not None:
                try:
                    store.delete(manifest.keychain_service(), profile.keychain_account)
                except Exception:
                    _atomic_write_manifest(self.manifest_path, manifest)
                    raise
        return {"status": "succeeded", "manifest": str(self.manifest_path), "deleted": alias}

    def resolve_r2(self, alias: str) -> R2Credentials:
        manifest = self._manifest()
        payload, reference = read_secret_profile(manifest, alias, "r2", self.secret_store)
        account_id = payload["account_id"]
        endpoint = payload.get("endpoint", f"https://{account_id}.r2.cloudflarestorage.com")
        return R2Credentials(
            account_id, payload["access_key_id"], payload["secret_access_key"],
            endpoint, reference,
        )

    def resolve_qbittorrent(self, alias: str) -> QBittorrentCredentials:
        manifest = self._manifest()
        payload, reference = read_secret_profile(
            manifest, alias, "qbittorrent", self.secret_store,
        )
        return QBittorrentCredentials(payload["username"], payload["password"], reference)

    def resolve_anibt(self, alias: str, *, api_url: str | None = None) -> AnibtCredentials:
        manifest = self._manifest()
        payload, reference = read_secret_profile(manifest, alias, "anibt", self.secret_store)
        profile = manifest.profile(alias, kind="anibt")
        resolved_url = api_url or str(profile.settings["api_url"])
        if not resolved_url.startswith("https://"):
            raise ValueError("anibt API URL must use HTTPS")
        return AnibtCredentials(payload["token"], resolved_url, reference)

    def resolve_ssh(self, alias: str) -> tuple[str, str]:
        return resolve_ssh_profile(self._manifest(), alias, self._ssh())

    def resolve_remote_pull(self, alias: str) -> RemotePullCredentials:
        manifest = self._manifest()
        profile = manifest.profile(alias, kind="remote_pull")
        ssh_alias, ssh_reference = resolve_ssh_profile(
            manifest, str(profile.settings["ssh_profile"]), self._ssh(),
        )
        return RemotePullCredentials(
            ssh_alias=ssh_alias,
            rclone_remote=str(profile.settings["rclone_remote"]),
            reference=manifest.reference(alias),
            ssh_reference=ssh_reference,
        )

    def probe_profile(self, alias: str, *, probe: Mapping[str, Any] | None = None,
                      connection_profile: str | None = None,
                      ssh: Path | str = "ssh") -> dict[str, Any]:
        from .probe import probe_credentials
        return probe_credentials(
            manifest_path=self.manifest_path, profile_alias=alias, probe=probe,
            connection_profile=connection_profile, secret_store=self.secret_store,
            ssh_resolver=self.ssh_resolver, ssh=ssh,
        )

    def _write_profile(self, *, current_alias: str | None, alias: str,
                       kind: str | None, settings: Mapping[str, Any] | None,
                       secret: Mapping[str, Any] | None,
                       label: str | None, description: str | None) -> dict[str, Any]:
        with self._locked_manifest() as manifest:
            profiles = dict(manifest.profiles)
            existing = profiles.get(current_alias) if current_alias is not None else None
            if current_alias is None and alias in profiles:
                raise ValueError(f"credential profile already exists: {alias}")
            if current_alias is not None and existing is None:
                raise ValueError(f"credential profile not found: {current_alias}")
            if alias != current_alias and alias in profiles:
                raise ValueError(f"credential profile already exists: {alias}")
            resolved_kind = kind or (existing.kind if existing is not None else None)
            if resolved_kind is None:
                raise ValueError("credential profile kind is required")
            resolved_settings = dict(existing.settings) if existing is not None else {}
            if settings is not None:
                resolved_settings = dict(settings)
            profile = CredentialProfile(
                alias=alias, kind=resolved_kind, settings=resolved_settings,
                label=label if label is not None else (existing.label if existing else None),
                description=(description if description is not None
                             else (existing.description if existing else None)),
            )
            if existing is not None:
                profiles.pop(current_alias)
            profiles[alias] = profile
            updated = CredentialManifest(namespace=manifest.namespace, profiles=profiles)
            self._commit_profile_change(manifest, updated, existing, profile, secret)
        return {
            "status": "succeeded", "manifest": str(self.manifest_path),
            "profile": self._profile_status(updated, profile),
        }

    def _commit_profile_change(self, old_manifest: CredentialManifest,
                               new_manifest: CredentialManifest,
                               old_profile: CredentialProfile | None,
                               new_profile: CredentialProfile,
                               secret: Mapping[str, Any] | None) -> None:
        old_is_secret = old_profile is not None and old_profile.kind in _SECRET_KINDS
        new_is_secret = new_profile.kind in _SECRET_KINDS
        if new_is_secret and secret is None and not old_is_secret:
            raise ValueError(f"credential profile {new_profile.alias} requires a secret object")
        if not new_is_secret and secret is not None:
            raise ValueError(f"credential profile {new_profile.alias} must not contain a secret object")
        store = self._store() if old_is_secret or new_is_secret else None
        service = new_manifest.keychain_service()
        old_account = old_profile.keychain_account if old_is_secret else None
        new_account = new_profile.keychain_account if new_is_secret else None
        old_value = store.get(service, old_account) if store is not None and old_account else None
        new_previous = (
            store.get(service, new_account)
            if store is not None and new_account and new_account != old_account else old_value
        )
        new_value = None
        if new_is_secret:
            if secret is not None:
                payload = validate_secret_payload(new_profile.kind, secret)
                new_value = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                )
            else:
                new_value = old_value
            if new_value is None:
                raise ValueError(f"credential profile is unavailable: {new_profile.alias}")
        manifest_written = False
        try:
            if store is not None and new_account is not None and new_value is not None:
                store.set(service, new_account, new_value)
            _atomic_write_manifest(self.manifest_path, new_manifest)
            manifest_written = True
            if store is not None and old_account is not None and old_account != new_account:
                store.delete(service, old_account)
        except Exception:
            if store is not None and new_account is not None:
                if new_previous is None:
                    try:
                        store.delete(service, new_account)
                    except Exception:
                        pass
                else:
                    store.set(service, new_account, new_previous)
            if store is not None and old_account is not None and old_value is not None:
                store.set(service, old_account, old_value)
            if manifest_written:
                try:
                    _atomic_write_manifest(self.manifest_path, old_manifest)
                except Exception:
                    pass
            raise

    def _profile_status(self, manifest: CredentialManifest,
                        profile: CredentialProfile) -> dict[str, Any]:
        available = True
        if profile.kind in _SECRET_KINDS:
            available = self._store().exists(manifest.keychain_service(), profile.keychain_account)
        elif profile.kind == "ssh":
            try:
                self._ssh().resolve(str(profile.settings["ssh_alias"]))
            except ValueError:
                available = False
        return public_profile(manifest, profile, available=available)

    def _validate_profile(self, manifest: CredentialManifest,
                          profile: CredentialProfile) -> dict[str, Any]:
        if profile.kind in _SECRET_KINDS:
            read_secret_profile(manifest, profile.alias, profile.kind, self.secret_store)
        elif profile.kind == "ssh":
            resolve_ssh_profile(manifest, profile.alias, self._ssh())
        elif profile.kind == "remote_pull":
            self.resolve_remote_pull(profile.alias)
        return public_profile(manifest, profile, available=True)

    def _references(self, manifest: CredentialManifest, alias: str) -> list[str]:
        references = [
            f"profile:{item.alias}"
            for item in manifest.profiles.values()
            if item.kind == "remote_pull" and item.settings.get("ssh_profile") == alias
        ]
        if self.reference_checker is not None:
            references.extend(self.reference_checker(alias))
        return sorted(set(references))

    @contextmanager
    def _locked_manifest(self) -> Iterator[CredentialManifest]:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.manifest_path.with_name(f".{self.manifest_path.name}.lock")
        with lock_path.open("a+") as handle:
            lock_path.chmod(0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield self._manifest()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
