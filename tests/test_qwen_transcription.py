from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmlsub.execution import BmlsubError, ErrorCode
from bmlsub.pipeline import Pipeline
from bmlsub.state.fingerprints import fingerprint_file
from bmlsub.state.models import ArtifactRecord, StageStatus, ValidationStatus
from bmlsub.state.sqlite_store import SQLiteJobStore
from bmlsub.transcription import (
    DEFAULT_QWEN3_MODEL, DEFAULT_WHISPER_MODEL, MlxQwen3AsrBackend,
    MlxWhisperBackend, TranscriptionMode, TranscriptionOptions, create_backend,
    run_transcription, run_transcript_text_export,
)
from bmlsub.transcription.core import _output_path, _transcribe_direct
from bmlsub.workstation.models import PreprocessConfig, TranscriptionJob, transcription_jobs_for_mode
from bmlsub.workstation.preprocess import _transcription_policy


def _result(**overrides):
    value = dict(
        text="こんにちは。", language="Japanese", finish_reason="eos", truncated=False,
        chunks=[{"start": 0.0, "end": 1.0, "text": "こんにちは。", "chunk_index": 0,
                 "finish_reason": "eos", "truncated": False, "generated_tokens": 4}],
    )
    value.update(overrides)
    return SimpleNamespace(**value)


def _audio(root):
    wav = root / "audio.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    store = SQLiteJobStore.for_workspace(root)
    store.initialize()
    run = store.create_run(str(root), "test", episode_id="17")
    stage = store.create_stage(run.run_id, "test.audio", input_fingerprint="audio",
                               parameter_fingerprint="test", tool_fingerprint="test")
    store.mark_stage_running(stage.stage_id)
    fp = fingerprint_file(wav, content_hash=True)
    store.register_artifact(ArtifactRecord(
        artifact_id="audio", run_id=run.run_id, stage_id=stage.stage_id,
        episode_id="17", artifact_type="generated.audio.transcribe", path=wav,
        size=fp.size, mtime_ns=fp.mtime_ns, content_hash=fp.content_hash,
        validation_status=ValidationStatus.VALID, created_at=datetime.now(timezone.utc),
        metadata={"media": {"duration_ms": 1000}},
    ))
    store.complete_stage(stage.stage_id)
    return wav, store


