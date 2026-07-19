"""Reliable MLX Whisper transcription of registered audio artifacts."""

from __future__ import annotations

from ..version import __version__

from dataclasses import dataclass, field
from enum import Enum
import importlib.metadata
import json
from pathlib import Path
import time
from typing import Any, Mapping, Protocol
import wave

from ..artifacts import ArtifactBatchWriter, ArtifactWriteSpec
from ..execution import BmlsubError, ErrorCode, ProcessRunner
from ..execution.stage_runner import StageContext, StageOutcome, StageRunner
from ..state.fingerprints import fingerprint_parameters, fingerprint_tools, hash_json
from ..state.models import Diagnostic, StageInputBinding, StageResult
from ..state.sqlite_store import SQLiteJobStore
from ..media.video import get_current_artifact


TRANSCRIPTION_STAGE = "transcription.whisper"
TRANSCRIPTION_SCHEMA_VERSION = "transcript-v1"
TRANSCRIPTION_PROFILE_VERSION = "whisper-profile-v1"
TRANSCRIPTION_NAMING_VERSION = "transcript-naming-v1"
TRANSCRIPTION_VALIDATOR_VERSION = "transcript-validator-v2"
CHUNK_PLAN_VERSION = "ffmpeg-chunk-plan-v1"


class TranscriptionMode(str, Enum):
    DIRECT = "direct"
    CHUNKED = "chunked"
    BOTH = "both"


class WhisperBackend(Protocol):
    def version(self) -> str: ...

    def transcribe(self, audio_path: Path, *, model: str, language: str,
                   decoding: Mapping[str, Any]) -> Mapping[str, Any]: ...


