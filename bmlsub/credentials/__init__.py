"""Unified credential manifests and native system secret storage."""

from .keychain import (
    MacOSKeychainSecretStore, SecretStore, SystemKeyringSecretStore,
    default_secret_store,
)
from .manifest import (
    CREDENTIAL_MANIFEST_SCHEMA, CredentialManifest, CredentialProfile,
    default_credential_manifest_path, import_credential_json, load_credential_manifest,
    load_secure_json, credential_status, resolve_credential_manifest_path,
    upsert_secret_profile, validate_credentials,
)
from .probe import probe_credentials
from .service import (
    AnibtCredentials, CredentialService, QBittorrentCredentials, R2Credentials,
    RemotePullCredentials,
)
from .ssh_config import SSHConfigIdentity, SSHConfigResolver

__all__ = [
    "AnibtCredentials", "CREDENTIAL_MANIFEST_SCHEMA", "CredentialManifest",
    "CredentialProfile", "CredentialService", "MacOSKeychainSecretStore",
    "QBittorrentCredentials", "R2Credentials", "RemotePullCredentials",
    "SSHConfigIdentity", "SSHConfigResolver", "SecretStore", "SystemKeyringSecretStore",
    "credential_status", "default_secret_store",
    "default_credential_manifest_path", "import_credential_json",
    "load_credential_manifest", "load_secure_json", "probe_credentials",
    "resolve_credential_manifest_path", "upsert_secret_profile", "validate_credentials",
]
