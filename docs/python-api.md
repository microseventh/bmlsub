# Python API and Profiles

[中文](zh/python-api.md) · [Documentation home](../README.md)

Recommended low-level entries are `Pipeline` and `CredentialService` from `bmlsub`. Episode orchestration is exported from `bmlsub.workstation`: `plan_preprocess`, `run_preprocess`, `plan_delivery`, `run_delivery`, `run_delivery_step`, `plan_publish`, `run_publish`, and `load_status`.

`WorkstationConfig.from_series_context()` inherits release names, Production Profiles, publish settings, and credential aliases from the episode's direct-parent `bgminfo/series.json`. Explicit arguments override only the supplied fields, and the resolved values are persisted in `workstation/state/config.json`. `run_delivery_step()` executes the named step rather than running the whole flow and returning a snapshot: supported steps are font validation, HEVC, CHS/CHT hardsub, subtitle mux, and torrent creation; prerequisites must already be present in the manifest.

Series metadata can be initialized directly from a Notebook or Python process with `create_series_metadata(series_folder_name, *, parent_dir=None, ...)`. The target is `<parent>/<name>/bgminfo/series.json`; an omitted parent means `~/Downloads`. The function validates the complete schema and controlled Profiles before filesystem changes, commits atomically, and refuses an existing file unless `replace=True`. `series_metadata_questions()` exposes the ordered question contract, while `prompt_series_metadata()` collects interactive answers and delegates to the pure creator.

`Pipeline` currently exposes subtitle compatibility, asset registration/query/matching, media listing/extraction, transcription, ASS analysis/normalization/reconstruction and analysis data helpers, ProductionRequest CRUD/execution, torrent/R2/remote/qB/Anibt, and Run query.


`validate_subtitles()` keeps compatibility parameters: `source_video` and `fallback_to_full_file` are currently ignored. It returns compatibility fields plus structured Stage fields. Other Stage calls generally return `StageResult.to_dict()`. Queries may return dict, list, or `None`.

`run_publish()` contains the workstation confirmation boundary and returns `awaiting_confirmation` unless `confirm_external_action=True`. Low-level `Pipeline` release methods do not contain that flag; embedding applications that call them directly must implement confirmation. Fake clients can be injected for tests.

`CredentialService` exposes list/get/status/validate, transactional create/update/delete, bounded probe, and R2/qB/Anibt/SSH/remote-pull resolvers. Resolver results may contain short-lived secrets and must not be serialized.

Production Profiles are strict: HEVC allows videotoolbox/libx265 and controlled 10-bit/AAC settings; hardsub is libx264 with controlled v1/v2 parameters; mux subtitle controls audio/default/forced ordinals. Full and single-step workstation delivery use the final resolved `config.delivery` parameters, so an empty CLI JSON object does not erase series defaults. The standard internal-subtitle product consumes the generated HEVC Artifact, ordered CHS then CHT ASS Artifacts, and all registered font Artifacts as Matroska attachments. ASS analysis and reconstruction Profiles reject unknown fields and validate nested policy types. Release Profile fields are documented in [Release](release.md).

`StageResult.to_dict()` fields are exactly `run_id`, `stage_name`, `status`, `artifacts`, `diagnostics`, `error`, `retryable`, `needs_review`, `reused`, `started_at`, `finished_at`, and `duration_ms`. Compatibility subtitle output additionally uses `stage`.

The top-level package also exports selected models, stores, runners, writers, asset/media helpers, subtitle/transcription functions, and `normalize_h264_parameters()`. Non-exported domain helpers are not the primary compatibility surface.
