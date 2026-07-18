# Release chain and Profiles

[中文](zh/release.md) · [Documentation home](../README.md)

All external release CLI commands require confirmation; create-torrent is local.

TorrentProfile accepts hybrid/v1, supported fixed piece lengths, private/comment/created_by, tracker URL, and timeout. libtorrent is the only backend.

R2UploadProfile requires bucket and object_key and controls content type, private/public access, public HTTPS base, multipart sizes, and concurrency. R2 credentials use profile alias or compatibility env/secure file.

RemotePullProfile requires ssh_alias, rclone_remote, bucket, object_key, normalized absolute target_path, and bounded timeout. A connection profile may resolve/validate the SSH alias.

QBittorrentSeedProfile requires ssh_alias, allows remote loopback host/port, absolute save_path, clean HTTPS origin, category/tags, polling, and v1 magnet fallback. The Stage consumes torrent, content, and remote-content receipt Artifacts.

AnibtPublishProfile supports id types bgm/anilist/mal/anidb, required anime ID/title, controlled resolution/language/subtitle/container enums, preview and Nyaa fields, and requires torrent-file mode. Preview and formal profiles fingerprint differently.

Receipts contain bounded validation/reuse identity, not secrets or complete responses.

`workstation/state/release-batch.json` freezes the current local release scope; it does not require all three workstation products in every batch. It records included and deferred product keys, product/intermediate Artifacts, track and attachment validation, the credential-status snapshot path, and the next local state. Deferred hardsub requests may remain pending rather than being marked failed. A batch may contain only the verified HEVC CHS/CHT internal-subtitle MKV and defer both MP4 files; `ready_for_torrent` does not imply that a torrent or any external resource was created.

No delete/withdraw Stage is implemented for external resources.
