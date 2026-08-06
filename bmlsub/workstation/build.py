"""Standalone build context, immutable plans, receipts, and local operations."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import sqlite3
import shlex
from typing import Any, Mapping
from uuid import uuid4
import wave

from ..execution.process_runner import ProcessRunner
from ..media.probe import FFprobeClient
from .operations import operation_spec
from .questions import associated_files, discover_inputs, VIDEO_SUFFIXES
from .rebuild import backup_target, rebuild_refusal, restore_backup
from .state import atomic_write_json


BUILD_SCHEMA_VERSION = "build-manifest-v1"
PLAN_SCHEMA_VERSION = "build-plan-v1"
RECEIPT_SCHEMA_VERSION = "build-receipt-v1"
TEXT_SUBTITLE_CODECS = frozenset({"ass", "ssa", "subrip", "srt", "webvtt", "vtt"})
DOWNSTREAM_OPERATIONS = {
    "bgminfo": ("encode", "torrent", "anibt"),
    "pubinfo": ("upr2", "dlvps", "seed", "anibt"),
    "encode": ("torrent", "upr2", "dlvps", "seed", "anibt"),
    "torrent": ("upr2", "dlvps", "seed", "anibt"),
    "upr2": ("dlvps", "seed"),
    "dlvps": ("seed",),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path, *, include_hash: bool = False) -> dict[str, object]:
    stat = path.stat()
    payload: dict[str, object] = {
        "path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
    }
    if include_hash:
        payload["sha256"] = _sha256(path)
    return payload


def _sanitized(value: Any, key: str = "") -> Any:
    lowered = key.casefold()
    if any(term in lowered for term in ("secret", "password", "token", "private_key")):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item): _sanitized(content, str(item)) for item, content in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitized(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


class BuildContext:
    """Persistent state that does not require a Workstation series layout."""

    def __init__(self, directory: Path | str = ".") -> None:
        self.cwd = Path(directory).expanduser().resolve()
        self.root = self.cwd / ".bmlsub" / "build"
        self.database = self.root / "state.sqlite3"
        self.manifest_path = self.root / "manifest.json"
        self.plans = self.root / "plans"
        self.receipts = self.root / "receipts"
        self.backups = self.cwd / ".bmlsub" / "backups"

    def initialize(self) -> None:
        self.plans.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS operation_runs (
                    run_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checkpoint TEXT,
                    plan_path TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operation_items (
                    run_id TEXT NOT NULL,
                    item_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    error_json TEXT,
                    PRIMARY KEY (run_id, item_path)
                );
            """)
        if not self.manifest_path.exists():
            atomic_write_json(self.manifest_path, {
                "schema_version": BUILD_SCHEMA_VERSION,
                "context_root": str(self.cwd),
                "operations": {},
            })

    def manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema_version": BUILD_SCHEMA_VERSION,
                    "context_root": str(self.cwd), "operations": {}}
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != BUILD_SCHEMA_VERSION:
            raise ValueError("unsupported build manifest schema")
        return payload

    def write_plan(self, plan: Mapping[str, Any]) -> Path:
        self.initialize()
        run_id = str(plan["run_id"])
        target = self.plans / f"{run_id}.json"
        if target.exists():
            raise FileExistsError(f"immutable build plan already exists: {target}")
        atomic_write_json(target, _sanitized(dict(plan)))
        now = _utc_now()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO operation_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, plan["operation"], plan["mode"], "confirmed", None,
                 str(target), None, now, now),
            )
        return target

    def transition(self, run_id: str, status: str, *, checkpoint: str | None = None,
                   error: Mapping[str, Any] | None = None) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE operation_runs SET status=?, checkpoint=?, error_json=?, updated_at=? WHERE run_id=?",
                (status, checkpoint, json.dumps(_sanitized(error), ensure_ascii=False) if error else None,
                 _utc_now(), run_id),
            )

    def record_item(self, run_id: str, item: str, status: str,
                    outputs: list[dict[str, Any]], error: Mapping[str, Any] | None = None) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO operation_items VALUES (?, ?, ?, ?, ?)",
                (run_id, item, status, json.dumps(_sanitized(outputs), ensure_ascii=False),
                 json.dumps(_sanitized(error), ensure_ascii=False) if error else None),
            )

    def commit_receipt(self, plan: Mapping[str, Any], result: Mapping[str, Any]) -> Path:
        run_id = str(plan["run_id"])
        manifest = self.manifest()
        operations = dict(manifest.get("operations", {}))
        invalidated: list[dict[str, str]] = []
        if plan["mode"] == "rebuild":
            for name in (str(plan["operation"]), *DOWNSTREAM_OPERATIONS.get(str(plan["operation"]), ())):
                history = list(operations.get(name, []))
                for record in history:
                    if record.get("status") not in {"stale", "failed"}:
                        record["status"] = "stale"
                        record["stale_reason"] = f"upstream_rebuilt:{plan['operation']}"
                        invalidated.append({"operation": name, "run_id": str(record.get("run_id"))})
                if history:
                    operations[name] = history
        if isinstance(result, dict):
            result["invalidated"] = invalidated
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "run_id": run_id,
            "operation": plan["operation"],
            "mode": plan["mode"],
            "status": result["status"],
            "committed_at": _utc_now(),
            "items": result.get("items", []),
            "invalidated": invalidated,
        }
        target = self.receipts / f"{run_id}.json"
        atomic_write_json(target, receipt)
        history = list(operations.get(str(plan["operation"]), []))
        history.append({
            "run_id": run_id, "mode": plan["mode"], "status": result["status"],
            "plan": str(self.plans / f"{run_id}.json"), "receipt": str(target),
        })
        operations[str(plan["operation"])] = history
        manifest["operations"] = operations
        atomic_write_json(self.manifest_path, manifest)
        return target

    def latest_receipt(self, operation: str) -> dict[str, Any] | None:
        history = self.manifest().get("operations", {}).get(operation, [])
        if not history:
            return None
        path = Path(history[-1]["receipt"])
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def recorded_outputs(self) -> dict[str, dict[str, Any]]:
        """Return current receipt identities keyed by exact absolute output path."""
        outputs: dict[str, dict[str, Any]] = {}
        for history in self.manifest().get("operations", {}).values():
            for record in history:
                receipt_path = Path(str(record.get("receipt") or ""))
                if not receipt_path.is_file():
                    continue
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for item in receipt.get("items", []):
                    for output in item.get("outputs", []):
                        value = output.get("path")
                        if value:
                            outputs[str(Path(value).expanduser().resolve())] = dict(output)
        return outputs