class QwenBackendTests(unittest.TestCase):
    def test_full_audio_is_passed_once_and_native_chunk_metadata_is_preserved(self):
        native = Mock(return_value=_result())
        with patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=native)}):
            result = MlxQwen3AsrBackend().transcribe(
                Path("complete.wav"), model="snapshot", language="ja", decoding={},
            )
        native.assert_called_once_with("complete.wav", model="snapshot", language="ja",
                                       return_chunks=True, return_timestamps=False)
        self.assertEqual(result["segments"][0]["text"], "こんにちは。")
        self.assertEqual(result["timestamp_granularity"], "audio_chunk")
        self.assertFalse(result["asr_metadata"]["truncated"])

    def test_supported_options_are_forwarded_and_whisper_options_rejected(self):
        native = Mock(return_value=_result())
        with patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=native)}):
            backend = MlxQwen3AsrBackend()
            backend.transcribe(Path("audio.wav"), model="m", language="ja",
                               decoding={"context": "ルル リリー", "max_new_tokens": 1024})
            self.assertEqual(native.call_args.kwargs["max_new_tokens"], 1024)
            with self.assertRaisesRegex(ValueError, "unsupported Qwen3"):
                backend.transcribe(Path("audio.wav"), model="m", language="ja",
                                   decoding={"condition_on_previous_text": False})
            self.assertEqual(native.call_count, 1)

    def test_truncated_result_fails_instead_of_publishing_partial_text(self):
        native = Mock(return_value=_result(truncated=True, finish_reason="length"))
        with patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=native)}):
            with self.assertRaises(BmlsubError) as caught:
                MlxQwen3AsrBackend().transcribe(Path("a.wav"), model="m", language="ja", decoding={})
        self.assertEqual(caught.exception.code, ErrorCode.OUTPUT_VALIDATION_FAILED)

    def test_missing_dependency_has_actionable_error(self):
        with patch.dict("sys.modules", {"mlx_qwen3_asr": None}):
            with self.assertRaises(BmlsubError) as caught:
                MlxQwen3AsrBackend().transcribe(Path("a.wav"), model="m", language="ja", decoding={})
        self.assertEqual(caught.exception.details["dependency"], "mlx-qwen3-asr")

    def test_inference_failure_is_reported_as_retryable(self):
        with patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=Mock(side_effect=RuntimeError("gpu")))}):
            with self.assertRaises(BmlsubError) as caught:
                MlxQwen3AsrBackend().transcribe(Path("a.wav"), model="m", language="ja", decoding={})
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.code, ErrorCode.EXTERNAL_SERVICE_ERROR)

    def test_repetition_stop_is_flagged_for_review_without_rewriting_text(self):
        result = _result(finish_reason="repetition")
        result.chunks[0]["finish_reason"] = "repetition"
        with patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=Mock(return_value=result))}):
            payload = MlxQwen3AsrBackend().transcribe(Path("a.wav"), model="m", language="ja", decoding={})
        self.assertEqual(payload["asr_metadata"]["review_required_chunk_indices"], [0])
        self.assertEqual(payload["text"], result.text)

    def test_direct_payload_retains_chunk_granularity_and_decode_diagnostics(self):
        backend = Mock()
        backend.transcribe.return_value = {"segments": _result().chunks,
            "timestamp_granularity": "audio_chunk", "asr_metadata": {"finish_reason": "eos"}}
        result = _transcribe_direct(Path("a.wav"), backend, TranscriptionOptions(engine="qwen3"), "model")
        self.assertEqual(result["engine"], "qwen3")
        self.assertEqual(result["timestamp_granularity"], "audio_chunk")
        self.assertEqual(result["asr_metadata"]["finish_reason"], "eos")


class QwenPolicyTests(unittest.TestCase):
    def test_full_policy_keeps_two_whisper_jobs_then_adds_direct_qwen(self):
        jobs = transcription_jobs_for_mode("full")
        self.assertEqual([(j.name, j.engine, j.mode) for j in jobs], [
            ("direct", "whisper", "direct"), ("chunked", "whisper", "chunked"),
            ("qwen3", "qwen3", "direct")])
        self.assertEqual(jobs[-1].model, DEFAULT_QWEN3_MODEL)
        self.assertEqual(jobs[-1].manual_cuts, ())
        self.assertEqual(_transcription_policy(jobs), "full")
        self.assertEqual(_transcription_policy(jobs[:2]), "full")
        self.assertEqual(_transcription_policy(jobs[-1:]), "qwen3")
        self.assertEqual(PreprocessConfig(transcription_policy="qwen3").transcription_policy, "qwen3")

    def test_engine_selects_default_model_and_backend(self):
        self.assertEqual(TranscriptionOptions().model, DEFAULT_WHISPER_MODEL)
        self.assertEqual(TranscriptionOptions(engine="qwen3").model, DEFAULT_QWEN3_MODEL)
        self.assertIsInstance(create_backend(), MlxWhisperBackend)
        self.assertIsInstance(create_backend("qwen3"), MlxQwen3AsrBackend)
        with self.assertRaises(ValueError):
            create_backend("unknown")

    def test_qwen_rejects_external_chunking_and_manual_cuts(self):
        for options in ({"mode": "chunked"}, {"mode": "both"}, {"manual_cuts": (10.0,)}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError, "Qwen3 requires direct"):
                    TranscriptionOptions(engine="qwen3", **options)
                with self.assertRaisesRegex(ValueError, "Qwen3 requires direct"):
                    TranscriptionJob(name="qwen3", engine="qwen3", **options)

    def test_old_serialized_jobs_still_default_to_whisper(self):
        job = TranscriptionJob(**{"name": "direct", "model": "mlx-community/whisper-medium-mlx"})
        self.assertEqual(job.engine, "whisper")
        qwen = transcription_jobs_for_mode("qwen3")[0]
        self.assertEqual(TranscriptionJob(**qwen.to_dict()), qwen)

    def test_named_jobs_do_not_change_engine_when_saved_as_policy(self):
        self.assertEqual(_transcription_policy((TranscriptionJob(name="qwen3"),)), "custom")
        self.assertEqual(_transcription_policy((TranscriptionJob(name="direct", engine="qwen3"),)), "custom")
        self.assertEqual(_transcription_policy((TranscriptionJob(name="qwen3", engine="qwen3", model="Qwen/Qwen3-ASR-0.6B"),)), "custom")

    def test_engine_output_identity_is_distinct(self):
        args = (Path("out"), "17", TranscriptionMode.DIRECT, "same-model")
        self.assertNotEqual(_output_path(*args), _output_path(*args, engine="qwen3"))

    @patch("bmlsub.pipeline.run_transcription")
    def test_pipeline_forwards_qwen_options(self, run):
        run.return_value.to_dict.return_value = {"status": "succeeded"}
        Pipeline().transcribe(workspace=".", episode_id="17", audio_artifact_id="audio", engine="qwen3")
        options = run.call_args.kwargs["options"]
        self.assertEqual((options.engine, options.model, options.mode),
                         ("qwen3", DEFAULT_QWEN3_MODEL, TranscriptionMode.DIRECT))


