from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import datetime, timezone
import unittest
import wave

from bmlsub.pipeline import Pipeline
from bmlsub.state.fingerprints import fingerprint_file
from bmlsub.state.models import ArtifactRecord, StageStatus, ValidationStatus
from bmlsub.state.sqlite_store import SQLiteJobStore
from bmlsub.transcription.core import (
    TRANSCRIPTION_PROGRESS_SCHEMA_VERSION,
    TranscriptionMode,
    TranscriptionOptions,
    _transcribe_chunked,
    _transcribe_direct,
    run_transcription,
)


class _Backend:
    def version(self) -> str:
        return "test"

    def transcribe(self, audio_path: Path, *, model: str, language: str, decoding):
        return {"segments": [{"start": 0.0, "end": 0.5, "text": audio_path.name}]}


class _Process:
    def version(self, executable) -> str:
        return "test"

    def run(self, args, *, timeout):
        target = Path(args[-1])
        with wave.open(str(target), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 1600)
        return None


class TranscriptionProgressTests(unittest.TestCase):
    def test_direct_reports_only_observable_start_and_completion(self):
        events = []
        options = TranscriptionOptions()

        _transcribe_direct(
            Path("audio.wav"), _Backend(), options, "model",
            progress_callback=events.append,
        )

        self.assertEqual([item["status"] for item in events], ["started", "completed"])
        self.assertTrue(all(
            item["schema_version"] == TRANSCRIPTION_PROGRESS_SCHEMA_VERSION
            and item["mode"] == "direct"
            and "current" not in item and "total" not in item
            for item in events
        ))

    def test_chunked_reports_completed_chunk_count(self):
        events = []
        options = TranscriptionOptions(mode=TranscriptionMode.CHUNKED)
        chunks = ((0.0, 10.0), (10.0, 20.0), (20.0, 25.0))

        with TemporaryDirectory() as temporary:
            paths = tuple(Path(temporary) / f"chunk-{index}.wav" for index in range(3))
            _transcribe_chunked(
                Path("audio.wav"), _Backend(), options, model_path="model",
                process=_Process(), ffmpeg="ffmpeg", process_timeout=30,
                chunks=chunks, chunk_paths=paths, progress_callback=events.append,
            )

        self.assertEqual([item["status"] for item in events], [
            "started", "progress", "progress", "progress", "completed",
        ])
        self.assertEqual(
            [(item["current"], item["total"]) for item in events],
            [(0, 3), (1, 3), (2, 3), (3, 3), (3, 3)],
        )
        self.assertEqual(
            [item["chunk_index"] for item in events if item["status"] == "progress"],
            [0, 1, 2],
        )
        self.assertTrue(all("percent" not in item for item in events))

    def test_both_mode_emits_direct_and_real_chunk_progress_from_public_api(self):
        events = []
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "audio.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 16000)
            store = SQLiteJobStore.for_workspace(root, root / "state")
            store.initialize()
            run = store.create_run(root, "test", episode_id="01")
            stage = store.create_stage(
                run.run_id, "test.audio", input_fingerprint="input",
                parameter_fingerprint="parameters", tool_fingerprint="tools",
            )
            store.mark_stage_running(stage.stage_id)
            fingerprint = fingerprint_file(audio, content_hash=True)
            store.register_artifact(ArtifactRecord(
                artifact_id="audio", run_id=run.run_id, stage_id=stage.stage_id,
                episode_id="01", artifact_type="generated.audio.transcribe",
                path=audio, size=fingerprint.size, mtime_ns=fingerprint.mtime_ns,
                content_hash=fingerprint.content_hash,
                validation_status=ValidationStatus.VALID,
                created_at=datetime.now(timezone.utc),
                metadata={"media": {"duration_ms": 1000}},
            ))
            store.complete_stage(stage.stage_id)

            result = run_transcription(
                workspace=root, episode_id="01", audio_artifact_id="audio",
                options=TranscriptionOptions(
                    mode=TranscriptionMode.BOTH, chunk_seconds=0.6,
                    overlap_seconds=0.1,
                ),
                backend=_Backend(), runner=_Process(), store=store,
                progress_callback=events.append,
            )

        self.assertIs(result.status, StageStatus.SUCCEEDED)
        self.assertEqual(
            [(item["mode"], item["status"]) for item in events],
            [
                ("direct", "started"), ("direct", "completed"),
                ("chunked", "started"), ("chunked", "progress"),
                ("chunked", "progress"), ("chunked", "completed"),
            ],
        )
        chunk_progress = [item for item in events if item["status"] == "progress"]
        self.assertEqual(
            [(item["current"], item["total"]) for item in chunk_progress],
            [(1, 2), (2, 2)],
        )

    @patch("bmlsub.pipeline.run_transcription")
    def test_pipeline_forwards_progress_callback(self, run_transcription):
        callback = lambda event: None
        run_transcription.return_value.to_dict.return_value = {"status": "succeeded"}

        result = Pipeline().transcribe(
            workspace=".", episode_id="01", audio_artifact_id="audio",
            progress_callback=callback,
        )

        self.assertEqual(result, {"status": "succeeded"})
        self.assertIs(
            run_transcription.call_args.kwargs["progress_callback"], callback,
        )


if __name__ == "__main__":
    unittest.main()
