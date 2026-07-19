# Changelog

All notable changes to `bmlsub` are recorded here.

## 1.1.1 — 2026-07-20

### Workstation experience

- Add the guided `workstation start` fast mode, scoped local production, safe rebuild planning, and explicit handoff to external delivery.
- Add consistent Chinese and English interactive interfaces selected at TTY startup, with explicit Enter-to-default guidance.
- Add transcript text export and expanded workstation inspection, planning, and state snapshots.

### Credentials and delivery

- Distinguish bmlsub credential profile names from OpenSSH Host aliases and resolve the selected SSH profile before saving publication settings.
- Make `workstation start delivery --configure` force the TTY credential wizard even for a complete plan; the wizard distinguishes profile reuse, repair, and creation and collects the full R2 credential triplet for Keychain storage.
- Add a concise credential/path summary, product-level confirmations in R2 → VPS → qB → Anibt order, `--verbose-plan`, and `-y/--yes` automatic execution with normal fingerprint reuse.
- Separate the VPS host `publish.remote_root` from the qBittorrent Docker-container `publish.qb_save_path`, which defaults to `/downloads`.
- Add explicit qB add/start/recheck parameters and a conservative migration for matching incomplete tasks that used the legacy host save path; migration removes only the qB task record with `deleteFiles=false`.
- Show both filesystem namespaces in the delivery plan and configuration guidance.

## 1.0.0 — 2026-07-18

First stable release of the public headless production and release core.

### Reliability core

- Versioned Run, Stage, Artifact, Diagnostic, Profile, and ProductionRequest models.
- SQLite execution ledger with conservative status transitions and stale detection.
- `StageRunner`, structured errors, `needs_review`, and safe result reuse.
- `ArtifactWriter`/`ArtifactBatchWriter` candidate validation, backups, atomic replacement, and recovery.

### Media and subtitles

- Explicit registration of videos, subtitles, fonts, chapters, and attachments.
- Candidate matching with evidence and explicit Artifact-ID confirmation.
- Audio/subtitle/attachment extraction and MLX Whisper transcription.
- ASS-aware Simplified-to-Traditional conversion without an implicit whole-file fallback.
- `ass-analysis-v4`, controlled normalization, font requirements, OP/ED/IN semantic grouping, and standard ASS reconstruction.

### Production and release

- HEVC 10-bit, H.264 hard-subtitle, and multi-subtitle Matroska ProductionRequests.
- libtorrent-only v1+v2 hybrid or v1 torrent creation.
- R2 upload, SSH+rclone remote pull, qBittorrent seeding, and Anibt preview/formal publishing.
- Bounded receipts, explicit external-action confirmation, and live/content validation during reuse.

### Workstation and series setup

- Strict, atomic `bgminfo/series.json` creation with default no-overwrite behavior, Downloads fallback, Notebook API, and guided question mode.
- Three-phase episode workstation with direct-parent series configuration inheritance and readable SQLite-derived snapshots.
- Real single-step delivery for font diagnostics, HEVC, CHS/CHT hardsubs, subtitle muxing, and torrent creation.
- Non-blocking Aegisub-owned font diagnostics, HEVC-to-ordered-CHS/CHT Matroska production, redacted credential status, and scoped release-batch snapshots.

### Credentials and publication readiness

- Unified `CredentialService` with default manifest discovery, profile CRUD, labels/descriptions, locking, rollback, reference checks, validation, and read-only probes.
- macOS Login Keychain profiles for R2, qBittorrent, and Anibt; OpenSSH and VPS rclone remain under their native managers.
- Full Python feature dependencies are installed by default; no extras selector is required. FFmpeg/ffprobe and MKVToolNix remain explicit Homebrew-managed system prerequisites, while SSH uses the macOS client.
- Repository release-boundary cleanup, private-environment redaction, wheel-content checks, and documentation for users and maintainers.

### Upgrade note

The package version is part of Stage tool fingerprints. The first 1.0.0 execution may therefore rerun work previously recorded with a 0.x tool identity. Existing outputs are not deleted automatically and remain protected by normal validation, backup, and transaction rules.