def _subtitle_extension(codec: str | None) -> tuple[str, str]:
    if codec in {"ass", "ssa"}:
        return ".ass", "ass"
    if codec in {"webvtt", "vtt"}:
        return ".vtt", "webvtt"
    return ".srt", "srt"


def _languages_match(actual: str | None, requested: str) -> bool:
    if not actual:
        return False
    groups = ({"en", "eng"}, {"ja", "jpn"}, {"zh", "chi", "zho"})
    left, right = actual.casefold(), requested.casefold()
    return left == right or any(left in group and right in group for group in groups)


def _mapping_for(operation: str, path: Path, options: Mapping[str, Any],
                 probe: FFprobeClient) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outputs: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    output_dir = Path(options.get("output_dir") or path.parent).expanduser().resolve()
    if operation == "ensub":
        summary = probe.inspect(path)
        language = str(options.get("subtitle_language") or "eng")
        include_unknown = bool(options.get("include_unknown_language"))
        streams = [item for item in summary.streams if item.codec_type == "subtitle"]
        text_streams = [item for item in streams if item.codec_name in TEXT_SUBTITLE_CODECS]
        selected = [item for item in text_streams
                    if _languages_match(item.language, language) or (include_unknown and not item.language)]
        if not selected:
            blockers.append({
                "code": "subtitle_track_missing", "input": str(path),
                "requested_language": language,
                "available": [item.to_dict() for item in streams],
            })
        for stream in selected:
            extension, muxer = _subtitle_extension(stream.codec_name)
            target = output_dir / f"{path.stem}.{stream.language or 'und'}.s{stream.index}{extension}"
            outputs.append({"path": str(target), "stream_index": stream.index,
                            "language": stream.language, "muxer": muxer})
    elif operation == "trans":
        summary = probe.inspect_media(path)
        audio = [item for item in summary.streams if item.codec_type == "audio"]
        if not audio:
            blockers.append({"code": "audio_track_missing", "input": str(path)})
        else:
            requested = str(options.get("audio_language") or "jpn")
            selected = next((item for item in audio if _languages_match(item.language, requested)),
                            next((item for item in audio if item.is_default), audio[0]))
            outputs.extend((
                {"path": str(output_dir / f"{path.stem}.archive.mka"),
                 "stream_index": selected.index, "kind": "archive"},
                {"path": str(output_dir / f"{path.stem}.transcribe.wav"),
                 "stream_index": selected.index, "kind": "transcribe"},
            ))
            strategy = str(options.get("strategy") or "none")
            if strategy in {"quick", "full"}:
                outputs.append({
                    "path": str(output_dir / f"{path.stem}.whisper-medium.direct.json"),
                    "kind": "transcript", "mode": "direct",
                    "model": "mlx-community/whisper-medium-mlx",
                    "language": str(options.get("transcription_language") or "ja"),
                })
            if strategy == "full":
                outputs.append({
                    "path": str(output_dir / f"{path.stem}.whisper-large-v3-turbo.chunked.json"),
                    "kind": "transcript", "mode": "chunked",
                    "model": "mlx-community/whisper-large-v3-turbo",
                    "language": str(options.get("transcription_language") or "ja"),
                    "chunk_seconds": float(options.get("chunk_seconds") or 240.0),
                    "overlap_seconds": float(options.get("overlap_seconds") or 5.0),
                })
            if strategy == "custom":
                mode = str(options.get("transcription_mode") or "direct")
                if mode not in {"direct", "chunked"}:
                    blockers.append({"code": "transcription_mode_invalid", "input": str(path)})
                elif not str(options.get("model") or "").strip():
                    blockers.append({"code": "transcription_model_missing", "input": str(path)})
                else:
                    model = str(options["model"])
                    outputs.append({
                        "path": str(output_dir / f"{path.stem}.{model.split('/')[-1]}.{mode}.json"),
                        "kind": "transcript", "mode": mode, "model": model,
                        "language": str(options.get("transcription_language") or "ja"),
                        "chunk_seconds": float(options.get("chunk_seconds") or 240.0),
                        "overlap_seconds": float(options.get("overlap_seconds") or 5.0),
                    })
    elif operation == "encode":
        mode = str(options.get("product") or "transcode")
        candidates = associated_files(path, path.parent.iterdir())
        if mode in {"hardsub", "mux", "combo"} and not candidates["subtitles"]:
            blockers.append({"code": "subtitle_mapping_missing", "input": str(path)})
        if mode in {"hardsub", "combo"} and not candidates["fonts"]:
            blockers.append({"code": "font_mapping_missing", "input": str(path)})
        if mode == "combo":
            intermediate = output_dir / f"{path.stem}.transcode.mkv"
            outputs.extend((
                {"path": str(intermediate), "product": "transcode", **candidates},
                {"path": str(output_dir / f"{path.stem}.hardsub.mp4"),
                 "product": "hardsub", **candidates},
                {"path": str(output_dir / f"{path.stem}.mux.mkv"), "product": "mux",
                 "video_source": str(intermediate), **candidates},
            ))
        else:
            suffix = ".mp4" if mode == "hardsub" else ".mkv"
            target = output_dir / f"{path.stem}.{mode}{suffix}"
            outputs.append({"path": str(target), "product": mode, **candidates})
    elif operation == "torrent":
        output_dir = Path(options.get("output_dir") or path.parent).expanduser().resolve()
        outputs.append({"path": str(output_dir / f"{path.name}.torrent"), "content": str(path)})
    elif operation == "upr2":
        prefix = str(options.get("object_prefix") or f"{path.parent.name}/").strip("/")
        bucket = str(options.get("bucket") or "bml")
        outputs.append({"bucket": bucket, "object_key": f"{prefix}/{path.name}",
                        "source": str(path)})
    elif operation in {"seed", "anibt"}:
        outputs.append({"torrent": str(path)})
    return outputs, blockers


