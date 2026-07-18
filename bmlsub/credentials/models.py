"""Strict models for non-secret credential manifests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping


CREDENTIAL_MANIFEST_SCHEMA = "bmlsub-credentials-v1"
_SUPPORTED_KINDS = {"r2", "qbittorrent", "anibt", "ssh", "remote_pull"}
_ALIAS = re.compile(r"^[A-Za-z0-9._@-]+$")
_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class CredentialProfile:
    alias: str
    kind: str
    settings: Mapping[str, Any]
    label: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not _ALIAS.fullmatch(self.alias):
            raise ValueError("credential profile alias contains unsupported characters")
        if self.kind not in _SUPPORTED_KINDS:
            raise ValueError(f"unsupported credential profile kind: {self.kind}")
        for field_name in ("label", "description"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"credential profile {field_name} must be a non-empty string")
            if any(ord(character) < 32 for character in value):
                raise ValueError(f"credential profile {field_name} contains control characters")
            limit = 128 if field_name == "label" else 512
            if len(value) > limit:
                raise ValueError(f"credential profile {field_name} is too long")
            object.__setattr__(self, field_name, value.strip())
        settings = dict(self.settings)
        allowed = {
            "r2": {"keychain_account"},
            "qbittorrent": {"keychain_account"},
            "anibt": {"keychain_account", "api_url"},
            "ssh": {"ssh_alias", "expected_host", "expected_user", "expected_port"},
            "remote_pull": {"ssh_profile", "rclone_remote"},
        }[self.kind]
        unknown = set(settings) - allowed
        if unknown:
            raise ValueError(f"unknown {self.kind} profile fields: {sorted(unknown)}")
        if self.kind in {"r2", "qbittorrent", "anibt"}:
            account = str(settings.get("keychain_account", self.alias)).strip()
            if not _ALIAS.fullmatch(account):
                raise ValueError("keychain_account contains unsupported characters")
            settings["keychain_account"] = account
            if self.kind == "anibt":
                api_url = str(settings.get("api_url", "https://anibt.net/api/releases/publish")).strip()
                if not api_url.startswith("https://"):
                    raise ValueError("Anibt API URL must use HTTPS")
                settings["api_url"] = api_url
        elif self.kind == "ssh":
            ssh_alias = str(settings.get("ssh_alias", "")).strip()
            if not _ALIAS.fullmatch(ssh_alias):
                raise ValueError("ssh profile requires a safe ssh_alias")
            settings["ssh_alias"] = ssh_alias
            for key in ("expected_host", "expected_user"):
                if key in settings and (not isinstance(settings[key], str) or not settings[key].strip()):
                    raise ValueError(f"{key} must be a non-empty string")
            if "expected_port" in settings:
                port = settings["expected_port"]
                if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                    raise ValueError("expected_port must be an integer between 1 and 65535")
        else:
            ssh_profile = str(settings.get("ssh_profile", "")).strip()
            remote = str(settings.get("rclone_remote", "")).strip()
            if not _ALIAS.fullmatch(ssh_profile):
                raise ValueError("remote_pull profile requires a safe ssh_profile")
            if not _NAME.fullmatch(remote):
                raise ValueError("remote_pull profile requires a safe rclone_remote")
            settings.update(ssh_profile=ssh_profile, rclone_remote=remote)
        object.__setattr__(self, "settings", MappingProxyType(settings))

    @property
    def keychain_account(self) -> str:
        if self.kind not in {"r2", "qbittorrent", "anibt"}:
            raise ValueError("profile does not use Keychain")
        return str(self.settings["keychain_account"])

    def to_dict(self) -> dict[str, Any]:
        result = {"kind": self.kind, **dict(self.settings)}
        if self.label is not None:
            result["label"] = self.label
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True)
class CredentialManifest:
    namespace: str
    profiles: Mapping[str, CredentialProfile]
    schema_version: str = CREDENTIAL_MANIFEST_SCHEMA
    backend: str = "macos-keychain"

    def __post_init__(self) -> None:
        if self.schema_version != CREDENTIAL_MANIFEST_SCHEMA:
            raise ValueError("unsupported credential manifest schema_version")
        if self.backend != "macos-keychain":
            raise ValueError("credential manifest backend must be macos-keychain")
        if not _ALIAS.fullmatch(self.namespace):
            raise ValueError("credential namespace contains unsupported characters")
        profiles = dict(self.profiles)
        for alias, profile in profiles.items():
            if alias != profile.alias:
                raise ValueError("credential profile key and alias do not match")
        for profile in profiles.values():
            if profile.kind == "remote_pull":
                target = profiles.get(str(profile.settings["ssh_profile"]))
                if target is None or target.kind != "ssh":
                    raise ValueError("remote_pull ssh_profile must reference an ssh profile")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))

    def profile(self, alias: str, *, kind: str | None = None) -> CredentialProfile:
        try:
            profile = self.profiles[alias]
        except KeyError as exc:
            raise ValueError(f"credential profile not found: {alias}") from exc
        if kind is not None and profile.kind != kind:
            raise ValueError(f"credential profile {alias} is not {kind}")
        return profile

    def keychain_service(self) -> str:
        return f"org.billionmetalab.bmlsub.{self.namespace}"

    def reference(self, alias: str) -> str:
        profile = self.profile(alias)
        if profile.kind in {"r2", "qbittorrent", "anibt"}:
            return f"keychain:{self.namespace}/{profile.keychain_account}"
        if profile.kind == "ssh":
            return f"ssh-config:{profile.settings['ssh_alias']}"
        return f"remote-pull:{self.namespace}/{alias}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "namespace": self.namespace,
            "profiles": {
                alias: profile.to_dict() for alias, profile in sorted(self.profiles.items())
            },
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CredentialManifest":
        allowed = {"schema_version", "backend", "namespace", "profiles"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown credential manifest fields: {sorted(unknown)}")
        raw_profiles = value.get("profiles")
        if not isinstance(raw_profiles, Mapping):
            raise ValueError("credential manifest profiles must be a JSON object")
        profiles: dict[str, CredentialProfile] = {}
        for alias, raw in raw_profiles.items():
            if not isinstance(alias, str) or not isinstance(raw, Mapping):
                raise ValueError("credential profiles must be named JSON objects")
            data = dict(raw)
            kind = data.pop("kind", None)
            label = data.pop("label", None)
            description = data.pop("description", None)
            if not isinstance(kind, str):
                raise ValueError(f"credential profile {alias} requires kind")
            profiles[alias] = CredentialProfile(
                alias=alias, kind=kind, settings=data,
                label=label, description=description,
            )
        return cls(
            schema_version=str(value.get("schema_version", "")),
            backend=str(value.get("backend", "")),
            namespace=str(value.get("namespace", "")),
            profiles=profiles,
        )
