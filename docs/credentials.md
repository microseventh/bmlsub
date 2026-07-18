# Credentials (implemented behavior)

[中文](zh/credentials.md) · [Documentation home](../README.md)

Default discovery prefers an existing Application Support manifest, then an existing legacy `.config/bml` manifest; otherwise Application Support is the target. Manifest and secret JSON files must be current-user-owned regular non-symlink files with mode 0600 or stricter.

Kinds are R2, qBittorrent, Anibt, SSH, and remote_pull. Secret kinds store only Keychain account references in the manifest. SSH uses OpenSSH identity settings; remote_pull references an SSH profile and server rclone remote. Labels/descriptions are bounded non-secret metadata.

CredentialService locks manifest CRUD with `fcntl.flock` and attempts Keychain/manifest rollback on failure. Delete requires `confirmed=True` and rejects references.

Status is redacted; validate checks Keychain payloads and SSH identity without network access; probe is bounded/read-only but reaches real services. CLI probe requires confirmation, while Python callers provide their own interaction boundary.

A workstation may freeze the redacted availability of the aliases referenced by `series.json` in `workstation/state/credentials-status.json`. The snapshot is limited to alias, kind, reference, availability, approved public settings, and check time. It contains no Keychain payload and does not imply that a network probe ran. Aliases shown in public documentation are examples; a real snapshot contains only the redacted aliases referenced by that series.

Release compatibility sources remain implemented: R2 and qB can use named environment variables or secure JSON; Anibt can use Python explicit token, environment, or secure JSON. CLI Anibt has env/config/profile options but no plaintext token argument. Compatibility sources are not automatically migrated or removed.
