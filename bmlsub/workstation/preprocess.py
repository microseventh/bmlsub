"""Workstation preprocessing planning and execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts import ArtifactBatchWriter, ArtifactWriteSpec
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..media.tracks import languages_match
from ..interactive import ui_text
from ..progress import finish_progress_task, progress_task
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding
from ..transcription import run_transcript_text_export
from .common import discover_source_video, ensure_directories, open_workstation
from .models import (
    PreprocessConfig, ReferenceTrackSelection, TrackSelection, WorkstationConfig,
)
from .series import discover_series_context
from .state import (
    atomic_write_json, load_manifest, pipeline_payload_step, refresh_summary,
    update_manifest, write_phase_plan, write_step, step_payload,
)


VIDEO_PURPOSES = (
    "source", "extract", "transcribe_source", "encode_source", "hardsub_source",
    "package_source",
)
REFERENCE_PUBLISH_VERSION = "reference-publish-v2"
_SUPPORTED_REFERENCE_CODECS = {"ass", "ssa", "subrip", "srt"}


def plan_preprocess(episode_dir: Path | str, *, episode_id: str | None = None,
                    source_video: Path | str | None = None,
                    reference_language: str = "eng",
                    reference_stream_index: int | None = None,
                    reference_stream_indices=(),
                    reference_policy: str = "all_matching",
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
    explicit_indices = _normalize_reference_indices(reference_stream_index, reference_stream_indices)
    selected_policy = "explicit" if explicit_indices else reference_policy
    steps = [
        "preprocess.inspect_video", "preprocess.extract_reference_subtitles",
        "preprocess.publish_reference_subtitles", "preprocess.extract_audio",
    ]
    for item in whisper_jobs:
        steps.extend((f"preprocess.transcribe.{item.name}",
                      f"preprocess.export_transcript_text.{item.name}"))
    return {
        "schema_version": "workstation-plan-v1", "workflow_id": f"episode-{identifier}",
        "phase": "preprocess", "status": status, "episode_dir": str(root),
        "episode_id": identifier, "source_video": str(video) if video else None,
        "selection": {
            "reference_language": reference_language,
            "reference_policy": selected_policy,
            "reference_stream_indices": list(explicit_indices),
            "audio_language": audio_language,
            "audio_stream_index": audio_stream_index,
        },
        "whisper_jobs": [item.to_dict() for item in whisper_jobs],
        "transcription_policy": _transcription_policy(whisper_jobs),
        "steps": steps,
        "error": error,
    }


def run_preprocess(episode_dir: Path | str, *, episode_id: str | None = None,
                   source_video: Path | str | None = None,
                   reference_language: str = "eng",
                   reference_stream_index: int | None = None,
                   reference_stream_indices=(),
                   reference_policy: str = "all_matching",
                   audio_language: str = "jpn",
                   audio_stream_index: int | None = None,
                   whisper_jobs=(), force: bool = False) -> dict[str, Any]:
    root = Path(episode_dir).expanduser().resolve()
    context = discover_series_context(root)
    identifier = episode_id or context.episode_id
    if identifier != context.episode_id:
        raise ValueError("episode_id does not match numeric episode directory")
    explicit_indices = _normalize_reference_indices(reference_stream_index, reference_stream_indices)
    selected_policy = "explicit" if explicit_indices else reference_policy
    reference_config = ReferenceTrackSelection(
        policy=selected_policy, language=reference_language,
        stream_indices=explicit_indices,
    )
    transcription_policy = _transcription_policy(whisper_jobs)
    config = WorkstationConfig.from_series_context(
        context,
        preprocess=PreprocessConfig(
            source_video=source_video, reference_tracks=reference_config,
            audio_track=TrackSelection(stream_index=audio_stream_index,
                                       language=audio_language),
            whisper_jobs=tuple(whisper_jobs),
            transcription_policy=transcription_policy,
        ),
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
        reference_language=reference_language,
        reference_stream_index=reference_stream_index,
        reference_stream_indices=reference_stream_indices,
        reference_policy=reference_policy,
        audio_language=audio_language, audio_stream_index=audio_stream_index,
        whisper_jobs=whisper_jobs,
    )
    write_phase_plan(
        root, phase="preprocess", policy=transcription_policy,
        expected_steps=plan["steps"], workflow_id=config.workflow_id,
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
    references, primary_index, reference_error, reference_diagnostics = _select_reference_tracks(
        tracks.get("tracks", []), reference_language, explicit_indices, selected_policy,
    )
    reference_step = None
    if reference_error:
        reference_step = step_payload(
            workflow_id=config.workflow_id, phase="preprocess",
            step="preprocess.extract_reference_subtitles", status="needs_review",
            diagnostics=reference_diagnostics, error=reference_error,
            next_action="select_reference_subtitle_tracks",
        )
        write_step(root, reference_step)
    else:
        resolved_reference = ReferenceTrackSelection(
            policy=selected_policy, language=reference_language,
            stream_indices=explicit_indices,
            resolved_stream_indices=tuple(item["index"] for item in references),
            primary_stream_index=primary_index,
        )
        config = WorkstationConfig.from_series_context(
            context,
            preprocess=PreprocessConfig(
                source_video=source_video, reference_tracks=resolved_reference,
                audio_track=TrackSelection(stream_index=audio_stream_index,
                                           language=audio_language),
                whisper_jobs=tuple(whisper_jobs),
                transcription_policy=transcription_policy,
            ),
        )
        atomic_write_json(paths["state"] / "config.json", config.to_dict())
        reference_result = workstation.pipeline.extract_subtitle_tracks(
            workspace=root, episode_id=identifier,
            video_artifact_id=video_artifact_id,
            stream_indices=tuple(item["index"] for item in references),
            output_dir=paths["reference"], force=force,
        )
        reference_step = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="preprocess",
            step="preprocess.extract_reference_subtitles", payload=reference_result,
        )
        if reference_step["status"] in {"succeeded", "skipped"}:
            published = _publish_reference_subtitles(
                workstation=workstation, root=root, video=video,
                reference_outputs=reference_step["outputs"],
                primary_stream_index=primary_index, force=force,
            )
            publish_step = pipeline_payload_step(
                root, workflow_id=config.workflow_id, phase="preprocess",
                step="preprocess.publish_reference_subtitles", payload=published,
            )
            reference_step = publish_step if publish_step["status"] not in {"succeeded", "skipped"} else reference_step
            if publish_step["status"] in {"succeeded", "skipped"}:
                delivery_by_source = {}
                primary_delivery = None
                for item in publish_step["outputs"]:
                    metadata = item.get("metadata", {})
                    source_id = metadata.get("source_subtitle_artifact_id")
                    if metadata.get("primary_alias"):
                        primary_delivery = item
                    elif source_id:
                        delivery_by_source[source_id] = item
                records = []
                for candidate, output in zip(references, reference_step["outputs"]):
                    delivery = delivery_by_source.get(output["artifact_id"])
                    records.append({
                        "stream_index": candidate["index"],
                        "language": candidate.get("language", reference_language),
                        "title": candidate.get("title"),
                        "is_default": bool(candidate.get("is_default")),
                        "is_forced": bool(candidate.get("is_forced")),
                        "artifact_id": output["artifact_id"],
                        "artifact_path": output["absolute_path"],
                        "delivery_artifact_id": delivery["artifact_id"] if delivery else None,
                        "delivery_path": delivery["absolute_path"] if delivery else None,
                    })
                update_manifest(root, preprocess={
                    "reference_selection": {
                        "policy": selected_policy,
                        "requested_language": reference_language,
                        "resolved_stream_indices": [item["index"] for item in references],
                        "primary_stream_index": primary_index,
                        "complete": True,
                    },
                    "reference_subtitles": records,
                    "primary_reference_artifact_id": (
                        primary_delivery["artifact_id"] if primary_delivery else None
                    ),
                    "primary_reference_delivery_path": (
                        primary_delivery["absolute_path"] if primary_delivery else None
                    ),
                    "reference_subtitle_artifact_id": next(
                        item["artifact_id"] for item in reference_step["outputs"]
                        if item.get("metadata", {}).get("source_stream_index") == primary_index
                    ),
                    "reference_delivery_path": (
                        primary_delivery["absolute_path"] if primary_delivery else None
                    ),
                })

    audio, audio_error = _select_audio_track(
        tracks.get("tracks", []), audio_language, audio_stream_index
    )
    if audio_error:
        audio_step = step_payload(
            workflow_id=config.workflow_id, phase="preprocess",
            step="preprocess.extract_audio", status="needs_review", error=audio_error,
            next_action="select_audio_track",
        )
        write_step(root, audio_step)
        summary = refresh_summary(root)
        return {
            "status": summary["preprocess"]["status"], "plan": plan,
            "manifest": load_manifest(root), "summary": summary,
            "last_step": audio_step, "reference_step": reference_step,
        }
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
    transcript_text_ids = {}
    chunk_ids = {}
    export_step = None
    for job in whisper_jobs:
        with progress_task(
            phase="preprocess", step=f"preprocess.transcribe.{job.name}",
            label=ui_text("MLX Whisper 转录", "MLX Whisper transcription"),
            detail=f"{job.name} ({job.mode})",
        ) as task:
            def update_progress(event: dict[str, Any]) -> None:
                task.update(
                    current=event.get("current"), total=event.get("total"),
                    unit=ui_text("段", "chunks"),
                    detail=f"{job.name} ({event.get('mode') or job.mode})",
                )

            result = workstation.pipeline.transcribe(
                workspace=root, episode_id=identifier, audio_artifact_id=transcribe_id,
                mode=job.mode, model=job.model, model_revision=job.model_revision,
                language=job.language, chunk_seconds=job.chunk_seconds,
                overlap_seconds=job.overlap_seconds, manual_cuts=job.manual_cuts,
                throttle_seconds=job.throttle_seconds, decoding=dict(job.decoding),
                output_dir=paths["transcripts"] / job.name,
                progress_callback=update_progress, force=force,
            )
            finish_progress_task(task, result)
        final = pipeline_payload_step(
            root, workflow_id=config.workflow_id, phase="preprocess",
            step=f"preprocess.transcribe.{job.name}", payload=result,
        )
        if final["status"] not in {"succeeded", "skipped"}:
            if final.get("retryable"):
                final["next_action"] = "resume_preprocess"
                write_step(root, final)
            summary = refresh_summary(root)
            return {
                "status": summary["preprocess"]["status"], "plan": plan,
                "manifest": load_manifest(root), "summary": summary,
                "last_step": final, "reference_step": reference_step,
            }
        transcript_outputs = [item for item in final["outputs"]
                              if item["artifact_type"].startswith("generated.transcript.")
                              and not item["artifact_type"].startswith("generated.transcript.text.")]
        transcript_ids[job.name] = [item["artifact_id"] for item in transcript_outputs]
        chunks = [item["artifact_id"] for item in final["outputs"]
                  if item["artifact_type"] == "generated.audio.transcription_chunk"]
        if chunks:
            chunk_ids[job.name] = chunks
        for output in transcript_outputs:
            mode = output["metadata"]["mode"]
            exported = run_transcript_text_export(
                workspace=root, episode_id=identifier,
                transcript_artifact_id=output["artifact_id"], mode=mode,
                model=output["metadata"]["model"], job_name=job.name,
                model_revision=output["metadata"].get("model_revision", job.model_revision),
                language=output["metadata"].get("language", job.language),
                store=workstation.store,
                state_dir=config.state_dir, force=force,
            )
            export_step = pipeline_payload_step(
                root, workflow_id=config.workflow_id, phase="preprocess",
                step=f"preprocess.export_transcript_text.{job.name}", payload=exported,
            )
            if export_step["status"] not in {"succeeded", "skipped"}:
                if export_step.get("retryable"):
                    export_step["next_action"] = "resume_preprocess"
                    write_step(root, export_step)
                summary = refresh_summary(root)
                return {
                    "status": summary["preprocess"]["status"], "plan": plan,
                    "manifest": load_manifest(root), "summary": summary,
                    "last_step": export_step, "reference_step": reference_step,
                }
            transcript_text_ids[job.name] = export_step["outputs"][0]["artifact_id"]
            final = export_step
        update_manifest(root, preprocess={
            "transcript_artifact_ids": {job.name: transcript_ids[job.name]},
            "transcript_text_artifact_ids": {
                job.name: transcript_text_ids.get(job.name)
            },
            "transcript_chunk_artifact_ids": {
                job.name: chunk_ids.get(job.name, [])
            },
        })
    if transcript_ids:
        update_manifest(root, preprocess={
            "transcript_artifact_ids": transcript_ids,
            "transcript_text_artifact_ids": transcript_text_ids,
            "transcript_chunk_artifact_ids": chunk_ids,
        })
    summary = refresh_summary(root)
    status = summary["preprocess"]["status"]
    if status == "succeeded" and reference_step and reference_step["status"] not in {"succeeded", "skipped"}:
        status = "needs_review"
    return {"status": status, "plan": plan,
            "manifest": load_manifest(root), "summary": summary, "last_step": final}


def run_preprocess_step(step: str, episode_dir: Path | str, **kwargs) -> dict[str, Any]:
    result = run_preprocess(episode_dir, **kwargs)
    return result if step in {"all", "preprocess"} else __import__(
        "bmlsub.workstation.state", fromlist=["load_status"]
    ).load_status(episode_dir, step)


def _normalize_reference_indices(stream_index, stream_indices) -> tuple[int, ...]:
    values = []
    if stream_index is not None:
        values.append(int(stream_index))
    values.extend(int(item) for item in (stream_indices or ()))
    normalized = tuple(dict.fromkeys(values))
    if any(item < 0 for item in normalized):
        raise ValueError("reference stream indices must be non-negative")
    return normalized


def _transcription_policy(whisper_jobs) -> str:
    jobs = tuple(whisper_jobs)
    signature = [(item.name, item.mode) for item in jobs]
    if not jobs:
        return "none"
    if signature == [("direct", "direct")]:
        return "quick"
    if signature == [("direct", "direct"), ("chunked", "chunked")]:
        return "full"
    return "custom"


def _select_reference_tracks(tracks, language, stream_indices, policy):
    subtitles = sorted(
        (item for item in tracks if item.get("kind") == "subtitle"),
        key=lambda item: int(item.get("index", -1)),
    )
    if policy == "explicit":
        by_index = {item.get("index"): item for item in subtitles}
        selected = [by_index[item] for item in stream_indices if item in by_index]
        if len(selected) != len(stream_indices):
            return [], None, {
                "code": "reference_tracks_not_found",
                "message": "one or more requested reference subtitle tracks are unavailable",
                "requested_stream_indices": list(stream_indices),
                "candidates": subtitles,
            }, []
    else:
        selected = [item for item in subtitles
                    if languages_match(str(item.get("language", "und")), language)]
    supported = [item for item in selected
                 if str(item.get("codec_name") or "").lower() in _SUPPORTED_REFERENCE_CODECS]
    unsupported = [item for item in selected if item not in supported]
    diagnostics = ([{
        "code": "reference_subtitle_codec_unsupported",
        "message": "matching subtitle tracks with unsupported codecs were not extracted",
        "context": {"candidates": unsupported},
    }] if unsupported else [])
    if not supported:
        return [], None, {
            "code": "reference_tracks_not_found",
            "message": "no extractable reference subtitle tracks match the request",
            "candidates": selected,
        }, diagnostics
    if policy == "unique" and len(supported) != 1:
        return [], None, _track_error(supported), diagnostics
    defaults = [item for item in supported if item.get("is_default")]
    primary = defaults[0]["index"] if len(defaults) == 1 else supported[0]["index"]
    return supported, primary, None, diagnostics


def _publish_reference_subtitles(*, workstation, root: Path, video: Path,
                                 reference_outputs, primary_stream_index: int,
                                 force: bool) -> dict[str, Any]:
    source_artifacts = []
    for output in reference_outputs:
        artifact = workstation.store.get_artifact(output["artifact_id"])
        if artifact is None:
            raise ValueError("reference subtitle artifact is missing")
        source_artifacts.append(artifact)
    plan = []
    for artifact in source_artifacts:
        index = int(artifact.metadata["source_stream_index"])
        suffix = artifact.path.suffix.lower()
        plan.append((artifact, root / f"{video.stem}.en.s{index}{suffix}", False))
        if index == primary_stream_index:
            plan.append((artifact, root / f"{video.stem}.en{suffix}", True))
    input_fp = hash_json([
        {"artifact_id": item.artifact_id, "content_hash": item.content_hash}
        for item in source_artifacts
    ])
    parameter_fp = fingerprint_parameters({
        "targets": [str(target.relative_to(root)) for _source, target, _alias in plan],
        "primary_stream_index": primary_stream_index,
        "version": REFERENCE_PUBLISH_VERSION,
    })
    tool_fp = fingerprint_tools({"copy": REFERENCE_PUBLISH_VERSION})

    def adapter(context: StageContext) -> StageOutcome:
        specs = []
        for source, target, primary_alias in plan:
            specs.append(ArtifactWriteSpec(
                target=target, artifact_type="workstation.reference_subtitle.delivery",
                validator=lambda path, expected=source.content_hash: _validate_reference_copy(
                    path, expected
                ),
                metadata={
                    "source_subtitle_artifact_id": source.artifact_id,
                    "source_stream_index": source.metadata.get("source_stream_index"),
                    "language": source.metadata.get("language"),
                    "primary_alias": primary_alias,
                    "publish_version": REFERENCE_PUBLISH_VERSION,
                },
            ))
        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=workstation.config.episode_id,
            source_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        )
        results = writer.write(
            tuple(specs),
            lambda paths: [path.write_bytes(source.path.read_bytes())
                           for (source, _target, _alias), path in zip(plan, paths)],
        )
        diagnostics = (Diagnostic(
            code="reference_subtitles_published",
            message="reference subtitle working copies were published",
            context={"track_count": len(source_artifacts),
                     "primary_stream_index": primary_stream_index},
        ),)
        return StageOutcome(
            artifacts=tuple(item.artifact for item in results),
            diagnostics=diagnostics,
        )

    result = StageRunner(workstation.store).run(
        workspace=root, command_name="workstation.publish-reference-subtitles",
        stage_name="workstation.publish_reference_subtitles",
        episode_id=workstation.config.episode_id,
        input_fingerprint=input_fp, parameter_fingerprint=parameter_fp,
        tool_fingerprint=tool_fp, adapter=adapter,
        inputs=tuple(StageInputBinding(item.artifact_id, "subtitle", index)
                     for index, item in enumerate(source_artifacts)),
        force=force,
    )
    return result.to_dict()


def _validate_reference_copy(path: Path, expected_hash: str | None) -> None:
    if not path.is_file():
        raise ValueError("reference subtitle copy is missing")
    if expected_hash:
        from ..state.fingerprints import sha256_file
        if sha256_file(path) != expected_hash:
            raise ValueError("reference subtitle copy hash does not match source")


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
