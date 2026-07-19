# Credentials (implemented behavior)

[中文](zh/credentials.md) · [Documentation home](../README.md)

For ordinary use, credentials are managed through the Workstation delivery wizard:

```bash
bmlsub workstation start delivery --configure
```

The three user-facing Workstation entry points are `bmlsub workstation start` for interactive fast mode, `bmlsub workstation start delivery` for interactive external delivery, and `bmlsub workstation start delivery -y` for unattended delivery. Unattended mode only uses existing validated profiles; it never prompts for secrets. If a required Keychain item is missing or invalid, it returns `needs_review` and the operator must rerun interactive delivery with `--configure` or use the dedicated credential commands.

Default discovery prefers an existing Application Support manifest, then an existing legacy `.config/bml` manifest; otherwise Application Support is the target. Manifest and secret JSON files must be current-user-owned regular non-symlink files with mode 0600 or stricter.

Kinds are R2, qBittorrent, Anibt, SSH, and remote_pull. Secret kinds store only Keychain account references in the manifest. SSH uses OpenSSH identity settings; remote_pull references an SSH profile and server rclone remote. Labels/descriptions are bounded non-secret metadata.

An SSH credential profile name and an OpenSSH Host alias are separate identifiers. For example, the bmlsub manifest profile may be named `staging-vps-profile` while its `ssh_alias` is `media-vps`, the actual `Host` in `~/.ssh/config`. The delivery wizard labels both concepts explicitly, resolves the selected profile through `CredentialService.resolve_ssh()`, and stores `credential_aliases.ssh: staging-vps-profile` separately from `publish.ssh_alias: media-vps`. Pressing Enter on a defaulted prompt is always described explicitly.

CredentialService locks manifest CRUD with `fcntl.flock` and attempts Keychain/manifest rollback on failure. Delete requires `confirmed=True` and rejects references.

Status is redacted; validate checks Keychain payloads and SSH identity without network access; probe is bounded/read-only but reaches real services. CLI probe requires confirmation, while Python callers provide their own interaction boundary.

A workstation may freeze the redacted availability of the aliases referenced by `series.json` in `workstation/state/credentials-status.json`. The delivery wizard stores both non-secret filesystem namespaces: `publish.remote_root` is the VPS host directory used by SSH/rclone (for example `/data/dcapp/qb/downloads`), while `publish.qb_save_path` is the corresponding qB Docker-container directory (default `/downloads`). They need not have the same string; the deployment volume mapping connects matching filenames. The snapshot is limited to alias, kind, reference, availability, approved public settings, and check time. It contains no Keychain payload and does not imply that a network probe ran. Aliases shown in public documentation are examples; a real snapshot contains only the redacted aliases referenced by that series.

Release compatibility sources remain implemented: R2 and qB can use named environment variables or secure JSON; Anibt can use Python explicit token, environment, or secure JSON. CLI Anibt has env/config/profile options but no plaintext token argument. Compatibility sources are not automatically migrated or removed.
