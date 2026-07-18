"""Workstation preprocessing planning and execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..media.tracks import languages_match
from .common import discover_source_video, ensure_directories, open_workstation
from .models import PreprocessConfig, WorkstationConfig
from .series import discover_series_context
from .state import (
    atomic_write_json, load_manifest, pipeline_payload_step, refresh_summary,
    update_manifest, write_step, step_payload,
)


VIDEO_PURPOSES = (
    "source", "extract", "transcribe_source", "encode_source", "hardsub_source",
    "package_source",
)


def plan_preprocess(episode_dir: Path | str, *, episode_id: str | None = None,
                    source_video: Path | str | None = None,
                    reference_language: str = "eng",
                    reference_stream_index: int | None = None,
                    audio_language: str = "jpn",
                    audio_stream_index: int | None = None,
                    whisper_jobs=()) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    video, error = discover_source_video(root, source_video)
    status = "needs_review" if error and error["code"].endswith("ambiguous") else "failed" if error else "succeeded"
    return {
        "schema_version": "workstation-plan-v1", "workflow_id": f"episode-{identifier}",
        "phase": "preprocess", "status": status, "episode_dir": str(root),
        "episode_id": identifier, "source_video": str(video) if video else None,
        "selection": {
            "reference_language": reference_language,
            "reference_stream_index": reference_stream_index,
            "audio_language": audio_language,
            "audio_stream_index": audio_stream_index,
        },
        "whisper_jobs": [item.to_dict() for item in whisper_jobs],
        "steps": [
            "preprocess.inspect_video", "preprocess.extract_reference_subtitle",
            "preprocess.extract_audio",
            *[f"preprocess.transcribe.{item.name}" for item in whisper_jobs],
        ],
        "error": error,
    }


def run_preprocess(episode_dir: Path | str, *, episode_id: str | None = None,
                   source_video: Path | str | None = None,
                   reference_language: str = "eng",
                   reference_stream_index: int | None = None,
                   audio_language: str = "jpn",
                   audio_stream_index: int | None = None,
                   whisper_jobs=(), force: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    config = WorkstationConfig.from_series_context(
        context,
        preprocess=PreprocessConfig(source_video=source_video, whisper_jobs=tuple(whisper_jobs)),
    )
    paths = ensure_directories(root)
    update_manifest(root, series={
        "root": str(context.series_root), "folder_name": context.series_folder_name,
        "metadata_path": str(context.metadata.path),
        "metadata_hash": context.metadata.content_hash,
    }, episode={"directory_name": context.episode_id})
    atomic_write_json(paths["state"] / "config.json", config.to_dict())
    workstation = open_workstation(config)
    plan = plan_preprocess(
        root, episode_id=identifier, source_video=source_video,
        reference_language=reference_language, reference_stream_index=reference_stream_index,
        audio_language=audio_language, audio_stream_index=audio_stream_index,
        whisper_jobs=whisper_jobs,
    )
    if plan["status"] != "succeeded":
        payload = step_payload(
            workflow_id=config.workflow_id, phase="preprocess", step="preprocess.inspect_video",
            status=plan["status"], error=plan["error"],
            next_action="select_source_video" if plan["status"] == "needs_review" else None,
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload

    video = Path(plan["source_video"])
    inspect = workstation.pipeline.register_video(
        video, workspace=root, episode_id=identifier, purposes=VIDEO_PURPOSES,
        default_for=VIDEO_PURPOSES, force=force,
    )
    inspect_step = pipeline_payload_step(
        root, workflow_id=config.workflow_id, phase="preprocess",
        step="preprocess.inspect_video", payload=inspect,
    )
    if inspect_step["status"] not in {"succeeded", "skipped"}:
        refresh_summary(root)
        return inspect_step
    video_artifact_id = inspect_step["outputs"][0]["artifact_id"]
    update_manifest(root, source={"video_artifact_id": video_artifact_id})

    tracks = workstation.pipeline.list_media_tracks(
        workspace=root, episode_id=identifier, video_artifact_id=video_artifact_id,
    )
    reference, reference_error = _select_reference_track(
        tracks.get("tracks", []), reference_language, reference_stream_index
    )
    if reference_error:
        payload = step_payload(
            workflow_id=config.workflow_id, phase="preprocess",
            step="preprocess.extract_reference_subtitle", status="needs_review",
            error=reference_error, next_action="select_reference_subtitle_track",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    reference_result = workstation.pipeline.extract_subtitle_track(
        workspace=root, episode_id=identifier, video_artifact_id=video_artifact_id,
        stream_index=reference["index"], output_dir=paths["reference"], force=force,
    )
    reference_step = pipeline_payload_step(
        root, workflow_id=config.workflow_id, phase="preprocess",
        step="preprocess.extract_reference_subtitle", payload=reference_result,
    )
    if reference_step["status"] not in {"succeeded", "skipped"}:
        refresh_summary(root)
        return reference_step
    reference_output = Path(reference_step["outputs"][0]["absolute_path"])
    top_level_reference = root / f"{video.stem}.en{reference_output.suffix.lower()}"
    _copy_reference(reference_output, top_level_reference)
    reference_artifact_id = reference_step["outputs"][0]["artifact_id"]
    update_manifest(root, preprocess={
        "reference_subtitle_artifact_id": reference_artifact_id,
        "reference_delivery_path": str(top_level_reference),
    })

    audio, audio_error = _select_audio_track(
        tracks.get("tracks", []), audio_language, audio_stream_index
    )
    if audio_error:
        payload = step_payload(
            workflow_id=config.workflow_id, phase="preprocess",
            step="preprocess.extract_audio", status="needs_review", error=audio_error,
            next_action="select_audio_track",
        )
        write_step(root, payload)
        refresh_summary(root)
        return payload
    audio_result = workstation.pipeline.extract_audio_track(
        workspace=root, episode_id=identifier, video_artifact_id=video_artifact_id,
        stream_index=audio["index"], mode="both", output_dir=paths["audio"], force=force,
    )
    audio_step = pipeline_payload_step(
        root, workflow_id=config.workflow_id, phase="preprocess",
        step="preprocess.extract_audio", payload=audio_result,
    )
    if audio_step["status"] not in {"succeeded", "skipped"}:
        refresh_summary(root)
        return audio_step
    archive_id = next(item["artifact_id"] for item in audio_step["outputs"]
                      if item["artifact_type"] == "generated.audio.archive")
    transcribe_id = next(item["artifact_id"] for item in audio_step["outputs"]
                         if item["artifact_type"] == "generated.audio.transcribe")
    update_manifest(root, preprocess={
        "archive_audio_artifact_id": archive_id,
        "transcribe_audio_artifact_id": transcribe_id,
    })

    final = audio_step
    transcript_ids = {}
    for job in whisper_jobs:
        result = workstation.pipeline.transcribe(
            workspace=root, episode_id=identifier, audio_artifact_id=transcribe_id,
            mode=job.mode, model=job.model, model_revision=job.model_revision,
            language=job.language, chunk_seconds=job.chunk_seconds,
            overlap_seconds=job.overlap_seconds, manual_cuts=job.manual_cuts,
            throttle_seconds=job.throttle_seconds, decoding=dict(job.decoding),
            output_dir=paths["transcripts"] / job.name, force=force,
        )
        final = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="preprocess",
            step=f"preprocess.transcribe.{job.name}", payload=result,
        )
        if final["status"] not in {"succeeded", "skipped"}:
            refresh_summary(root)
            return final
        transcript_ids[job.name] = [item["artifact_id"] for item in final["outputs"]]
    if transcript_ids:
        update_manifest(root, preprocess={"transcript_artifact_ids": transcript_ids})
    summary = refresh_summary(root)
    return {"status": summary["preprocess"]["status"], "plan": plan,
            "manifest": load_manifest(root), "summary": summary, "last_step": final}


def run_preprocess_step(step: str, episode_dir: Path | str, **kwargs) -> dict[str, Any]:
    result = run_preprocess(episode_dir, **kwargs)
    return result if step in {"all", "preprocess"} else __import__(
        "bmlsub.workstation.state", fromlist=["load_status"]
    ).load_status(episode_dir, step)


def _select_reference_track(tracks, language, stream_index):
    candidates = [item for item in tracks if item.get("kind") == "subtitle"]
    if stream_index is not None:
        selected = [item for item in candidates if item.get("index") == stream_index]
        return (selected[0], None) if len(selected) == 1 else (None, _track_error(candidates))
    candidates = [item for item in candidates if languages_match(str(item.get("language", "und")), language)]
    candidates = [item for item in candidates if not item.get("is_forced") and not _signs_only(item)]
    if len(candidates) == 1:
        return candidates[0], None
    return None, _track_error(candidates)


def _select_audio_track(tracks, language, stream_index):
    candidates = [item for item in tracks if item.get("kind") == "audio"]
    if stream_index is not None:
        selected = [item for item in candidates if item.get("index") == stream_index]
        return (selected[0], None) if len(selected) == 1 else (None, _track_error(candidates))
    language_matches = [item for item in candidates if languages_match(str(item.get("language", "und")), language)]
    if len(language_matches) == 1:
        return language_matches[0], None
    defaults = [item for item in language_matches if item.get("is_default")]
    if len(defaults) == 1:
        return defaults[0], None
    return None, _track_error(language_matches)


def _track_error(candidates):
    return {"code": "track_selection_ambiguous", "message": "media track selection requires review",
            "candidates": candidates}


def _signs_only(item):
    title = str(item.get("title") or "").lower()
    return any(marker in title for marker in ("sign", "song", "forced", "sdh"))


def _copy_reference(source: Path, target: Path) -> None:
    data = source.read_bytes()
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(data)
    if temporary.read_bytes() != data:
        temporary.unlink()
        raise IOError("reference subtitle copy verification failed")
    temporary.replace(target)