class QwenStageTests(unittest.TestCase):
    def test_registered_qwen_artifact_exports_text_and_reuses_without_external_slices(self):
        native = Mock(return_value=_result())
        process = Mock()
        process.run.side_effect = AssertionError("Qwen must not create external slices")
        process.version.side_effect = AssertionError("Direct Qwen must not need ffmpeg")
        with TemporaryDirectory() as temp, patch.dict("sys.modules", {"mlx_qwen3_asr": SimpleNamespace(transcribe=native)}), \
             patch.object(MlxQwen3AsrBackend, "version", return_value="0.4.4"), \
             patch("bmlsub.transcription.core._resolve_model", return_value=("snapshot", {"revision": "test"})):
            root = Path(temp)
            wav, store = _audio(root)
            kwargs = dict(workspace=root, episode_id="17", audio_artifact_id="audio", store=store,
                          runner=process, options=TranscriptionOptions(engine="qwen3"))
            first = run_transcription(**kwargs)
            self.assertIs(first.status, StageStatus.SUCCEEDED)
            payload = json.loads(first.artifacts[0].path.read_text())
            self.assertEqual(payload["timestamp_granularity"], "audio_chunk")
            self.assertEqual(first.artifacts[0].metadata["engine"], "qwen3")
            second = run_transcription(**kwargs)
            self.assertTrue(second.reused)
            native.assert_called_once()
            self.assertEqual(native.call_args.args, (str(wav.resolve()),))
            self.assertFalse((root / "workstation/preprocess/audio/chunks").exists())
            exported = run_transcript_text_export(
                workspace=root, episode_id="17", transcript_artifact_id=first.artifacts[0].artifact_id,
                mode="direct", model=DEFAULT_QWEN3_MODEL, job_name="qwen3", store=store,
            )
            self.assertEqual(exported["status"], "succeeded")
            self.assertEqual(Path(exported["artifacts"][0]["path"]).read_text(), "こんにちは。\n")

    def test_missing_dependency_is_detected_before_model_download(self):
        with TemporaryDirectory() as temp, patch.object(MlxQwen3AsrBackend, "version", return_value="unavailable"), \
             patch("bmlsub.transcription.core._resolve_model") as resolve:
            root = Path(temp)
            _, store = _audio(root)
            with self.assertRaisesRegex(BmlsubError, "mlx-qwen3-asr"):
                run_transcription(workspace=root, episode_id="17", audio_artifact_id="audio",
                                  store=store, options=TranscriptionOptions(engine="qwen3"))
            resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