class MlxWhisperBackend:
    """Lazy optional dependency boundary for mlx-whisper."""

    def version(self) -> str:
        try:
            return importlib.metadata.version("mlx-whisper")
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def transcribe(self, audio_path: Path, *, model: str, language: str,
                   decoding: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            import mlx_whisper
        except ImportError as exc:
            raise BmlsubError(
                "mlx-whisper is required for transcription",
                code=ErrorCode.INPUT_MISSING,
                details={"dependency": "mlx-whisper"},
            ) from exc
        try:
            return mlx_whisper.transcribe(
                str(audio_path), path_or_hf_repo=model, language=language, **dict(decoding)
            )
        except Exception as exc:
            raise BmlsubError(
                "MLX Whisper transcription failed",
                code=ErrorCode.EXTERNAL_SERVICE_ERROR,
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc


@dataclass(frozen=True)
class TranscriptionOptions:
    mode: TranscriptionMode = TranscriptionMode.DIRECT
    model: str = "mlx-community/whisper-large-v3-turbo"
    model_revision: str = "main"
    language: str = "ja"
    chunk_seconds: float = 240.0
    overlap_seconds: float = 5.0
    manual_cuts: tuple[float, ...] = ()
    throttle_seconds: float = 0.0
    decoding: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.model_revision.strip() or not self.language.strip():
            raise ValueError("model, model revision, and language must not be empty")
        if self.chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be positive")
        if self.overlap_seconds < 0 or self.overlap_seconds >= self.chunk_seconds:
            raise ValueError("overlap_seconds must be non-negative and smaller than chunk_seconds")
        if self.throttle_seconds < 0:
            raise ValueError("throttle_seconds must be non-negative")
        cuts = tuple(float(item) for item in self.manual_cuts)
        if any(item <= 0 for item in cuts) or list(cuts) != sorted(set(cuts)):
            raise ValueError("manual cuts must be unique positive ascending seconds")
        object.__setattr__(self, "manual_cuts", cuts)
        object.__setattr__(self, "decoding", dict(self.decoding))


def parse_timestamp(value: str) -> float:
    """Parse SS, MM:SS, or HH:MM:SS into seconds."""
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"invalid timestamp: {value}")
    try:
        numbers = [float(item) for item in parts]
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if any(item < 0 for item in numbers) or any(item >= 60 for item in numbers[1:]):
        raise ValueError(f"invalid timestamp: {value}")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def run_transcription(*, workspace: Path | str, episode_id: str,
                      audio_artifact_id: str, options: TranscriptionOptions | None = None,
                      output_dir: Path | str | None = None,
                      ffmpeg: Path | str = "ffmpeg", process_timeout: float = 600.0,
                      backend: WhisperBackend | None = None,
                      runner: ProcessRunner | None = None,
                      store: SQLiteJobStore | None = None,
                      state_dir: Path | str | None = None,
                      force: bool = False) -> StageResult:
    root = Path(workspace).expanduser().resolve()
    ledger = store or SQLiteJobStore.for_workspace(root, state_dir)
    ledger.initialize()
    audio = get_current_artifact(ledger, audio_artifact_id)
    if (audio is None or audio.episode_id != episode_id
            or audio.artifact_type != "generated.audio.transcribe"):
        raise BmlsubError(
            "transcription requires a current generated.audio.transcribe artifact",
            code=ErrorCode.INPUT_MISSING,
            details={"artifact_id": audio_artifact_id},
        )
    config = options or TranscriptionOptions()
    whisper = backend or MlxWhisperBackend()
    if isinstance(whisper, MlxWhisperBackend):
        resolved_model, model_integrity = _resolve_model(config.model, config.model_revision)
    else:
        resolved_model = config.model
        model_integrity = {"kind": "injected", "identifier": config.model,
                           "revision": config.model_revision}
    duration_seconds = _audio_duration_seconds(audio)
    if config.manual_cuts and config.manual_cuts[-1] >= duration_seconds:
        raise ValueError("manual cuts must fall within the audio duration")
    modes = ((TranscriptionMode.DIRECT, TranscriptionMode.CHUNKED)
             if config.mode is TranscriptionMode.BOTH else (config.mode,))
    directory = _output_directory(root, episode_id, output_dir)
    targets = tuple(_output_path(directory, episode_id, mode, config.model) for mode in modes)
    chunk_plan = (_chunk_plan(duration_seconds, config)
                  if TranscriptionMode.CHUNKED in modes else ())
    chunk_directory = _chunk_output_directory(root, config.model)
    chunk_targets = tuple(
        chunk_directory / f"chunk-{index:04d}-{_format_seconds(start)}-{_format_seconds(end)}.wav"
        for index, (start, end) in enumerate(chunk_plan)
    )
    process = runner or ProcessRunner(timeout=process_timeout)
    ffmpeg_version = process.version(ffmpeg) if TranscriptionMode.CHUNKED in modes else "unused"
    input_fp = hash_json({
        "artifact_id": audio.artifact_id,
        "content_hash": audio.content_hash,
        "source_fingerprint": audio.source_fingerprint,
        "size": audio.size,
        "mtime_ns": audio.mtime_ns,
    })
    parameter_fp = fingerprint_parameters({
        "mode": config.mode.value,
        "model": config.model,
        "model_revision": config.model_revision,
        "model_integrity": model_integrity,
        "language": config.language,
        "chunk_seconds": config.chunk_seconds,
        "overlap_seconds": config.overlap_seconds,
        "manual_cuts": list(config.manual_cuts),
        "throttle_seconds": config.throttle_seconds,
        "decoding": dict(config.decoding),
        "targets": [str(path.relative_to(root)) for path in targets],
        "chunk_targets": [str(path.relative_to(root)) for path in chunk_targets],
        "schema_version": TRANSCRIPTION_SCHEMA_VERSION,
        "profile_version": TRANSCRIPTION_PROFILE_VERSION,
        "naming_version": TRANSCRIPTION_NAMING_VERSION,
        "chunk_plan_version": CHUNK_PLAN_VERSION,
    })
    tool_fp = fingerprint_tools({
        "bmlsub": __version__,
        "mlx_whisper": whisper.version(),
        "ffmpeg": ffmpeg_version,
        "validator": TRANSCRIPTION_VALIDATOR_VERSION,
    })

    def adapter(context: StageContext) -> StageOutcome:
        validation: dict[str, dict[str, Any]] = {}
        specs = []
        for mode, target in zip(modes, targets):
            def validator(path: Path, selected=mode) -> None:
                validation[selected.value] = validate_transcript_output(
                    path, expected_mode=selected, expected_language=config.language,
                    expected_model=config.model,
                )
            specs.append(ArtifactWriteSpec(
                target=target,
                artifact_type=f"generated.transcript.{mode.value}",
                validator=validator,
                metadata={
                    "source_audio_artifact_id": audio.artifact_id,
                    "mode": mode.value,
                    "model": config.model,
                    "model_revision": config.model_revision,
                    "language": config.language,
                    "schema_version": TRANSCRIPTION_SCHEMA_VERSION,
                    "profile_version": TRANSCRIPTION_PROFILE_VERSION,
                    "validator_version": TRANSCRIPTION_VALIDATOR_VERSION,
                },
            ))
        for index, (target, (start, end)) in enumerate(zip(chunk_targets, chunk_plan)):
            specs.append(ArtifactWriteSpec(
                target=target, artifact_type="generated.audio.transcription_chunk",
                validator=_validate_chunk_audio,
                metadata={
                    "source_audio_artifact_id": audio.artifact_id,
                    "mode": "chunked", "model": config.model, "chunk_index": index,
                    "start": start, "end": end, "chunk_seconds": config.chunk_seconds,
                    "overlap_seconds": config.overlap_seconds,
                    "chunk_plan_version": CHUNK_PLAN_VERSION,
                    "channels": 1, "sample_rate": 16000, "codec": "pcm_s16le",
                },
            ))
        writer = ArtifactBatchWriter(
            workspace=root, run_id=context.run_id, stage_id=context.stage_id,
            episode_id=episode_id, source_fingerprint=input_fp,
            parameter_fingerprint=parameter_fp,
        )

        def produce(paths: tuple[Path, ...]) -> None:
            transcript_paths = paths[:len(modes)]
            persisted_chunks = paths[len(modes):]
            for mode, candidate in zip(modes, transcript_paths):
                payload = (_transcribe_direct(audio.path, whisper, config, resolved_model)
                           if mode is TranscriptionMode.DIRECT else
                           _transcribe_chunked(
                               audio.path, whisper, config, model_path=resolved_model,
                               process=process, ffmpeg=ffmpeg,
                               process_timeout=process_timeout,
                               chunks=chunk_plan, chunk_paths=persisted_chunks,
                           ))
                candidate.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

        results = writer.write(tuple(specs), produce)
        artifacts = []
        diagnostics = []
        for mode, result in zip(modes, results):
            metadata = dict(result.artifact.metadata)
            metadata["validation"] = validation[mode.value]
            from dataclasses import replace
            artifacts.append(replace(result.artifact, metadata=metadata))
            if result.backup_path:
                diagnostics.append(Diagnostic(
                    code="artifact_backup_created",
                    message="existing transcript output was backed up",
                    context={"path": str(result.backup_path)},
                ))
        for result in results[len(modes):]:
            artifacts.append(result.artifact)
            if result.backup_path:
                diagnostics.append(Diagnostic(
                    code="artifact_backup_created",
                    message="existing transcription chunk was backed up",
                    context={"path": str(result.backup_path)},
                ))
        diagnostics.append(Diagnostic(
            code="transcription_completed",
            message="requested transcription modes completed",
            context={"modes": [item.value for item in modes], "model": config.model},
        ))
        return StageOutcome(artifacts=tuple(artifacts), diagnostics=tuple(diagnostics))

    return StageRunner(ledger).run(
        workspace=root, command_name="transcribe", stage_name=TRANSCRIPTION_STAGE,
        episode_id=episode_id, input_fingerprint=input_fp,
        parameter_fingerprint=parameter_fp, tool_fingerprint=tool_fp,
        adapter=adapter,
        inputs=(StageInputBinding(audio.artifact_id, "audio", 0),),
        force=force,
    )


def validate_transcript_output(path: Path, *, expected_mode: TranscriptionMode,
                               expected_language: str, expected_model: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("transcript is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != TRANSCRIPTION_SCHEMA_VERSION:
        raise ValueError("transcript schema version is invalid")
    if payload.get("mode") != expected_mode.value:
        raise ValueError("transcript mode does not match the request")
    if payload.get("language") != expected_language or payload.get("model") != expected_model:
        raise ValueError("transcript model or language does not match the request")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError("transcript segments must be a list")
    previous_start = -1.0
    for item in segments:
        if not isinstance(item, dict):
            raise ValueError("transcript segment must be an object")
        start, end, text = item.get("start"), item.get("end"), item.get("text")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError("transcript segment timestamps must be numeric")
        if start < 0 or end < start or start < previous_start or not isinstance(text, str):
            raise ValueError("transcript segment structure is invalid")
        previous_start = float(start)
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("transcript text must be a string")
    return {"segment_count": len(segments), "text_characters": len(text)}


def _transcribe_direct(audio_path: Path, backend: WhisperBackend,
                       options: TranscriptionOptions, model_path: str) -> dict[str, Any]:
    result = backend.transcribe(
        audio_path, model=model_path, language=options.language,
        decoding=options.decoding,
    )
    segments = _normalize_segments(result.get("segments", ()), offset=0.0)
    return _transcript_payload(options, TranscriptionMode.DIRECT, segments)


def _transcribe_chunked(audio_path: Path, backend: WhisperBackend,
                        options: TranscriptionOptions, *, model_path: str,
                        process: ProcessRunner, ffmpeg: Path | str,
                        process_timeout: float,
                        chunks: tuple[tuple[float, float], ...],
                        chunk_paths: tuple[Path, ...]) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    if len(chunks) != len(chunk_paths):
        raise ValueError("chunk plan and output paths do not match")
    for index, ((start, end), chunk_path) in enumerate(zip(chunks, chunk_paths)):
        process.run([
            str(ffmpeg), "-nostdin", "-y", "-v", "error",
            "-ss", _format_seconds(start), "-t", _format_seconds(end - start),
            "-i", str(audio_path), "-vn", "-sn", "-dn",
            "-c:a", "pcm_s16le", "-ac", "1", "-ar", "16000",
            "-f", "wav", str(chunk_path),
        ], timeout=process_timeout)
        result = backend.transcribe(
            chunk_path, model=model_path, language=options.language,
            decoding=options.decoding,
        )
        segments.extend(_normalize_segments(
            result.get("segments", ()), offset=start, chunk_index=index,
            clip_start=start, clip_end=end,
        ))
        if options.throttle_seconds and index + 1 < len(chunks):
            time.sleep(options.throttle_seconds)
    segments.sort(key=lambda item: (item["start"], item["end"], item.get("chunk_index", 0)))
    return _transcript_payload(options, TranscriptionMode.CHUNKED, segments)


def _chunk_plan(duration_seconds: float,
                options: TranscriptionOptions) -> tuple[tuple[float, float], ...]:
    boundaries = (0.0, *options.manual_cuts, duration_seconds)
    chunks: list[tuple[float, float]] = []
    step = options.chunk_seconds - options.overlap_seconds
    for part_start, part_end in zip(boundaries[:-1], boundaries[1:]):
        start = part_start
        while start < part_end:
            end = min(start + options.chunk_seconds, part_end)
            chunks.append((start, end))
            if end >= part_end:
                break
            start += step
    return tuple(chunks)


def _normalize_segments(raw_segments: Any, *, offset: float,
                        chunk_index: int | None = None,
                        clip_start: float | None = None,
                        clip_end: float | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, (list, tuple)):
        raise ValueError("Whisper result segments must be a list")
    normalized = []
    for item in raw_segments:
        if not isinstance(item, Mapping):
            raise ValueError("Whisper segment must be an object")
        try:
            raw_start = float(item["start"]) + offset
            raw_end = float(item["end"]) + offset
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Whisper segment timestamps are invalid") from exc
        if not (raw_start == raw_start and raw_end == raw_end):
            raise ValueError("Whisper segment timestamps are invalid")
        start = max(0.0, raw_start)
        end = max(start, raw_end)
        if clip_start is not None:
            start = max(start, clip_start)
        if clip_end is not None:
            end = min(end, clip_end)
        text = str(item.get("text", "")).strip()
        segment: dict[str, Any] = {"start": round(start, 3), "end": round(end, 3), "text": text}
        if normalized and segment["start"] < normalized[-1]["start"]:
            segment["start"] = normalized[-1]["start"]
        if segment["end"] < segment["start"]:
            segment["end"] = segment["start"]
        if chunk_index is not None:
            segment["chunk_index"] = chunk_index
        normalized.append(segment)
    return normalized


def _transcript_payload(options: TranscriptionOptions, mode: TranscriptionMode,
                        segments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": TRANSCRIPTION_SCHEMA_VERSION,
        "mode": mode.value,
        "model": options.model,
        "model_revision": options.model_revision,
        "language": options.language,
        "segments": segments,
        "text": "\n".join(item["text"] for item in segments if item["text"]),
    }


def _audio_duration_seconds(artifact) -> float:
    media = artifact.metadata.get("media")
    if isinstance(media, Mapping):
        duration_ms = media.get("duration_ms")
        if isinstance(duration_ms, (int, float)) and duration_ms > 0:
            return float(duration_ms) / 1000.0
    try:
        with wave.open(str(artifact.path), "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()
    except (OSError, wave.Error, ZeroDivisionError) as exc:
        raise BmlsubError(
            "transcription audio duration is unavailable",
            code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            details={"artifact_id": artifact.artifact_id},
        ) from exc
    if duration <= 0:
        raise BmlsubError(
            "transcription audio is empty", code=ErrorCode.OUTPUT_VALIDATION_FAILED
        )
    return duration


def _resolve_model(model: str, revision: str) -> tuple[str, dict[str, Any]]:
    path = Path(model).expanduser()
    if path.exists():
        resolved = path.resolve()
        return str(resolved), _local_model_integrity(resolved)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise BmlsubError(
            "huggingface-hub is required to resolve a model repository",
            code=ErrorCode.INPUT_MISSING,
            details={"dependency": "huggingface-hub"},
        ) from exc
    try:
        snapshot = Path(snapshot_download(repo_id=model, revision=revision)).resolve()
    except Exception as exc:
        raise BmlsubError(
            "Whisper model revision could not be resolved",
            code=ErrorCode.EXTERNAL_SERVICE_ERROR,
            retryable=True,
            details={"model": model, "revision": revision,
                     "exception_type": type(exc).__name__},
        ) from exc
    integrity = _local_model_integrity(snapshot)
    integrity.update({"kind": "repository", "identifier": model, "revision": revision})
    return str(snapshot), integrity


def _local_model_integrity(resolved: Path) -> dict[str, Any]:
    if resolved.is_file():
        stat = resolved.stat()
        return {"kind": "file", "path": str(resolved), "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns}
    entries = []
    for item in sorted(candidate for candidate in resolved.rglob("*") if candidate.is_file()):
        stat = item.stat()
        entries.append({"path": str(item.relative_to(resolved)), "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns})
    return {"kind": "directory", "path": str(resolved), "files": entries}


def _output_directory(workspace: Path, episode_id: str,
                      output_dir: Path | str | None) -> Path:
    directory = (Path(output_dir).expanduser().resolve() if output_dir is not None
                 else workspace / "outputs" / episode_id / "transcripts")
    try:
        directory.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("transcription output directory must be inside workspace") from exc
    return directory


def _output_path(directory: Path, episode_id: str, mode: TranscriptionMode,
                 model: str) -> Path:
    model_name = model.rstrip("/").split("/")[-1]
    safe_model = "".join(char if char.isalnum() or char in "._-" else "-" for char in model_name)
    return directory / f"{episode_id}.{mode.value}.{safe_model}.transcript.json"


def _chunk_output_directory(workspace: Path, model: str) -> Path:
    model_name = model.rstrip("/").split("/")[-1]
    safe_model = "".join(char if char.isalnum() or char in "._-" else "-" for char in model_name)
    return workspace / "workstation" / "preprocess" / "audio" / "chunks" / f"chunked-{safe_model}"


def _validate_chunk_audio(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getframerate() != 16000 or handle.getsampwidth() != 2:
                raise ValueError("chunk WAV must be mono 16 kHz signed 16-bit PCM")
            if handle.getnframes() <= 0:
                raise ValueError("chunk WAV is empty")
    except (OSError, wave.Error) as exc:
        raise ValueError("chunk WAV is unreadable") from exc


def _format_seconds(value: float) -> str:
    return f"{value:.3f}"
