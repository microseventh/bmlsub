# bmlsub 1.1.3

`bmlsub` is a macOS/Apple Silicon local headless media-production core. Its public entries are the `bmlsub` CLI, `Pipeline`, and `CredentialService`. This page describes the capabilities implemented in this release, not planned features.

[中文文档](docs/zh/README.md)

## Full installation from GitHub

After the remote repository has been synchronized, install the code and all Python feature dependencies with:

```bash
conda activate base
python -m pip install git+https://github.com/microseventh/bmlsub.git
```

This installs every Python feature dependency. pip does not install the system programs `ffmpeg`, `ffprobe`, `mkvmerge`, or `ssh`; see [Getting started](docs/getting-started.md).

## Workstation fast mode

From a series root, ordinary users only need three entry points:

```bash
# Interactive Workstation fast mode
bmlsub workstation start

# Interactive external delivery
bmlsub workstation start delivery

# Unattended external delivery
bmlsub workstation start delivery -y
```

`bmlsub workstation start` selects the episode and current phase through the interactive interface. It offers quick/full/no-transcription preprocess choices and local production scope without requiring ordinary users to compose low-level flags. The human translation handoff remains explicit and is never bypassed by fast mode.

After local products and Torrents are complete, `bmlsub workstation start delivery` checks the Credential Manifest, macOS Keychain profiles, SSH alias, and host/container paths. It prints one concise summary and confirms each product in R2 → VPS pull → qB seed → Anibt order. Use `--configure` for first setup or credential repair and `--verbose-plan` only when complete file mappings are needed.

`bmlsub workstation start delivery -y` uses existing validated configuration and credentials and skips external-delivery confirmations. Valid Stage fingerprints and receipts are reused; `-y` does not imply `--force` and does not unconditionally repeat uploads or publications. Missing or invalid credentials stop with `needs_review` instead of requesting secrets. In a TTY, an episode can still be selected interactively; fully noninteractive automation over a series containing multiple episodes must identify the episode through the advanced CLI interface.

English details: [Getting started](docs/getting-started.md) · 中文详情：[快速开始](docs/zh/getting-started.md)


Use `bmlsub workstation rebuild` to force-rebuild preprocess, full local delivery, or one delivery step. Rebuild keeps historical state and never offers publication as a target.

Traditional series titles and group names are generated automatically from the Simplified values through the configured Taiwan conversion provider. Failed conversions are recorded in `bgminfo/series.json` as pending retry state; rerun `workstation start` or use `workstation series retry-traditionalization` instead of re-entering the Traditional text.

For episode production, Workstation requires a formal `<episode>.CHS&JPN.ass` and also checks for an optional `<episode>.CHT&JPN.ass` (case-insensitive). When the formal CHT/JPN ASS exists, it is registered and used directly for the `h264-cht` product and the CHT track in the MKV. Only when it is absent does Workstation call the configured Taiwan conversion provider to generate CHT from CHS.

## Implemented capabilities

- explicit registration, query, candidate matching, and confirmation for videos, ASS/SRT subtitles, fonts, chapters, and attachments;
- media track listing plus archive/transcription audio, text-subtitle, and attachment extraction;
- MLX Whisper `direct`, `chunked`, and `both` modes;
- ASS conversion, `ass-analysis-v4`, controlled normalization, font requirements, OP/ED/IN collapse, and reconstruction;
- `encode + hevc-10bit`, `hardsub + h264-chs/h264-cht`, and `mux_subtitle + mkv-subtitle` ProductionRequests;
- libtorrent torrent creation, R2 upload, SSH+rclone pull, qBittorrent seeding, and Anibt preview/formal publication;
- Keychain R2/qB/Anibt profiles, OpenSSH and remote-pull profiles, CRUD/status/validation/read-only probes;
- three-phase episode workstation orchestration with direct-parent series/Profile inheritance, real single-step delivery, `workstation/state/state.sqlite3`, readable step/Artifact/credential/batch snapshots, non-blocking font diagnostics, parallel video products, torrents, and explicitly confirmed publication;
- Run, Stage, Artifact, ProductionRequest, SQLite, fingerprints, stale detection, transactional output, and safe reuse.

A `remux` enum exists in the model, but the CLI and executable Profile contract reject it; independent remux is not a 1.1.3 feature.

## Current boundaries

There is no general cross-process Stage/output lock, automatic HTTP provider retry/backoff, automatic import of existing files as successful Stages, mutable job control, GUI, daemon, queue, or Remote Worker. Run query is read-only. File commit and SQLite Artifact registration are separate transaction boundaries.

## Documentation

[Getting started](docs/getting-started.md) · [Core concepts](docs/concepts.md) · [CLI](docs/cli.md) · [Python API](docs/python-api.md) · [Credentials](docs/credentials.md) · [Security](docs/security.md) · [Architecture](docs/architecture.md) · [Development](docs/development.md) · [Release](docs/release.md)

[ADR 0001](docs/adr/0001-reliability-core.md) records the reliability-core architectural decision. Current behavior is defined by the package source and the manuals above.

CLI stdout is one final JSON document. Exit codes: success/reuse `0`, failure `1`, `needs_review` `2`.

License: MIT.
