"""User-facing workstation discovery, stage inspection, and confirmed dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import sqlite3

from .common import FONT_SUFFIXES, discover_source_video, resolve_explicit_path
from .series import SeriesMetadata
from .state import load_manifest, read_json


_PRODUCT_MANIFEST_KEYS = (
    "hardsub_chs_artifact_id",
    "hardsub_cht_artifact_id",
    "muxed_mkv_artifact_id",
)
_TORRENT_MANIFEST_KEYS = ("mp4_chs", "mp4_cht", "mkv_hevc")
REBUILD_TARGETS = (
    "preprocess", "delivery", "validate_subtitles_fonts", "encode_hevc",
    "encode_hardsub_chs", "encode_hardsub_cht", "mux_subtitles",
    "create_torrents",
)


def resolve_series_root(value: Path | str | None = None) -> Path:
    """Resolve the metadata-owning workspace without guessing from digits alone."""
    candidate = Path.cwd() if value is None else Path(value).expanduser()
    resolved = candidate.resolve()
    if not resolved.is_dir():
        raise ValueError("series root directory does not exist")
    if (resolved / "bgminfo" / "series.json").is_file():
        return resolved
    if resolved.name.isdigit() and (resolved.parent / "bgminfo" / "series.json").is_file():
        return resolved.parent
    return resolved


def discover_episode_directories(series_root: Path | str) -> tuple[Path, ...]:
    root = resolve_series_root(series_root)
    episodes = [item.resolve() for item in root.iterdir() if item.is_dir() and item.name.isdigit()]
    if not episodes and root.name.isdigit():
        return (root,)
    return tuple(sorted(episodes, key=lambda item: (int(item.name), len(item.name), item.name)))


def inspect_series_workspace(series_root: Path | str | None = None) -> dict[str, Any]:
    root = resolve_series_root(series_root)
    metadata_path = root / "bgminfo" / "series.json"
    episodes = discover_episode_directories(root)
    blocking = []
    metadata = None
    if not metadata_path.is_file():
        blocking.append({
            "code": "series_metadata_missing",
            "message": "bgminfo/series.json is missing",
        })
    else:
        try:
            metadata = SeriesMetadata.load(metadata_path)
            if metadata.traditionalization.get("status") != "resolved":
                blocking.append({
                    "code": "series_traditionalization_pending",
                    "message": "traditional series title or group name is pending conversion",
                })
        except (OSError, ValueError) as exc:
            blocking.append({
                "code": "series_metadata_invalid",
                "message": str(exc),
            })
    if not episodes:
        blocking.append({
            "code": "episode_directories_missing",
            "message": "series root has no direct numeric episode directory",
        })
    return {
        "schema_version": "workstation-start-v1",
        "status": "succeeded" if not blocking else "needs_review",
        "workspace_layout": (
            "standalone_episode" if len(episodes) == 1 and episodes[0] == root
            else "series"
        ),
        "series_root": str(root),
        "working_directory": str(root),
        "metadata_path": str(metadata_path),
        "metadata": metadata.to_dict() if metadata else None,
        "episodes": [{"episode_id": item.name, "episode_dir": str(item)} for item in episodes],
        "blocking": blocking,
        "next_action": _series_next_action(blocking),
    }


def inspect_episode_stage(
    series_root: Path | str, episode_id: str, *,
    source_video: Path | str | None = None,
    production_subtitle: Path | str | None = None,
) -> dict[str, Any]:
    root = resolve_series_root(series_root)
    identifier = episode_id.strip()
    if not identifier.isdigit():
        raise ValueError("episode_id must contain digits only")
    episode = root if root.name == identifier else (root / identifier).resolve()
    is_standalone = episode == root and root.name.isdigit()
    if not is_standalone and (episode.parent != root or not episode.is_dir()):
        raise ValueError("selected episode is not a direct numeric directory of the series root")
    if is_standalone and not episode.is_dir():
        raise ValueError("selected standalone episode directory does not exist")
    metadata = SeriesMetadata.load(root / "bgminfo" / "series.json")
    state_dir = episode / "workstation" / "state"
    manifest_path = state_dir / "manifest.json"
    summary_path = state_dir / "summary.json"
    database_path = state_dir / "state.sqlite3"
    has_registered_state = manifest_path.is_file()
    if has_registered_state:
        result = _inspect_registered_state(
            episode, manifest_path, summary_path,
            source_video=source_video, production_subtitle=production_subtitle,
        )
    else:
        result = _inspect_physical_state(
            episode, identifier, source_video=source_video,
            production_subtitle=production_subtitle,
        )
    return {
        "schema_version": "workstation-start-v1",
        "status": "needs_review" if result["detected_phase"] in {"blocked", "ambiguous", "human_handoff"} else "succeeded",
        "workspace_layout": "standalone_episode" if is_standalone else "series_episode",
        "series_root": str(root),
        "episode_dir": str(episode),
        "episode_id": identifier,
        "working_directory": str(episode),
        "metadata_path": str(metadata.path),
        "state_source": "registered" if has_registered_state else "physical_files",
        **result,
    }


def execute_recommended_action(
    inspection: dict[str, Any], *, confirmed: bool = False,
    confirm_external_action: bool = False, force: bool = False,
    source_video: Path | str | None = None,
    reference_stream_index: int | None = None,
    reference_stream_indices=(), reference_policy: str = "all_matching",
    audio_stream_index: int | None = None,
    production_subtitle: Path | str | None = None,
    whisper_jobs=(), delivery_selection=None,
) -> dict[str, Any]:
    """Dispatch the inspected action only after an explicit local confirmation."""
    if not inspection.get("executable"):
        return {
            "status": "needs_review",
            "inspection": inspection,
            "error": {
                "code": "workstation_action_blocked",
                "message": "the detected stage has no executable automatic action",
            },
        }
    if not confirmed:
        return {
            "status": "awaiting_confirmation",
            "inspection": inspection,
            "next_action": inspection.get("recommended_action"),
        }
    action = inspection.get("recommended_action")
    episode_dir = inspection["episode_dir"]
    episode_id = inspection["episode_id"]
    if action == "run_preprocess":
        from .preprocess import run_preprocess
        return run_preprocess(
            episode_dir, episode_id=episode_id, source_video=source_video,
            reference_stream_index=reference_stream_index,
            reference_stream_indices=reference_stream_indices,
            reference_policy=reference_policy,
            audio_stream_index=audio_stream_index, whisper_jobs=whisper_jobs,
            force=force,
        )
    if action == "run_delivery":
        from .delivery import run_delivery
        return run_delivery(
            episode_dir, episode_id=episode_id,
            production_subtitle=production_subtitle,
            selection=delivery_selection, force=force,
        )
    if action == "run_publish":
        if not confirm_external_action:
            return {
                "status": "awaiting_confirmation",
                "inspection": inspection,
                "next_action": "confirm_external_action",
            }
        from .publish import run_publish
        return run_publish(
            episode_dir, episode_id=episode_id,
            confirm_external_action=True, force=force,
        )
    return {
        "status": "needs_review",
        "inspection": inspection,
        "error": {"code": "unsupported_action", "message": f"unsupported action: {action}"},
    }


def plan_rebuild(series_root: Path | str, episode_id: str,
                 target: str | None = None) -> dict[str, Any]:
    root = resolve_series_root(series_root)
    identifier = episode_id.strip()
    episode = (root / identifier).resolve()
    if not identifier.isdigit() or episode.parent != root or not episode.is_dir():
        raise ValueError("rebuild episode must be a direct numeric directory")
    if target is not None and target not in REBUILD_TARGETS:
        raise ValueError(f"unsupported rebuild target: {target}")
    return {
        "schema_version": "workstation-rebuild-plan-v1",
        "status": "succeeded" if target else "needs_review",
        "series_root": str(root), "episode_dir": str(episode),
        "episode_id": identifier, "target": target,
        "force": True, "external_publish_allowed": False,
        "available_targets": list(REBUILD_TARGETS),
        "next_action": "select_rebuild_target" if target is None else "confirm_rebuild",
    }


def run_rebuild(plan: dict[str, Any], *, confirmed: bool = False,
                source_video: Path | str | None = None,
                production_subtitle: Path | str | None = None,
                reference_stream_index: int | None = None,
                reference_stream_indices=(), reference_policy: str = "all_matching",
                audio_stream_index: int | None = None, whisper_jobs=()) -> dict[str, Any]:
    if plan.get("target") not in REBUILD_TARGETS:
        return {"status": "needs_review", "plan": plan,
                "next_action": "select_rebuild_target"}
    if not confirmed:
        return {"status": "awaiting_confirmation", "plan": plan,
                "next_action": "confirm_rebuild"}
    target = plan["target"]
    episode_dir = plan["episode_dir"]
    episode_id = plan["episode_id"]
    if target == "preprocess":
        from .preprocess import run_preprocess
        return run_preprocess(
            episode_dir, episode_id=episode_id, source_video=source_video,
            reference_stream_index=reference_stream_index,
            reference_stream_indices=reference_stream_indices,
            reference_policy=reference_policy,
            audio_stream_index=audio_stream_index, whisper_jobs=whisper_jobs,
            force=True,
        )
    from .delivery import run_delivery, run_delivery_step
    if target == "delivery":
        return run_delivery(
            episode_dir, episode_id=episode_id,
            production_subtitle=production_subtitle, force=True,
        )
    return run_delivery_step(
        target, episode_dir, episode_id=episode_id,
        production_subtitle=production_subtitle, force=True,
    )


def _inspect_registered_state(
    episode: Path, manifest_path: Path, summary_path: Path, *,
    source_video: Path | str | None = None,
    production_subtitle: Path | str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(episode)
    summary = read_json(summary_path, {})
    evidence = [{"code": "manifest_present", "path": str(manifest_path)}]
    missing = []
    blocking = []
    valid_artifact_ids = _validated_manifest_artifact_ids(
        episode, manifest, evidence,
    )

    def artifact_is_current(artifact_id: Any) -> bool:
        return bool(artifact_id) and (
            valid_artifact_ids is None or artifact_id in valid_artifact_ids
        )

    source_id = manifest.get("source", {}).get("video_artifact_id")
    source_registered = artifact_is_current(source_id)
    if source_registered:
        evidence.append({"code": "source_video_registered", "artifact_id": source_id})
    else:
        missing.append("source.video_artifact_id")
    products = manifest.get("products", {})
    torrents = manifest.get("torrents", {})
    publish = manifest.get("publish", {})
    products_complete = all(
        artifact_is_current(products.get(key)) for key in _PRODUCT_MANIFEST_KEYS
    )
    torrents_complete = all(
        artifact_is_current(torrents.get(key)) for key in _TORRENT_MANIFEST_KEYS
    )
    publish_complete = _publish_receipts_complete(publish, valid_artifact_ids)
    if publish_complete:
        evidence.append({"code": "publish_receipts_complete"})
        return _inspection_result("complete", "high", evidence, missing, blocking, None, False)
    if products_complete and torrents_complete:
        evidence.append({"code": "local_products_and_torrents_registered"})
        return _inspection_result(
            "publish", "high", evidence, missing, blocking, "run_publish", True,
        )

    if source_registered:
        preprocess_status = summary.get("preprocess", {}).get("status")
        if preprocess_status in {"failed", "needs_review", "blocked", "running", "interrupted", "partial"}:
            evidence.append({"code": "preprocess_incomplete", "status": preprocess_status})
            return _inspection_result(
                "preprocess", "high", evidence, missing, blocking,
                "run_preprocess", True,
            )

    physical = _inspect_physical_state(
        episode, episode.name, source_video=source_video,
        production_subtitle=production_subtitle,
    )
    if physical["detected_phase"] in {
        "local_production", "ambiguous", "blocked", "human_handoff",
    }:
        physical["evidence"] = evidence + physical["evidence"]
        physical["confidence"] = (
            "high" if physical["detected_phase"] == "local_production" else "medium"
        )
        if physical["detected_phase"] == "local_production":
            physical["missing"].extend(
                key for key in _PRODUCT_MANIFEST_KEYS if not products.get(key)
            )
        if summary.get("preprocess", {}).get("status") == "succeeded":
            physical["evidence"].append({"code": "preprocess_summary_succeeded"})
        return physical

    if source_registered:
        preprocess_status = summary.get("preprocess", {}).get("status")
        if preprocess_status == "succeeded":
            evidence.append({"code": "preprocess_summary_succeeded"})
            missing.extend(["formal CHS subtitle", "top-level Aegisub fonts"])
            return _inspection_result(
                "human_handoff", "high", evidence, missing, blocking, None, False,
            )
        from .preprocess import plan_preprocess
        preprocess_plan = plan_preprocess(episode, source_video=source_video)
        if preprocess_plan["status"] == "succeeded":
            return _inspection_result(
                "preprocess", "high", evidence, missing, blocking,
                "run_preprocess", True,
            )
        error = preprocess_plan.get("error")
        if error:
            blocking.append(error)
        return _inspection_result(
            "ambiguous" if preprocess_plan["status"] == "needs_review" else "blocked",
            "high", evidence, missing, blocking, None, False,
        )
    blocking.append({
        "code": "source_artifact_missing",
        "message": "manifest has no source video Artifact and physical discovery is not ready",
    })
    return _inspection_result("blocked", "high", evidence, missing, blocking, None, False)


def _inspect_physical_state(
    episode: Path, episode_id: str, *,
    source_video: Path | str | None = None,
    production_subtitle: Path | str | None = None,
) -> dict[str, Any]:
    video, video_error = discover_source_video(episode, source_video)
    entries = tuple(item for item in episode.iterdir() if item.is_file())
    fonts = tuple(sorted(
        item.resolve() for item in entries if item.suffix.lower() in FONT_SUFFIXES
    ))
    if production_subtitle is not None:
        selected = resolve_explicit_path(episode, production_subtitle)
        production_subtitles = (selected,) if selected.is_file() and selected.suffix.lower() == ".ass" else ()
        subtitle_error = None if production_subtitles else {
            "code": "input_missing", "message": "explicit production subtitle does not exist",
        }
    else:
        production_subtitles = tuple(sorted(
            item.resolve() for item in entries
            if item.name.lower() == f"{episode_id}.chs&jpn.ass".lower()
        ))
        subtitle_error = None
    reference_subtitles = tuple(sorted(
        item.resolve() for item in entries
        if item.suffix.lower() in {".ass", ".srt"}
        and any(marker in item.name.lower() for marker in (".en.", ".eng."))
    ))
    evidence = []
    missing = []
    blocking = []
    if video_error:
        blocking.append(video_error)
        phase = "ambiguous" if video_error["code"].endswith("ambiguous") else "blocked"
        return _inspection_result(phase, "low", evidence, missing, blocking, None, False)
    evidence.append({"code": "source_video_present", "path": str(video)})
    if subtitle_error:
        blocking.append(subtitle_error)
        missing.append("explicit production subtitle")
        return _inspection_result("blocked", "medium", evidence, missing, blocking, None, False)
    if len(production_subtitles) > 1:
        blocking.append({
            "code": "production_subtitle_ambiguous", "message": "multiple exact formal CHS subtitles were found",
            "candidates": [str(item) for item in production_subtitles],
        })
        return _inspection_result("ambiguous", "low", evidence, missing, blocking, None, False)
    if production_subtitles:
        evidence.append({"code": "formal_chs_present", "path": str(production_subtitles[0])})
        if fonts:
            evidence.append({"code": "top_level_fonts_present", "count": len(fonts)})
            return _inspection_result(
                "local_production", "medium", evidence, missing, blocking,
                "run_delivery", True,
            )
        missing.append("top-level Aegisub fonts")
        blocking.append({"code": "input_missing", "message": "formal CHS exists but top-level fonts are missing"})
        return _inspection_result("blocked", "medium", evidence, missing, blocking, None, False)
    missing.append(f"{episode_id}.CHS&JPN.ass")
    if reference_subtitles:
        evidence.append({
            "code": "reference_subtitle_present",
            "paths": [str(item) for item in reference_subtitles],
        })
        missing.append("formal CHS subtitle and Aegisub font package")
        return _inspection_result("human_handoff", "medium", evidence, missing, blocking, None, False)
    return _inspection_result("preprocess", "medium", evidence, missing, blocking, "run_preprocess", True)


def _saved_preprocess_options(episode_dir: Path | str) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    state = root / "workstation" / "state"
    config = read_json(state / "config.json", {})
    preprocess = config.get("preprocess", {}) if isinstance(config, dict) else {}
    references = preprocess.get("reference_tracks", {})
    audio = preprocess.get("audio_track", {})
    jobs = preprocess.get("whisper_jobs", [])
    plan = read_json(state / "preprocess-plan.json", {})
    plan_policy = plan.get("policy") if isinstance(plan, dict) else None
    manifest_selection = load_manifest(root).get("preprocess", {}).get(
        "reference_selection", {},
    )
    if not isinstance(manifest_selection, dict):
        manifest_selection = {}
    return {
        "reference_policy": manifest_selection.get(
            "policy", references.get("policy", "all_matching"),
        ),
        "reference_language": manifest_selection.get(
            "requested_language", references.get("language", "eng"),
        ),
        "reference_stream_indices": tuple(
            manifest_selection.get("resolved_stream_indices")
            or references.get("stream_indices") or ()
        ),
        "audio_language": audio.get("language", "jpn"),
        "audio_stream_index": audio.get("stream_index"),
        "whisper_jobs": tuple(jobs) if isinstance(jobs, list) else (),
        "transcription_policy": plan_policy or preprocess.get(
            "transcription_policy", "full",
        ),
    }


def _publish_receipts_complete(
    publish: Any, valid_artifact_ids: set[str] | None = None,
) -> bool:
    if not isinstance(publish, dict):
        return False

    def receipt_is_current(value: Any) -> bool:
        return isinstance(value, str) and bool(value) and (
            valid_artifact_ids is None or value in valid_artifact_ids
        )

    for section in ("r2", "remote"):
        receipts = publish.get(section)
        if not isinstance(receipts, dict) or not all(
            receipt_is_current(receipts.get(f"{key}:{label}"))
            for key in _TORRENT_MANIFEST_KEYS for label in ("content", "torrent")
        ):
            return False
    return all(
        isinstance(publish.get(section), dict)
        and all(receipt_is_current(publish[section].get(key))
                for key in _TORRENT_MANIFEST_KEYS)
        for section in ("qb", "anibt")
    )


def _validated_manifest_artifact_ids(
    episode: Path, manifest: dict[str, Any], evidence: list[dict[str, Any]],
) -> set[str] | None:
    """Return current manifest Artifact IDs when a real SQLite ledger exists."""
    database = episode / "workstation" / "state" / "state.sqlite3"
    if not database.is_file():
        return None
    try:
        with database.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                evidence.append({
                    "code": "registered_state_unreadable",
                    "message": "state.sqlite3 is not a valid SQLite database",
                })
                return set()
        from ..state.sqlite_store import SQLiteJobStore
        store = SQLiteJobStore.for_workspace(episode, database.parent)
        valid = set()
        for field, artifact_id in _manifest_artifact_references(manifest):
            artifact = store.get_artifact(artifact_id)
            current = False
            if artifact is not None and artifact.validation_status.value == "valid":
                try:
                    stat = artifact.path.stat()
                    current = (
                        artifact.path.is_file()
                        and stat.st_size == artifact.size
                        and stat.st_mtime_ns == artifact.mtime_ns
                    )
                except OSError:
                    current = False
            if current:
                valid.add(artifact_id)
            else:
                evidence.append({
                    "code": "registered_artifact_invalid",
                    "field": field,
                    "artifact_id": artifact_id,
                })
        return valid
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        evidence.append({
            "code": "registered_state_unreadable", "message": str(exc),
        })
        return set()


def _manifest_artifact_references(
    manifest: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []

    def add(field: str, value: Any) -> None:
        if isinstance(value, str) and value:
            references.append((field, value))

    def walk_artifact_fields(value: Any, field: str, key: str = "") -> None:
        if key.endswith("_artifact_id") or key == "artifact_id":
            add(field, value)
            return
        if key.endswith("_artifact_ids") or key == "artifact_ids":
            if isinstance(value, (list, tuple)):
                for index, artifact_id in enumerate(value):
                    add(f"{field}[{index}]", artifact_id)
            elif isinstance(value, dict):
                for nested_key, artifact_ids in value.items():
                    walk_artifact_fields(
                        artifact_ids, f"{field}.{nested_key}", "artifact_ids",
                    )
            return
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                walk_artifact_fields(
                    nested_value, f"{field}.{nested_key}", nested_key,
                )
        elif isinstance(value, list):
            for index, nested_value in enumerate(value):
                walk_artifact_fields(nested_value, f"{field}[{index}]", key)

    for section_name in ("source", "preprocess", "subtitles", "products", "fonts"):
        walk_artifact_fields(manifest.get(section_name, {}), section_name)
    for section_name in ("torrents", "publish"):
        section = manifest.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, dict):
                for nested_key, artifact_id in value.items():
                    add(f"{section_name}.{key}.{nested_key}", artifact_id)
            else:
                add(f"{section_name}.{key}", value)
    return tuple(references)


def _inspection_result(phase: str, confidence: str, evidence, missing, blocking,
                       action: str | None, executable: bool) -> dict[str, Any]:
    return {
        "detected_phase": phase,
        "confidence": confidence,
        "evidence": list(evidence),
        "missing": list(dict.fromkeys(missing)),
        "blocking": list(blocking),
        "recommended_action": action,
        "executable": executable,
    }


def _series_next_action(blocking: list[dict[str, Any]]) -> str | None:
    codes = {item.get("code") for item in blocking}
    if "series_metadata_missing" in codes:
        return "create_series_metadata"
    if "series_metadata_invalid" in codes:
        return "fix_series_metadata"
    if "series_traditionalization_pending" in codes:
        return "retry_traditionalization"
    if "episode_directories_missing" in codes:
        return "create_numeric_episode_directory"
    return None
