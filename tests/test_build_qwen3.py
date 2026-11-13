from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

from bmlsub.media.models import MediaStreamSummary, MediaSummary
from bmlsub.workstation.build import BuildContext, _execute_whisper, execute_plan, plan_operation


QWEN_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
MEDIUM_MODEL = "mlx-community/whisper-medium-mlx"
TURBO_MODEL = "mlx-community/whisper-large-v3-turbo"


def _write_wave(path: Path, duration: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * int(16000 * duration))


class _Probe:
    def inspect_media(self, path):
        return MediaSummary("matroska", 1000, (
            MediaStreamSummary(0, "video", "h264"),
            MediaStreamSummary(1, "audio", "aac", language="jpn", is_default=True),
        ))


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, args):
        self.calls.append(list(args))
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".wav":
            _write_wave(target)
        else:
            target.write_bytes(b"archive audio")


class _Backend:
    def __init__(self, engine, calls):
        self.engine = engine
        self.calls = calls

    def transcribe(self, audio_path, *, model, language, decoding):
        self.calls.append((self.engine, model, Path(audio_path), language))
        payload = {"segments": [{"start": 0.0, "end": 0.8, "text": "こんにちは。"}]}
        if self.engine == "qwen3":
            payload["timestamp_granularity"] = "audio_chunk"
            payload["asr_metadata"] = {
                "finish_reason": "eos", "truncated": False, "internal_chunk_count": 1,
                "chunks": [{"start": 0.0, "end": 0.8, "text": "こんにちは。", "chunk_index": 0}],
            }
        return payload


