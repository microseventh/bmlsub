# Architecture (implemented code)

[中文](zh/architecture.md) · [Documentation home](../README.md)

The CLI parses arguments, JSON objects, confirmation flags, output, and exit codes; it forwards business work to `Pipeline`. Credential commands call `CredentialService`.

```text
CLI / Python API
  → Pipeline / CredentialService
  → domain execution
  → StageRunner + SQLiteJobStore + ArtifactWriter/ArtifactBatchWriter
```

Packages: `state` owns models/fingerprints/SQLite; `execution` owns StageRunner/ProcessRunner/errors; `artifacts` owns file transactions; `assets` registration/matching; `media` probing/extraction; `transcription` MLX; `ass_analysis` parsing/profiles/analysis/reconstruction; `production` requests/profiles/encoding/mux; `credentials` secure JSON/manifest/Keychain/SSH/probe; `release` torrent/R2/remote/qB/Anibt; `workstation` owns direct-parent series inheritance, three-phase episode orchestration, real single-step delivery, and readable state/Artifact/batch snapshots; `pipeline` is the low-level facade; `cli` is the console contract.

Input validation and `stage_inputs` registration occur before reuse lookup. Reuse keys are stage name plus input/parameter/tool fingerprints, followed by Artifact revalidation.

SQLite is the migrated execution-state authority; files hold content; Artifacts connect them. File commit precedes Artifact registration, creating an explicit transaction seam.

The workstation fixes its database at `<episode>/workstation/state/state.sqlite3` and exports resolved config, manifest, summary, step, and Artifact JSON. Resolved series defaults plus episode overrides are persisted in `config.json`. Redacted credential availability and the selected release scope may be frozen separately in `credentials-status.json` and `release-batch.json`; neither replaces SQLite.

`run_delivery_step()` directly executes the requested production step. HEVC writes its Artifact ID to the manifest; mux then consumes that HEVC Artifact, ordered CHS/CHT subtitle Artifacts, and all registered font Artifacts. It does not call the full delivery flow first.

Credential manifests have a file lock, but no general Stage job lock exists. This is a single-machine headless core, not a multi-process task server. GUI, daemon, queue, cancellation/pause, Remote Worker, plugin system, and web console are not implemented.