def plan_operation(operation: str, directory: Path | str = ".", *, rebuild: bool = False,
                   recursive: bool = False, options: Mapping[str, Any] | None = None,
                   probe: FFprobeClient | None = None) -> dict[str, Any]:
    spec = operation_spec(operation)
    refusal = rebuild_refusal(operation) if rebuild else None
    if refusal:
        return refusal
    root = Path(directory).expanduser().resolve()
    choices = dict(options or {})
    discovery = discover_inputs(operation, root, recursive=recursive)
    recorded_outputs = BuildContext(root).recorded_outputs()
    registered_paths = set(recorded_outputs)
    if registered_paths:
        retained = []
        for value in discovery["found"]:
            if str(Path(value).resolve()) in registered_paths:
                discovery["skipped"].append({"path": value, "reason": "registered_output"})
            else:
                retained.append(value)
        discovery["found"] = retained
    inspector = probe or FFprobeClient()
    mappings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    total_bytes = 0
    total_duration_ms = 0
    for value in discovery["found"]:
        source = Path(value)
        total_bytes += source.stat().st_size
        outputs, item_blockers = _mapping_for(operation, source, choices, inspector)
        mappings.append({"input": str(source), "outputs": outputs})
        blockers.extend(item_blockers)
        if source.suffix.lower() in VIDEO_SUFFIXES:
            try:
                total_duration_ms += inspector.inspect_media(source).duration_ms or 0
            except Exception:
                pass

    if spec.input_kind not in {"config", "receipt"} and not discovery["found"]:
        blockers.append({"code": "no_supported_inputs", "operation": operation})
    if operation == "dlvps":
        upload_receipt = BuildContext(root).latest_receipt("upr2")
        remote_root = str(choices.get("remote_root") or "").rstrip("/")
        uploaded = [candidate for item in (upload_receipt or {}).get("items", [])
                    for candidate in item.get("outputs", [])
                    if candidate.get("bucket") and candidate.get("object_key")]
        if not uploaded:
            blockers.append({"code": "upload_receipt_required", "operation": operation})
        elif not remote_root.startswith("/"):
            blockers.append({"code": "remote_root_required", "operation": operation})
        else:
            mappings = []
            for uploaded_item in uploaded:
                name = Path(str(uploaded_item["object_key"])).name
                mappings.append({
                    "input": f"r2://{uploaded_item['bucket']}/{uploaded_item['object_key']}",
                    "outputs": [{**uploaded_item, "path": f"{remote_root}/{name}"}],
                })
            total_bytes = sum(int(item.get("size", 0)) for item in uploaded)
    elif operation == "seed":
        from ..release.torrent import read_torrent_metadata
        remote_receipt = BuildContext(root).latest_receipt("dlvps")
        remote_outputs = [candidate for item in (remote_receipt or {}).get("items", [])
                          for candidate in item.get("outputs", [])]
        for mapping in mappings:
            try:
                metadata = read_torrent_metadata(mapping["input"])
            except Exception as exc:
                blockers.append({"code": "torrent_metadata_invalid", "input": mapping["input"],
                                 "message": str(exc)})
                continue
            remote = next((item for item in remote_outputs
                           if Path(str(item.get("path") or "")).name == metadata.name
                           and int(item.get("size", -1)) == metadata.length), None)
            if remote is None:
                blockers.append({"code": "remote_content_receipt_required", "input": mapping["input"],
                                 "torrent_name": metadata.name})
            mapping["outputs"][0].update({
                "torrent_id": metadata.torrent_id, "info_hash_v1": metadata.info_hash_v1,
                "info_hash_v2": metadata.info_hash_v2, "name": metadata.name,
                "length": metadata.length, "magnet_uri": metadata.magnet_uri,
                "remote_content": remote,
            })
    if operation == "bgminfo":
        mappings = [{"input": str(root), "outputs": [{"path": str(root / "bgminfo" / "series.json")}]}]
    elif operation == "pubinfo":
        mappings = [{"input": str(root), "outputs": [{"path": str(root / ".bmlsub" / "build" / "manifest.json")}]}]

    seen: set[str] = set()
    for mapping in mappings:
        for output in mapping["outputs"]:
            target_value = output.get("path")
            if not target_value:
                continue
            target = Path(target_value)
            key = str(target).casefold()
            if key in seen:
                blockers.append({"code": "duplicate_output", "path": str(target)})
            seen.add(key)
            if target.exists() and not rebuild:
                recorded = recorded_outputs.get(str(target.resolve()))
                if (recorded and recorded.get("sha256") and
                        _sha256(target) == recorded.get("sha256")):
                    output["action"] = "skip"
                    output["reason"] = "recorded_output_is_current"
                elif operation == "bgminfo":
                    try:
                        from .series import SeriesMetadata
                        SeriesMetadata.load(target)
                        output["action"] = "skip"
                        output["reason"] = "valid_series_metadata_exists"
                    except (OSError, ValueError):
                        blockers.append({"code": "output_conflict", "path": str(target),
                                         "next_action": "bmlsub rebuild bgminfo"})
                elif operation == "pubinfo" and BuildContext(root).manifest().get("delivery"):
                    output["action"] = "skip"
                    output["reason"] = "valid_delivery_configuration_exists"
                else:
                    blockers.append({"code": "output_conflict", "path": str(target),
                                     "next_action": f"bmlsub rebuild {operation}"})
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "run_id": uuid4().hex,
        "created_at": _utc_now(),
        "operation": operation,
        "mode": "rebuild" if rebuild else "build",
        "status": "needs_review" if blockers else "planned",
        "context_root": str(root),
        "operation_spec": spec.to_dict(),
        "discovery": discovery,
        "options": _sanitized(choices),
        "mappings": mappings,
        "summary": {"task_count": len(mappings), "input_bytes": total_bytes,
                    "media_duration_ms": total_duration_ms},
        "blockers": blockers,
        "confirmation_word": f"REBUILD {operation.upper()}" if rebuild else "yes",
    }


