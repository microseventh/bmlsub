# Release chain and Profiles

[中文](zh/release.md) · [Documentation home](../README.md)

## Workstation delivery modes

The normal user flow begins with interactive Workstation fast mode:

```bash
bmlsub workstation start
```

Once local products and Torrents are ready, use interactive delivery:

```bash
bmlsub workstation start delivery
```

For unattended delivery with previously validated credentials and configuration:

```bash
bmlsub workstation start delivery -y
```

Both delivery modes evaluate actions in R2 → VPS pull → qB seed → Anibt order. Interactive mode asks whether the Anibt account has Nyaa syndication access; answering yes enables Nyaa proxy publication for all three products with category `1_4`, while answering no keeps the existing Anibt-only behavior. `-y/--yes` defaults this choice to enabled and accepts all external-delivery confirmations automatically. Nyaa is sent through the same multipart Torrent request; the service uses `notes` as the Nyaa description when no explicit Nyaa description is supplied. Both modes retain Stage fingerprint, Artifact, receipt, and live validator checks, so valid prior results are reused. Fast unattended delivery is not a force mode: `--force` remains an explicit request to rerun Stage execution. `--resume` and `--restart` describe recovery intent, but neither deletes external files or withdraws releases.

Before execution, Workstation validates the Credential Manifest, macOS Keychain payloads for R2/qB/Anibt, SSH identity, public paths, and local inputs. Missing or invalid credentials stop unattended mode with `needs_review`; secrets are never requested or emitted noninteractively.

TorrentProfile accepts hybrid/v1, supported fixed piece lengths, private/comment/created_by, tracker URL, and timeout. libtorrent is the only backend.

R2UploadProfile requires bucket and object_key and controls content type, private/public access, public HTTPS base, multipart sizes, and concurrency. R2 credentials use profile alias or compatibility env/secure file.

RemotePullProfile requires ssh_alias, rclone_remote, bucket, object_key, normalized absolute target_path, and bounded timeout. A connection profile may resolve/validate the SSH alias.

QBittorrentSeedProfile requires ssh_alias, allows remote loopback host/port, an absolute container `save_path` (default `/downloads`), clean HTTPS origin, category/tags, polling, and v1 magnet fallback. Workstation publication keeps the VPS host `publish.remote_root` separate from the Docker-container `publish.qb_save_path`; for example, `/data/dcapp/qb/downloads` may be volume-mapped to `/downloads`. Add requests explicitly disable paused, skip-checking, sequential-download, first/last-piece-priority, and root-folder modes, then use qB v5 start with a legacy resume fallback and request a recheck. A matching incomplete task at the known legacy host path is replaced with `deleteFiles=false`; any other unknown path remains blocked. The Stage consumes torrent, content, remote-content, and remote-torrent receipt Artifacts.

AnibtPublishProfile supports id types bgm/anilist/mal/anidb, required anime ID/title, controlled resolution/language/subtitle/container enums, preview and Nyaa fields, and requires torrent-file mode. Preview and formal profiles fingerprint differently.

Receipts contain bounded validation/reuse identity, not secrets or complete responses.

`workstation/state/release-batch.json` freezes the current local release scope; it does not require all three workstation products in every batch. It records included and deferred product keys, product/intermediate Artifacts, track and attachment validation, the credential-status snapshot path, and the next local state. Deferred hardsub requests may remain pending rather than being marked failed. A batch may contain only the verified HEVC CHS/CHT internal-subtitle MKV and defer both MP4 files; `ready_for_torrent` does not imply that a torrent or any external resource was created.

No delete/withdraw Stage is implemented for external resources.