class BuildQwen3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "17.mkv"
        self.source.write_bytes(b"video fixture")

    def plan(self, options=None):
        return plan_operation("trans", self.root, options=options, probe=_Probe())

    @staticmethod
    def transcripts(plan):
        return [item for item in plan["mappings"][0]["outputs"]
                if item.get("kind") == "transcript"]

    def test_default_plan_is_full_and_preserves_requested_model_order(self):
        plan = self.plan()
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["options"]["strategy"], "full")
        self.assertEqual(
            [(item["engine"], item["model"], item["mode"]) for item in self.transcripts(plan)],
            [("whisper", MEDIUM_MODEL, "direct"), ("whisper", TURBO_MODEL, "chunked"),
             ("qwen3", QWEN_MODEL, "direct")],
        )
        self.assertFalse((self.root / ".bmlsub").exists())

    def test_qwen3_strategy_plans_only_qwen_with_audio_chunk_times(self):
        outputs = self.transcripts(self.plan({"strategy": "qwen3"}))
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["engine"], "qwen3")
        self.assertEqual(outputs[0]["model"], QWEN_MODEL)
        self.assertEqual(outputs[0]["mode"], "direct")
        self.assertEqual(outputs[0]["timestamp_granularity"], "audio_chunk")
        self.assertNotIn("chunk_seconds", outputs[0])

    def test_none_and_quick_keep_their_existing_meaning(self):
        self.assertEqual(self.transcripts(self.plan({"strategy": "none"})), [])
        outputs = self.transcripts(self.plan({"strategy": "quick"}))
        self.assertEqual([(item["engine"], item["model"]) for item in outputs],
                         [("whisper", MEDIUM_MODEL)])

    def test_custom_engine_defaults_to_whisper_and_accepts_qwen_direct(self):
        legacy = self.transcripts(self.plan({"strategy": "custom", "model": "custom/model"}))
        self.assertEqual(legacy[0]["engine"], "whisper")
        qwen = self.transcripts(self.plan({
            "strategy": "custom", "engine": "qwen3", "model": QWEN_MODEL,
        }))
        self.assertEqual(qwen[0]["engine"], "qwen3")
        self.assertEqual(qwen[0]["timestamp_granularity"], "audio_chunk")
        self.assertEqual(qwen[0]["path"], self.transcripts(self.plan({"strategy": "qwen3"}))[0]["path"])

    def test_custom_qwen_external_chunking_is_blocked(self):
        plan = self.plan({"strategy": "custom", "engine": "qwen3", "model": QWEN_MODEL,
                          "transcription_mode": "chunked"})
        self.assertEqual(plan["status"], "needs_review")
        self.assertIn("qwen3_requires_direct", [item["code"] for item in plan["blockers"]])
        self.assertEqual(self.transcripts(plan), [])

    def test_invalid_engine_is_blocked_before_execution(self):
        plan = self.plan({"strategy": "custom", "engine": "unknown", "model": "custom/model"})
        self.assertEqual(plan["status"], "needs_review")
        self.assertIn("transcription_engine_invalid", [item["code"] for item in plan["blockers"]])

    def test_full_execution_is_serial_and_qwen_receives_whole_audio(self):
        plan = self.plan()
        calls = []
        runner = _Runner()
        with patch("bmlsub.transcription.create_backend",
                   side_effect=lambda engine: _Backend(engine, calls)):
            result = execute_plan(plan, runner=runner)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual([(engine, model) for engine, model, _, _ in calls],
                         [("whisper", MEDIUM_MODEL), ("whisper", TURBO_MODEL), ("qwen3", QWEN_MODEL)])
        self.assertEqual(calls[0][2], self.root / "17.transcribe.wav")
        self.assertEqual(calls[2][2], calls[0][2])
        self.assertEqual(calls[1][2].name, "chunk-0000.wav")
        self.assertEqual(sum("-ss" in args for args in runner.calls), 1)
        payloads = [json.loads(Path(item["path"]).read_text(encoding="utf-8"))
                    for item in self.transcripts(plan)]
        self.assertEqual([item["engine"] for item in payloads], ["whisper", "whisper", "qwen3"])
        self.assertEqual([item["timestamp_granularity"] for item in payloads],
                         ["segment", "segment", "audio_chunk"])
        self.assertEqual(payloads[-1]["segments"], [{"start": 0.0, "end": 0.8, "text": "こんにちは。"}])
        self.assertEqual(payloads[-1]["asr_metadata"], {
            "finish_reason": "eos", "truncated": False, "internal_chunk_count": 1,
            "chunks": [{"start": 0.0, "end": 0.8, "text": "こんにちは。", "chunk_index": 0}],
        })

    def test_old_plan_without_engine_still_runs_whisper(self):
        plan = self.plan({"strategy": "quick"})
        output = self.transcripts(plan)[0]
        output.pop("engine")
        calls = []
        with patch("bmlsub.transcription.create_backend",
                   side_effect=lambda engine: _Backend(engine, calls)):
            result = execute_plan(plan, runner=_Runner())
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(calls[0][0], "whisper")
        saved = json.loads(Path(output["path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["engine"], "whisper")
        self.assertEqual(saved["timestamp_granularity"], "segment")

    def test_qwen_only_execution_does_not_create_external_chunks(self):
        plan = self.plan({"strategy": "qwen3", "chunk_seconds": 0.3, "overlap_seconds": 0.1})
        runner = _Runner()
        calls = []
        with patch("bmlsub.transcription.create_backend",
                   side_effect=lambda engine: _Backend(engine, calls)):
            result = execute_plan(plan, runner=runner)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], self.root / "17.transcribe.wav")
        self.assertFalse(any("-ss" in args for args in runner.calls))
        self.assertFalse((BuildContext(self.root).root / "chunks").exists())

    def test_qwen_external_chunking_is_also_rejected_for_saved_plan(self):
        outputs = self.transcripts(self.plan({"strategy": "qwen3"}))
        outputs[0]["mode"] = "chunked"
        wav = self.root / "audio.wav"
        _write_wave(wav)
        with patch("bmlsub.transcription.create_backend") as factory:
            with self.assertRaisesRegex(ValueError, "Qwen3 requires direct"):
                _execute_whisper(wav, outputs, _Runner(), BuildContext(self.root), False)
        factory.assert_not_called()
        self.assertFalse(Path(outputs[0]["path"]).exists())

    def test_missing_qwen_dependency_is_reported_as_failed_not_skipped(self):
        plan = self.plan({"strategy": "qwen3"})
        backend = _Backend("qwen3", [])
        with patch("bmlsub.transcription.create_backend", return_value=backend), \
                patch.object(backend, "transcribe",
                             side_effect=ImportError("mlx-qwen3-asr is required for transcription")):
            result = execute_plan(plan, runner=_Runner())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["items"][0]["status"], "failed")
        self.assertIn("mlx-qwen3-asr is required", result["items"][0]["error"]["message"])
        self.assertFalse(Path(self.transcripts(plan)[0]["path"]).exists())

    def test_repetition_stop_warns_in_output_and_receipt_without_changing_text(self):
        plan = self.plan({"strategy": "qwen3"})
        backend = _Backend("qwen3", [])
        raw = backend.transcribe(Path("audio.wav"), model=QWEN_MODEL, language="ja", decoding={})
        raw["asr_metadata"].update({
            "finish_reason": "repetition", "review_required_chunk_indices": [0],
        })
        with patch("bmlsub.transcription.create_backend", return_value=backend), \
                patch.object(backend, "transcribe", return_value=raw):
            result = execute_plan(plan, runner=_Runner())
        self.assertEqual(result["status"], "succeeded")
        completed = next(item for item in result["items"][0]["outputs"]
                         if item.get("kind") == "transcript")
        warning = {
            "code": "qwen3_repetition_stop",
            "message": "Qwen3 stopped repeated generation; review the affected audio chunks",
            "level": "warning", "context": {"chunk_indices": [0]},
        }
        self.assertEqual(completed["diagnostics"], [warning])
        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        recorded = next(item for item in receipt["items"][0]["outputs"]
                        if item.get("kind") == "transcript")
        self.assertEqual(recorded["diagnostics"], [warning])
        transcript = json.loads(Path(completed["path"]).read_text(encoding="utf-8"))
        self.assertEqual(transcript["text"], "こんにちは。")
        self.assertEqual(transcript["segments"], raw["segments"])
        self.assertFalse(transcript["asr_metadata"]["truncated"])


if __name__ == "__main__":
    unittest.main()