def _temporary_target(target: Path) -> Path:
    return target.with_name(f".{target.stem}.{uuid4().hex}.tmp{target.suffix}")


def _prepare_target(target: Path, context: BuildContext, rebuild: bool) -> Path | None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return None
    if not rebuild:
        raise FileExistsError(f"output exists; use rebuild: {target}")
    return backup_target(target, context.backups)


def _execute_ensub(source: Path, output: Mapping[str, Any], runner: ProcessRunner,
                    context: BuildContext, rebuild: bool) -> tuple[dict[str, Any], Path | None]:
    target = Path(str(output["path"]))
    backup = _prepare_target(target, context, rebuild)
    temporary = _temporary_target(target)
    try:
        runner.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source,
                    "-map", f"0:{output['stream_index']}", "-c:s", "copy",
                    "-f", str(output["muxer"]), temporary])
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise ValueError("subtitle output is empty")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup:
            restore_backup(backup, target)
        raise
    return {**dict(output), **_identity(target, include_hash=True)}, backup


def _execute_trans(source: Path, outputs: list[Mapping[str, Any]], runner: ProcessRunner,
                   context: BuildContext, rebuild: bool) -> tuple[list[dict[str, Any]], list[Path]]:
    completed: list[dict[str, Any]] = []
    backups: list[Path] = []
    for output in (item for item in outputs if item.get("kind") != "transcript"):
        target = Path(str(output["path"]))
        if output.get("action") == "skip":
            completed.append({**dict(output), **_identity(target, include_hash=True)})
            continue
        backup = _prepare_target(target, context, rebuild)
        if backup:
            backups.append(backup)
        temporary = _temporary_target(target)
        try:
            argv: list[Path | str] = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source,
                                            "-map", f"0:{output['stream_index']}", "-vn", "-sn", "-dn"]
            if output["kind"] == "archive":
                argv.extend(["-c:a", "copy", "-f", "matroska", temporary])
            else:
                argv.extend(["-c:a", "pcm_s16le", "-ac", "1", "-ar", "16000", "-f", "wav", temporary])
            runner.run(argv)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise ValueError("audio output is empty")
            temporary.replace(target)
            completed.append({**dict(output), **_identity(target, include_hash=True)})
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup:
                restore_backup(backup, target)
            raise
    return completed, backups


def _whisper_segments(payload: Mapping[str, Any], *, offset: float = 0.0) -> list[dict[str, Any]]:
    segments = []
    for item in payload.get("segments") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            start = max(0.0, float(item.get("start", 0.0)) + offset)
            end = max(start, float(item.get("end", start)) + offset)
        except (TypeError, ValueError):
            continue
        segments.append({"start": start, "end": end, "text": str(item.get("text") or "")})
    return segments


def _write_transcript(target: Path, output: Mapping[str, Any], segments: list[dict[str, Any]],
                      context: BuildContext, rebuild: bool) -> tuple[dict[str, Any], Path | None]:
    backup = _prepare_target(target, context, rebuild)
    temporary = _temporary_target(target)
    payload = {
        "schema_version": "transcript-v1", "mode": output["mode"],
        "model": output["model"], "language": output["language"],
        "segments": segments, "text": " ".join(
            str(item["text"]).strip() for item in segments if str(item["text"]).strip()
        ),
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        from ..transcription import TranscriptionMode, validate_transcript_output
        validate_transcript_output(
            temporary, expected_mode=TranscriptionMode(str(output["mode"])),
            expected_language=str(output["language"]), expected_model=str(output["model"]),
        )
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup:
            restore_backup(backup, target)
        raise
    return {**dict(output), **_identity(target, include_hash=True)}, backup


def _execute_whisper(wav_path: Path, outputs: list[Mapping[str, Any]], runner: ProcessRunner,
                     context: BuildContext, rebuild: bool) -> list[dict[str, Any]]:
    from ..transcription import MlxWhisperBackend
    backend = MlxWhisperBackend()
    completed: list[dict[str, Any]] = []
    with wave.open(str(wav_path), "rb") as stream:
        duration = stream.getnframes() / stream.getframerate()
    for output in (item for item in outputs if item.get("kind") == "transcript"):
        target = Path(str(output["path"]))
        if output.get("action") == "skip":
            completed.append({**dict(output), **_identity(target, include_hash=True)})
            continue
        if output["mode"] == "direct":
            raw = backend.transcribe(
                wav_path, model=str(output["model"]), language=str(output["language"]), decoding={},
            )
            segments = _whisper_segments(raw)
        else:
            chunk_seconds = float(output.get("chunk_seconds") or 240.0)
            overlap = float(output.get("overlap_seconds") or 5.0)
            if overlap < 0 or overlap >= chunk_seconds:
                raise ValueError("Whisper overlap must be smaller than chunk length")
            starts: list[float] = []
            cursor = 0.0
            while cursor < duration:
                starts.append(cursor)
                end = min(duration, cursor + chunk_seconds)
                if end >= duration:
                    break
                cursor = end - overlap
            segments = []
            chunk_root = context.root / "chunks" / str(output["model"]).replace("/", "_")
            chunk_root.mkdir(parents=True, exist_ok=True)
            for index, start in enumerate(starts):
                end = min(duration, start + chunk_seconds)
                chunk = chunk_root / f"chunk-{index:04d}.wav"
                runner.run([
                    "ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(start),
                    "-t", str(end - start), "-i", wav_path, "-c:a", "pcm_s16le",
                    "-ac", "1", "-ar", "16000", chunk,
                ])
                raw = backend.transcribe(
                    chunk, model=str(output["model"]), language=str(output["language"]), decoding={},
                )
                segments.extend(_whisper_segments(raw, offset=start))
            segments.sort(key=lambda item: (item["start"], item["end"]))
        written, _ = _write_transcript(target, output, segments, context, rebuild)
        completed.append(written)
    return completed


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _execute_encode(source: Path, output: Mapping[str, Any], runner: ProcessRunner,
                    context: BuildContext, rebuild: bool,
                    options: Mapping[str, Any]) -> tuple[dict[str, Any], Path | None]:
    target = Path(str(output["path"]))
    backup = _prepare_target(target, context, rebuild)
    temporary = _temporary_target(target)
    product = str(output["product"])
    try:
        if product == "mux":
            subtitles = [Path(item) for item in output.get("subtitles", [])]
            fonts = [Path(item) for item in output.get("fonts", [])]
            mux_source = Path(str(output.get("video_source") or source))
            argv: list[Path | str] = ["mkvmerge", "-o", temporary, mux_source]
            for subtitle in subtitles:
                argv.extend(["--language", "0:chi", "--track-name", "0:Chinese", subtitle])
            for font in fonts:
                argv.extend(["--attachment-mime-type", _font_mime(font), "--attach-file", font])
            runner.run(argv)
        elif product == "hardsub":
            subtitle = Path(str(output["subtitles"][0]))
            fonts = [Path(item) for item in output.get("fonts", [])]
            fonts_dir = fonts[0].parent if fonts else source.parent
            vf = f"subtitles=filename='{_filter_path(subtitle)}':fontsdir='{_filter_path(fonts_dir)}'"
            runner.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source,
                        "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
                        "-c:v", str(options.get("video_codec") or "libx264"),
                        "-crf", str(options.get("crf") or 20), "-preset", str(options.get("preset") or "medium"),
                        "-c:a", "aac", "-b:a", "192k", temporary])
        else:
            runner.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source,
                        "-map", "0:v:0", "-map", "0:a?", "-map", "-0:s", "-map", "-0:d",
                        "-c:v", str(options.get("video_codec") or "libx265"),
                        "-crf", str(options.get("crf") or 23), "-preset", str(options.get("preset") or "medium"),
                        "-pix_fmt", str(options.get("pixel_format") or "yuv420p10le"),
                        "-c:a", "copy", temporary])
        FFprobeClient().inspect(temporary)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup:
            restore_backup(backup, target)
        raise
    return {**dict(output), **_identity(target, include_hash=True)}, backup


