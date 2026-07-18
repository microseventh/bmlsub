# CLI reference

[中文](zh/cli.md) · [Documentation home](../README.md)

The exact parser is `bmlsub.cli.build_parser()`. Stdout is one final JSON document; incidental output goes to stderr. Exit codes are 0 for ordinary success/reuse, 1 for failed/error payloads, and 2 for `needs_review`.

Command paths:

```text
episode validate
asset register-video | register-subtitle | register-font | register-chapter | register-attachment
asset match | confirm | manifest | show | list
media tracks | extract-audio | extract-subtitle | extract-attachments
subtitle analyze-ass | normalize-ass | reconstruct-ass
transcribe
production create | show | list | execute
credentials import-json | upsert-secret | list | get | create | update | delete | status | validate | probe
release create-torrent | upload-r2 | pull-remote | seed-qbittorrent | publish-anibt
workstation preprocess | delivery | publish | status
run show
```

Implemented details:

- episode conversion defaults to Taiwan/zhconvert/60 seconds; whole-file mode is explicit; the no-fallback flag is deprecated compatibility.
- video registration requires one or more purposes. Media commands require exactly one of video Artifact ID or purpose.
- audio extraction defaults to `both`; transcription defaults to direct, large-v3-turbo/main/ja, 240-second chunks and 5-second overlap.
- ASS commands require registered subtitle or analysis Artifact IDs and JSON-object Profiles.
- Production create accepts only encode, hardsub, or mux_subtitle and the four matching output Profiles. Execute defaults to ffmpeg/ffprobe/mkvmerge and a 7200-second process timeout.
- Credential secret files must pass secure-file validation. Delete and probe require explicit confirmation flags.
- External release commands require `--confirm-external-action`; local torrent creation does not.
- `workstation series show --workspace <episode>` strictly loads the direct-parent `bgminfo/series.json`. `workstation series create` creates `<parent>/<series-folder>/bgminfo/series.json`; omitted parent defaults to `~/Downloads`. It requires the folder name, titles, romanized title, and groups; production/publish JSON and IDs are optional. Existing metadata is refused unless `--replace` is explicit. `--interactive` asks the same questions on stderr while preserving one final stdout JSON document.
- Workstation preprocess discovers one top-level source, extracts one English reference subtitle and Japanese audio, and can run configured Whisper jobs. Delivery inherits release names and Production Profiles from the direct parent `bgminfo/series.json`; explicit CLI values are episode overrides. Full delivery validates the CHS ASS/Aegisub-font handoff, creates CHS/CHT snapshots, records non-blocking font diagnostics, builds three products and matching torrents.
- `workstation delivery --step` performs a real single step. Choices are `validate_subtitles_fonts`, `encode_hevc`, `encode_hardsub_chs`, `encode_hardsub_cht`, `mux_subtitles`, and `create_torrents`; `all`/`delivery` run the full flow. A single step consumes prerequisite Artifact IDs already recorded in `manifest.json` and never runs the full delivery implicitly.
- Publish returns `awaiting_confirmation` until `--confirm-external-action` is present. Workstation state always lives below `<episode>/workstation/state`; readable snapshots include resolved config/manifest/summary and may include redacted `credentials-status.json` and a scoped `release-batch.json`.
- Run show is read-only.

See the Chinese reference for the complete per-command required/default/choice tables; both are derived from the same parser.
