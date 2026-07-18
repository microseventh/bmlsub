# Core concepts and current boundaries

[中文](zh/concepts.md) · [Documentation home](../README.md)

Implemented statuses are Run `pending/running/succeeded/failed/needs_review/interrupted`, Stage `pending/running/succeeded/failed/skipped/stale/needs_review`, Artifact validation `discovered/unverified/valid/invalid/stale`, and match `confirmed/inferred/ambiguous/unmatched`.

`StageResult.reused=true` is valid only with `status=skipped`. Failed results require an error; review results require the review flag.

Artifacts independently record path identity, fingerprints, validation, bounded metadata, and purposes. ProductionRequest inputs express selection; `stage_inputs` records what a Stage actually consumed.

Executable ProductionRequests are encode/hevc-10bit, hardsub/h264-chs or h264-cht, and mux_subtitle/mkv-subtitle. The model's remux enum is not executable. The workstation's standard internal-subtitle chain is direct source → generated HEVC Artifact → muxed MKV. Hardsubs continue to branch from the direct source, not the HEVC intermediate. Ordered subtitle inputs define track order, and all registered top-level Aegisub font Artifacts are real mux/hardsub inputs.

Aegisub Fonts Collector is the authority for font family, variant, and glyph completeness. BMLSub registers fonts, produces ASS/font diagnostics, and validates Matroska attachment identity/count, but missing-variant or missing-glyph analysis counts are non-blocking. The workstation font-validation step may therefore succeed while explicitly recording Aegisub ownership.

`StageRunner` creates Run/Stage records, validates inputs, records inputs, checks reusable fingerprints and output validity, executes the adapter, registers returned Artifacts, and closes lifecycle state. Invalid historical output makes the old Stage stale.

Formal output uses candidate validation, backup, and atomic replacement. File commit and SQLite Artifact registration are separate: registration failure leaves a failed Stage and a diagnostic file, never reusable success.

Credential manifests use `flock`; SQLite serializes writes; formal replacement is atomic. There is no general cross-process episode/Artifact/output job lock. Do not run competing processes for the same formal target.

The default subtitle provider performs one synchronous HTTP request with no automatic retry/backoff. Existing files are not inferred as successful historical Stages. Run query is read-only, and there is no cancel/pause/resume/queue, GUI, daemon, or Remote Worker.