def _font_mime(path: Path) -> str:
    return "font/otf" if path.suffix.lower() in {".otf", ".otc"} else "font/ttf"


def _execute_torrent(source: Path, output: Mapping[str, Any], context: BuildContext,
                     rebuild: bool, options: Mapping[str, Any]) -> tuple[dict[str, Any], Path | None]:
    from ..release.profiles import TorrentProfile
    from ..release.torrent import create_torrent
    target = Path(str(output["path"]))
    backup = _prepare_target(target, context, rebuild)
    temporary = _temporary_target(target)
    trackers = tuple(options.get("trackers") or ("https://tracker.example.invalid/announce",))
    profile = TorrentProfile(
        format=str(options.get("torrent_format") or "v1"),
        piece_length=options.get("piece_length"), private=bool(options.get("private", True)),
        comment=str(options.get("comment") or ""),
    )
    try:
        metadata = create_torrent(source, temporary, trackers=trackers, profile=profile)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup:
            restore_backup(backup, target)
        raise
    return {**dict(output), **_identity(target, include_hash=True),
            "torrent": metadata.bounded()}, backup


def _r2_head(client: Any, bucket: str, object_key: str) -> Mapping[str, Any] | None:
    try:
        return client.client.head_object(Bucket=bucket, Key=object_key)
    except Exception as exc:
        response = getattr(exc, "response", None)
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") if isinstance(response, Mapping) else None
        code = response.get("Error", {}).get("Code") if isinstance(response, Mapping) else None
        if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _execute_upr2(source: Path, output: Mapping[str, Any], context: BuildContext,
                  rebuild: bool, options: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    from ..credentials import CredentialService
    from ..release.external_profiles import R2UploadProfile
    from ..release.r2 import Boto3R2Client, validate_remote_object

    alias = str(options.get("r2_profile") or "").strip()
    if not alias:
        raise ValueError("an existing R2 credential profile is required")
    service = CredentialService(manifest_path=options.get("credential_manifest"))
    credentials = service.resolve_r2(alias)
    client = Boto3R2Client(credentials)
    source_identity = _identity(source, include_hash=True)
    bucket = str(output["bucket"])
    object_key = str(output["object_key"])
    profile = R2UploadProfile(
        bucket=bucket, object_key=object_key,
        content_type=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
    )
    existing = _r2_head(client, bucket, object_key)
    if existing is not None:
        metadata = {str(key).casefold(): str(value) for key, value in dict(existing.get("Metadata") or {}).items()}
        same = (int(existing.get("ContentLength", -1)) == source_identity["size"] and
                metadata.get("bml-sha256") == source_identity["sha256"])
        if not rebuild:
            if same:
                identity = validate_remote_object(
                    client, profile, expected_size=int(source_identity["size"]),
                    expected_sha256=str(source_identity["sha256"]),
                )
                return {**dict(output), **identity.bounded(), "credential_profile": alias}, True
            raise FileExistsError(f"R2 object differs; use rebuild upr2: {bucket}/{object_key}")
        previous = context.latest_receipt("upr2")
        recorded = next((candidate for item in (previous or {}).get("items", [])
                         for candidate in item.get("outputs", [])
                         if candidate.get("bucket") == bucket and candidate.get("object_key") == object_key), None)
        if (recorded is None or int(recorded.get("size", -1)) != int(existing.get("ContentLength", -2))
                or str(recorded.get("sha256")) != metadata.get("bml-sha256")):
            raise ValueError("R2 rebuild refused because the old receipt does not prove the current object identity")
        context.transition(str(options["run_id"]), "running", checkpoint="delete_started")
        client.client.delete_object(Bucket=bucket, Key=object_key)
        if _r2_head(client, bucket, object_key) is not None:
            raise ValueError("R2 object still exists after exact delete")
        context.transition(str(options["run_id"]), "running", checkpoint="deleted")
    metadata = {
        "bml-sha256": str(source_identity["sha256"]),
        "bml-schema": "build-receipt-v1",
    }
    client.upload(source, profile, metadata=metadata)
    identity = validate_remote_object(
        client, profile, expected_size=int(source_identity["size"]),
        expected_sha256=str(source_identity["sha256"]),
    )
    return {**dict(output), **identity.bounded(), "credential_profile": alias}, False


def _execute_anibt(source: Path, output: Mapping[str, Any], context: BuildContext,
                   options: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    from ..credentials import CredentialService
    from ..release.anibt import RequestsAnibtClient
    from ..release.external_profiles import AnibtPublishProfile
    from ..release.torrent import read_torrent_metadata

    alias = str(options.get("anibt_profile") or "").strip()
    if not alias:
        raise ValueError("an existing Anibt credential profile is required")
    metadata = read_torrent_metadata(source)
    previous = context.latest_receipt("anibt")
    recorded = next((candidate for item in (previous or {}).get("items", [])
                     for candidate in item.get("outputs", [])
                     if candidate.get("torrent_id") == metadata.torrent_id), None)
    if recorded is not None:
        return dict(recorded), True
    credentials = CredentialService(
        manifest_path=options.get("credential_manifest"),
    ).resolve_anibt(alias)
    nyaa = bool(options.get("nyaa"))
    trackers = metadata.trackers or ("https://tracker.anibt.net/announce",)
    if nyaa and not any("nyaa.tracker.wf:7777/announce" in item for item in trackers):
        trackers = (*trackers, "http://nyaa.tracker.wf:7777/announce")
    profile = AnibtPublishProfile(
        anime_id_type=str(options.get("anime_id_type") or "bgm"),
        anime_id=str(options.get("anime_id") or ""), title=str(options.get("title") or metadata.name),
        episode_key=str(options.get("episode_key") or ""),
        resolution=str(options.get("resolution") or "1080p"),
        language=tuple(options.get("language") or ("CHS", "JP")),
        subtitle=str(options.get("subtitle") or "INTERNAL"),
        format=str(options.get("format") or Path(metadata.name).suffix.lstrip(".").upper() or "MKV"),
        file_size=metadata.length, trackers=tuple(trackers), notes=str(options.get("notes") or ""),
        nyaa=nyaa, nyaa_category="1_4" if nyaa else "",
    )
    response = RequestsAnibtClient().publish(
        torrent_path=source, profile=profile,
        api_url=credentials.api_url, token=credentials.token,
    )
    return {
        **dict(output), "torrent_id": metadata.torrent_id,
        "info_hash_v1": metadata.info_hash_v1, "name": metadata.name,
        "length": metadata.length, "credential_profile": alias,
        "profile": profile.receipt_summary(),
        "response": {key: value for key, value in response.items()
                     if key in {"ok", "id", "releaseId", "url", "message"}},
    }, False


def _execute_dlvps(output: Mapping[str, Any], context: BuildContext, rebuild: bool,
                   options: Mapping[str, Any], runner: ProcessRunner) -> tuple[dict[str, Any], bool]:
    from ..credentials import CredentialService
    from ..release.external_profiles import RemotePullProfile
    from ..release.remote import SSHRclonePullClient

    alias = str(options.get("remote_pull_profile") or "").strip()
    if not alias:
        raise ValueError("an existing remote-pull credential profile is required")
    credentials = CredentialService(
        manifest_path=options.get("credential_manifest"),
    ).resolve_remote_pull(alias)
    profile = RemotePullProfile(
        ssh_alias=credentials.ssh_alias, rclone_remote=credentials.rclone_remote,
        bucket=str(output["bucket"]), object_key=str(output["object_key"]),
        target_path=str(output["path"]),
    )
    client = SSHRclonePullClient(runner=runner)
    expected_size = int(output["size"])
    expected_sha256 = str(output["sha256"])
    try:
        current = client.inspect(profile)
    except Exception:
        current = None
    if current is not None:
        same = current.size == expected_size and current.sha256 == expected_sha256
        if not rebuild:
            if same:
                return {**dict(output), **current.bounded(), "credential_profile": alias}, True
            raise FileExistsError(f"VPS target differs; use rebuild dlvps: {profile.target_path}")
        previous = context.latest_receipt("dlvps")
        recorded = next((candidate for item in (previous or {}).get("items", [])
                         for candidate in item.get("outputs", [])
                         if candidate.get("path") == profile.target_path), None)
        if (recorded is None or int(recorded.get("size", -1)) != current.size
                or str(recorded.get("sha256")) != current.sha256):
            raise ValueError("VPS rebuild refused because the old receipt does not prove the target identity")
        script = "\n".join((
            "set -eu", f"target={shlex.quote(profile.target_path)}",
            f"test $(wc -c < \"$target\" | tr -d ' ') = {current.size}",
            "if command -v sha256sum >/dev/null 2>&1; then sha=$(sha256sum \"$target\" | cut -d ' ' -f 1); else sha=$(shasum -a 256 \"$target\" | cut -d ' ' -f 1); fi",
            f"test \"$sha\" = {shlex.quote(current.sha256)}", "rm -f -- \"$target\"",
        ))
        context.transition(str(options["run_id"]), "running", checkpoint="delete_started")
        runner.run(["ssh", profile.ssh_alias, f"sh -c {shlex.quote(script)}"])
        context.transition(str(options["run_id"]), "running", checkpoint="deleted")
    identity = client.pull(
        profile, run_id=str(options["run_id"]),
        expected_size=expected_size, expected_sha256=expected_sha256,
    )
    return {**dict(output), **identity.bounded(), "credential_profile": alias}, False


def _execute_seed(source: Path, output: Mapping[str, Any], context: BuildContext,
                  rebuild: bool, options: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    from ..credentials import CredentialService
    from ..release.external_profiles import QBittorrentSeedProfile
    from ..release.qbittorrent import SSHQBittorrentClient

    qb_alias = str(options.get("qb_profile") or "").strip()
    ssh_profile = str(options.get("ssh_profile") or "").strip()
    if not qb_alias or not ssh_profile:
        raise ValueError("existing qBittorrent and SSH profiles are required")
    service = CredentialService(manifest_path=options.get("credential_manifest"))
    credentials = service.resolve_qbittorrent(qb_alias)
    ssh_alias, _ssh_reference = service.resolve_ssh(ssh_profile)
    profile = QBittorrentSeedProfile(
        ssh_alias=ssh_alias, port=int(options.get("qb_port") or 8080),
        save_path=str(options.get("qb_save_path") or "/downloads"),
        webui_origin=str(options.get("qb_origin") or "https://127.0.0.1:8080"),
    )
    client = SSHQBittorrentClient(credentials)
    if rebuild:
        previous = context.latest_receipt("seed")
        recorded = next((candidate for item in (previous or {}).get("items", [])
                         for candidate in item.get("outputs", [])
                         if candidate.get("torrent_hash") in {output.get("info_hash_v1"), output.get("info_hash_v2")}), None)
        if recorded is None:
            raise ValueError("qB rebuild refused because no exact prior task receipt exists")
        identity = client.inspect(torrent_hash=str(recorded["torrent_hash"]), profile=profile)
        if (identity.name != recorded.get("name") or identity.total_size != recorded.get("total_size")
                or identity.save_path != recorded.get("save_path")):
            raise ValueError("qB rebuild refused because the live task identity differs from its receipt")
        context.transition(str(options["run_id"]), "running", checkpoint="delete_started")
        with client._session(profile) as (session, base_url):
            client._delete_task(session, base_url, identity.torrent_hash)
        context.transition(str(options["run_id"]), "running", checkpoint="deleted")
    identity = client.add_and_verify(
        torrent_path=source, magnet_uri=str(output["magnet_uri"]),
        expected_hash=str(output["info_hash_v1"]), expected_name=str(output["name"]),
        expected_size=int(output["length"]), profile=profile,
        alternate_hashes=tuple(item for item in (output.get("info_hash_v2"),) if item),
    )
    return {**dict(output), **identity.bounded(),
            "credential_profile": qb_alias, "ssh_profile": ssh_profile}, False


def execute_plan(plan: Mapping[str, Any], *, context: BuildContext | None = None,
                 runner: ProcessRunner | None = None) -> dict[str, Any]:
    """Execute one confirmed local plan, recording every item and final receipt."""
    if plan.get("status") != "planned":
        return {"status": "needs_review", "operation": plan.get("operation"),
                "blockers": plan.get("blockers", [])}
    operation = str(plan["operation"])
    spec = operation_spec(operation)
    if spec.external and operation not in {"upr2", "dlvps", "seed", "anibt"}:
        return {
            "status": "needs_review", "operation": operation,
            "error": {"code": "external_configuration_required",
                      "message": "external execution requires a verified credential profile and receipt identity"},
            "plan": dict(plan), "external_actions": [],
        }
    root = context or BuildContext(str(plan["context_root"]))
    root.write_plan(plan)
    root.transition(str(plan["run_id"]), "running")
    process = runner or ProcessRunner(timeout=7200)
    rebuild = plan["mode"] == "rebuild"
    options = dict(plan.get("options", {}))
    options["run_id"] = plan["run_id"]
    items: list[dict[str, Any]] = []
    successes = failures = skipped = 0
    try:
        if operation == "bgminfo":
            planned_output = plan["mappings"][0]["outputs"][0]
            if planned_output.get("action") == "skip":
                target = Path(planned_output["path"])
                result = {**dict(planned_output), **_identity(target, include_hash=True)}
                item_status = "skipped"
                skipped += 1
            else:
                result = _execute_bgminfo(root, options, rebuild)
                item_status = "succeeded"
                successes += 1
            item = {"input": str(root.cwd), "status": item_status, "outputs": [result]}
            root.record_item(str(plan["run_id"]), str(root.cwd), item_status, [result])
            items.append(item)
        elif operation == "pubinfo":
            planned_output = plan["mappings"][0]["outputs"][0]
            if planned_output.get("action") == "skip":
                result = dict(planned_output)
                item_status = "skipped"
                skipped += 1
            else:
                result = _execute_pubinfo(root, options, rebuild)
                item_status = "succeeded"
                successes += 1
            item = {"input": str(root.cwd), "status": item_status, "outputs": [result]}
            root.record_item(str(plan["run_id"]), str(root.cwd), item_status, [result])
            items.append(item)
        else:
            for mapping in plan["mappings"]:
                source = Path(mapping["input"]) if operation != "dlvps" else None
                try:
                    if mapping["outputs"] and all(
                        output.get("action") == "skip" for output in mapping["outputs"]
                    ):
                        outputs = [
                            {**dict(output), **_identity(Path(output["path"]), include_hash=True)}
                            for output in mapping["outputs"]
                        ]
                        item = {"input": str(source), "input_identity": _identity(source, include_hash=True),
                                "status": "skipped", "outputs": outputs}
                        root.record_item(str(plan["run_id"]), str(source), "skipped", outputs)
                        items.append(item)
                        skipped += 1
                        continue
                    if operation == "ensub":
                        outputs = [
                            ({**dict(output), **_identity(Path(output["path"]), include_hash=True)}
                             if output.get("action") == "skip" else
                             _execute_ensub(source, output, process, root, rebuild)[0])
                            for output in mapping["outputs"]
                        ]
                    elif operation == "trans":
                        outputs, _ = _execute_trans(source, mapping["outputs"], process, root, rebuild)
                        transcript_outputs = [item for item in mapping["outputs"]
                                              if item.get("kind") == "transcript"]
                        if transcript_outputs:
                            wav_path = next(Path(str(item["path"])) for item in mapping["outputs"]
                                            if item.get("kind") == "transcribe")
                            outputs.extend(_execute_whisper(
                                wav_path, transcript_outputs, process, root, rebuild,
                            ))
                    elif operation == "encode":
                        outputs = [
                            ({**dict(output), **_identity(Path(output["path"]), include_hash=True)}
                             if output.get("action") == "skip" else
                             _execute_encode(source, output, process, root, rebuild, options)[0])
                            for output in mapping["outputs"]
                        ]
                    elif operation == "torrent":
                        outputs = [_execute_torrent(source, mapping["outputs"][0], root,
                                                    rebuild, options)[0]]
                    elif operation == "upr2":
                        result_output, reused = _execute_upr2(
                            source, mapping["outputs"][0], root, rebuild, options,
                        )
                        outputs = [result_output]
                        if reused:
                            item = {"input": str(source), "input_identity": _identity(source, include_hash=True),
                                    "status": "skipped", "outputs": outputs}
                            root.record_item(str(plan["run_id"]), str(source), "skipped", outputs)
                            items.append(item)
                            skipped += 1
                            continue
                    elif operation == "anibt":
                        result_output, reused = _execute_anibt(
                            source, mapping["outputs"][0], root, options,
                        )
                        outputs = [result_output]
                        if reused:
                            item = {"input": str(source), "input_identity": _identity(source, include_hash=True),
                                    "status": "skipped", "outputs": outputs}
                            root.record_item(str(plan["run_id"]), str(source), "skipped", outputs)
                            items.append(item)
                            skipped += 1
                            continue
                    elif operation == "dlvps":
                        result_output, reused = _execute_dlvps(
                            mapping["outputs"][0], root, rebuild, options, process,
                        )
                        outputs = [result_output]
                        if reused:
                            item = {"input": mapping["input"], "status": "skipped", "outputs": outputs}
                            root.record_item(str(plan["run_id"]), mapping["input"], "skipped", outputs)
                            items.append(item)
                            skipped += 1
                            continue
                    elif operation == "seed":
                        assert source is not None
                        result_output, reused = _execute_seed(source, mapping["outputs"][0], root,
                                                              rebuild, options)
                        outputs = [result_output]
                        if reused:
                            item = {"input": str(source), "input_identity": _identity(source, include_hash=True),
                                    "status": "skipped", "outputs": outputs}
                            root.record_item(str(plan["run_id"]), str(source), "skipped", outputs)
                            items.append(item)
                            skipped += 1
                            continue
                    else:
                        raise ValueError(f"operation is not locally executable: {operation}")
                    item = {"input": mapping["input"], "status": "succeeded", "outputs": outputs}
                    if source is not None:
                        item["input_identity"] = _identity(source, include_hash=True)
                    root.record_item(str(plan["run_id"]), mapping["input"], "succeeded", outputs)
                    successes += 1
                except KeyboardInterrupt:
                    root.record_item(str(plan["run_id"]), mapping["input"], "interrupted", [])
                    raise
                except Exception as exc:
                    error = {"code": "item_failed", "message": str(exc),
                             "exception_type": type(exc).__name__}
                    item = {"input": mapping["input"], "status": "failed", "outputs": [], "error": error}
                    root.record_item(str(plan["run_id"]), mapping["input"], "failed", [], error)
                    failures += 1
                items.append(item)
                if failures and spec.external:
                    break
        status = ("partial" if (successes or skipped) and failures else
                  "failed" if failures else "succeeded" if successes else "skipped")
        result = {"status": status, "operation": operation, "mode": plan["mode"],
                  "run_id": plan["run_id"], "items": items,
                  "completed": successes, "skipped": skipped, "failed": failures,
                  "next_action": None if not failures else f"bmlsub {plan['mode']} {operation}"}
        receipt = root.commit_receipt(plan, result)
        result["receipt"] = str(receipt)
        root.transition(str(plan["run_id"]), status, checkpoint="committed")
        return result
    except KeyboardInterrupt:
        root.transition(str(plan["run_id"]), "interrupted")
        return {"status": "interrupted", "operation": operation, "run_id": plan["run_id"],
                "items": items, "next_action": f"bmlsub {plan['mode']} {operation}"}
    except Exception as exc:
        error = {"code": "operation_failed", "message": str(exc),
                 "exception_type": type(exc).__name__}
        root.transition(str(plan["run_id"]), "failed", error=error)
        return {"status": "failed", "operation": operation, "run_id": plan["run_id"],
                "items": items, "error": error,
                "next_action": f"bmlsub {plan['mode']} {operation}"}


def _execute_bgminfo(context: BuildContext, options: Mapping[str, Any], rebuild: bool) -> dict[str, Any]:
    from .series import create_series_metadata
    required = ("title_chs", "romanized_title", "group_chs")
    missing = [key for key in required if not str(options.get(key) or "").strip()]
    if missing:
        raise ValueError(f"missing series metadata fields: {', '.join(missing)}")
    target = context.cwd / "bgminfo" / "series.json"
    backup = _prepare_target(target, context, rebuild)
    try:
        metadata = create_series_metadata(
            context.cwd.name, parent_dir=context.cwd.parent,
            title_chs=str(options["title_chs"]), title_cht=options.get("title_cht"),
            romanized_title=str(options["romanized_title"]),
            group_chs=str(options["group_chs"]), group_cht=options.get("group_cht"),
            bgm_id=int(options["bgm_id"]) if options.get("bgm_id") else None,
            anime_id=str(options["anime_id"]) if options.get("anime_id") else None,
            replace=False,
        )
    except Exception:
        if backup:
            restore_backup(backup, target)
        raise
    return {"path": str(target), "sha256": metadata.content_hash,
            "backup": str(backup) if backup else None}


def _execute_pubinfo(context: BuildContext, options: Mapping[str, Any], rebuild: bool) -> dict[str, Any]:
    allowed = {"ssh_alias", "remote_root", "qb_origin", "qb_save_path", "r2_profile",
               "r2_bucket", "anibt_profile", "credential_manifest"}
    values = {key: value for key, value in options.items() if key in allowed and value not in (None, "")}
    if not values:
        raise ValueError("delivery configuration is empty")
    context.initialize()
    manifest = context.manifest()
    if manifest.get("delivery") and not rebuild:
        raise FileExistsError("delivery configuration exists; use rebuild pubinfo")
    if manifest.get("delivery") and rebuild:
        snapshot = context.backups / f"manifest.{uuid4().hex}.json"
        atomic_write_json(snapshot, manifest)
    manifest["delivery"] = values
    atomic_write_json(context.manifest_path, manifest)
    return {"path": str(context.manifest_path), "configuration": values}
