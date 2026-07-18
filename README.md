# bmlsub 1.0.0

`bmlsub` is a macOS/Apple Silicon local headless media-production core. Its implemented public entries are the `bmlsub` CLI, `Pipeline`, and `CredentialService`. This page describes the current `Project/bmlsub` code, not planned features.

[中文文档](docs/zh/README.md)

## Full installation from GitHub

After the remote repository has been synchronized, install the code and all Python feature dependencies with:

```bash
conda activate base
python -m pip install git+https://github.com/microseventh/bmlsub.git
```

This installs every Python feature dependency. pip does not install the system programs `ffmpeg`, `ffprobe`, `mkvmerge`, or `ssh`; see [Getting started](docs/getting-started.md).

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

A `remux` enum exists in the model, but the CLI and executable Profile contract reject it; independent remux is not a 1.0.0 feature.

## Current boundaries

There is no general cross-process Stage/output lock, automatic HTTP provider retry/backoff, automatic import of existing files as successful Stages, mutable job control, GUI, daemon, queue, or Remote Worker. Run query is read-only. File commit and SQLite Artifact registration are separate transaction boundaries.

## Documentation

[Getting started](docs/getting-started.md) · [Core concepts](docs/concepts.md) · [CLI](docs/cli.md) · [Python API](docs/python-api.md) · [Credentials](docs/credentials.md) · [Security](docs/security.md) · [Architecture](docs/architecture.md) · [Development](docs/development.md) · [Release](docs/release.md)

[ADR 0001](docs/adr/0001-reliability-core.md) records the reliability-core architectural decision. Current behavior is defined by the package source and the manuals above.

CLI stdout is one final JSON document. Exit codes: success/reuse `0`, failure `1`, `needs_review` `2`.

License: MIT.
