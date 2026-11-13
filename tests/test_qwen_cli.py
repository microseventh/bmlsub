from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmlsub.cli import (
    _legacy_build_parser, _prompt_transcription_mode, _transcribe, _workstation_start,
)
from bmlsub.interactive import set_ui_language
from bmlsub.workstation.commands import _questions
from bmlsub.workstation_summary import format_workstation_summary


QWEN_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"


class QwenCliTests(unittest.TestCase):
    def tearDown(self):
        set_ui_language("zh")

    def test_transcribe_parser_selects_engine_without_forcing_whisper_model(self):
        parser = _legacy_build_parser()
        required = ["transcribe", "--episode-id", "17", "--audio-artifact-id", "audio"]
        whisper = parser.parse_args(required)
        self.assertEqual(whisper.engine, "whisper")
        self.assertIsNone(whisper.model)
        qwen = parser.parse_args(required + ["--engine", "qwen3"])
        self.assertEqual(qwen.engine, "qwen3")
        self.assertEqual(qwen.mode, "direct")
        self.assertIsNone(qwen.model)
        custom = parser.parse_args(required + ["--engine", "qwen3", "--model", "/models/qwen"])
        self.assertEqual(custom.model, "/models/qwen")

    def test_transcribe_dispatches_engine_model_and_progress_label(self):
        args = _legacy_build_parser().parse_args([
            "transcribe", "--episode-id", "17", "--audio-artifact-id", "audio",
            "--engine", "qwen3",
        ])
        payload = {"status": "succeeded", "outputs": []}
        with patch("bmlsub.cli.Pipeline") as pipeline, \
             patch("bmlsub.progress.progress_task") as progress, \
             patch("bmlsub.progress.finish_progress_task"):
            pipeline.return_value.transcribe.return_value = payload
            result = _transcribe(args)
        self.assertIs(result, payload)
        options = pipeline.return_value.transcribe.call_args.kwargs
        self.assertEqual(options["engine"], "qwen3")
        self.assertIsNone(options["model"])
        self.assertEqual(options["mode"], "direct")
        self.assertEqual(progress.call_args.kwargs["step"], "transcription.qwen3")
        self.assertIn("Qwen3-ASR", progress.call_args.kwargs["label"])

    def test_old_handler_namespace_still_uses_whisper(self):
        args = _legacy_build_parser().parse_args([
            "transcribe", "--episode-id", "17", "--audio-artifact-id", "audio",
        ])
        del args.engine
        with patch("bmlsub.cli.Pipeline") as pipeline, \
             patch("bmlsub.progress.progress_task"), \
             patch("bmlsub.progress.finish_progress_task"):
            pipeline.return_value.transcribe.return_value = {"status": "succeeded"}
            _transcribe(args)
        self.assertEqual(pipeline.return_value.transcribe.call_args.kwargs["engine"], "whisper")

    def test_workstation_parsers_accept_qwen_only_and_keep_full_default(self):
        parser = _legacy_build_parser()
        for command in ("start", "rebuild", "preprocess"):
            with self.subTest(command=command):
                args = parser.parse_args(["workstation", command, "--transcription", "qwen3"])
                self.assertEqual(args.transcription, "qwen3")
        self.assertEqual(parser.parse_args(["workstation", "preprocess"]).transcription, "full")

    def test_transcription_menu_preserves_old_choices_and_adds_qwen(self):
        for language in ("zh", "en"):
            set_ui_language(language)
            for choice, expected in (("", "full"), ("1", "quick"), ("2", "full"),
                                     ("3", "none"), ("4", "qwen3"), ("qwen3", "qwen3")):
                with self.subTest(language=language, choice=choice):
                    output = io.StringIO()
                    with redirect_stderr(output), patch("bmlsub.cli._prompt_stderr", return_value=choice):
                        self.assertEqual(_prompt_transcription_mode(), expected)
                    self.assertIn("Whisper medium", output.getvalue())
                    self.assertIn("turbo", output.getvalue())
                    self.assertIn("Qwen3-ASR", output.getvalue())
            with redirect_stderr(io.StringIO()), patch("bmlsub.cli._prompt_stderr", return_value=""):
                self.assertEqual(_prompt_transcription_mode(default="qwen3"), "qwen3")

    def test_workstation_resumes_saved_qwen_policy_and_defaults_to_full_without_tty(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = root / "17"
            args = _legacy_build_parser().parse_args([
                "workstation", "start", "--series-root", str(root),
                "--episode-id", "17", "--execute",
            ])
            for saved_policy, expected in (("qwen3", "qwen3"), (None, "full")):
                inspection = {
                    "episode_dir": str(episode), "episode_id": "17",
                    "detected_phase": "preprocess", "confidence": "high",
                    "evidence": [{"code": "preprocess_incomplete"}] if saved_policy else [],
                    "blocking": [], "executable": True, "recommended_action": "run_preprocess",
                }
                with self.subTest(saved_policy=saved_policy), redirect_stderr(io.StringIO()), \
                     patch("bmlsub.cli._ensure_ui_language"), \
                     patch("bmlsub.cli._prepare_series_workspace", return_value={
                         "status": "succeeded", "series_root": str(root), "episodes": [],
                     }), \
                     patch("bmlsub.cli.sys.stdin.isatty", return_value=False), \
                     patch("bmlsub.workstation.inspect_episode_stage", return_value=inspection), \
                     patch("bmlsub.workstation.start._saved_preprocess_options", return_value={
                         "transcription_policy": saved_policy,
                     }), \
                     patch("bmlsub.workstation.transcription_jobs_for_mode", return_value=("job",)) as jobs, \
                     patch("bmlsub.workstation.execute_recommended_action", return_value={"status": "succeeded"}) as execute:
                    _workstation_start(args)
                jobs.assert_called_once_with(expected)
                self.assertEqual(execute.call_args.kwargs["whisper_jobs"], ("job",))

    def _resume_saved_preprocess(self, saved, *, explicit_policy=None):
        from bmlsub.workstation import transcription_jobs_for_mode

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            argv = ["workstation", "start", "--series-root", str(root),
                    "--episode-id", "17", "--execute"]
            if explicit_policy is not None:
                argv.extend(("--transcription", explicit_policy))
            args = _legacy_build_parser().parse_args(argv)
            inspection = {
                "episode_dir": str(root / "17"), "episode_id": "17",
                "detected_phase": "preprocess", "confidence": "high",
                "evidence": [{"code": "preprocess_incomplete"}],
                "blocking": [], "executable": True, "recommended_action": "run_preprocess",
            }
            with redirect_stderr(io.StringIO()), \
                 patch("bmlsub.cli._ensure_ui_language"), \
                 patch("bmlsub.cli._prepare_series_workspace", return_value={
                     "status": "succeeded", "series_root": str(root), "episodes": [],
                 }), \
                 patch("bmlsub.cli.sys.stdin.isatty", return_value=True), \
                 patch("bmlsub.cli._prompt_transcription_mode", side_effect=AssertionError("unexpected policy prompt")), \
                 patch("bmlsub.workstation.inspect_episode_stage", return_value=inspection), \
                 patch("bmlsub.workstation.start._saved_preprocess_options", return_value=saved), \
                 patch("bmlsub.workstation.transcription_jobs_for_mode", wraps=transcription_jobs_for_mode) as factory, \
                 patch("bmlsub.workstation.execute_recommended_action", return_value={"status": "succeeded"}) as execute:
                _workstation_start(args)
            return execute.call_args.kwargs["whisper_jobs"], factory.call_args_list

    def test_saved_custom_qwen_job_preserves_model_language_and_decoding(self):
        from bmlsub.workstation import TranscriptionJob

        custom = TranscriptionJob(
            name="qwen-custom", engine="qwen3", mode="direct",
            model="/models/qwen-custom", model_revision="custom-revision",
            language="en", chunk_seconds=180.0, overlap_seconds=0.0,
            throttle_seconds=0.5, decoding={"context": "Custom names", "max_new_tokens": 256},
        ).to_dict()
        jobs, factory_calls = self._resume_saved_preprocess({
            "transcription_policy": "custom", "whisper_jobs": (custom,),
        })
        self.assertEqual([job.to_dict() for job in jobs], [custom])
        self.assertEqual(factory_calls, [])

    def test_saved_custom_policy_rejects_empty_or_damaged_jobs(self):
        cases = (
            None, (), {}, ("invalid",), ({"engine": "qwen3"},),
            ({"name": "custom", "engine": "invalid"},),
            ({"name": "custom", "engine": "qwen3", "mode": "chunked"},),
            ({"name": "custom", "unexpected_field": True},),
            ({"name": "custom", "model": []},),
            ({"name": "custom"}, {"name": "custom"}),
        )
        for saved_jobs in cases:
            with self.subTest(saved_jobs=saved_jobs), self.assertRaisesRegex(ValueError, "saved custom transcription"):
                self._resume_saved_preprocess({
                    "transcription_policy": "custom", "whisper_jobs": saved_jobs,
                })

    def test_explicit_policy_overrides_saved_custom_jobs(self):
        for saved_jobs in (({"name": "custom", "engine": "qwen3", "model": "/models/custom"},), ()):
            with self.subTest(saved_jobs=saved_jobs):
                jobs, factory_calls = self._resume_saved_preprocess({
                    "transcription_policy": "custom", "whisper_jobs": saved_jobs,
                }, explicit_policy="qwen3")
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0].name, "qwen3")
                self.assertEqual(jobs[0].engine, "qwen3")
                self.assertEqual(jobs[0].model, QWEN_MODEL)
                self.assertEqual(len(factory_calls), 1)
                self.assertEqual(factory_calls[0].args, ("qwen3",))

    def test_saved_legacy_full_still_expands_to_three_default_models(self):
        from bmlsub.workstation import transcription_jobs_for_mode

        legacy_jobs = [job.to_dict() for job in transcription_jobs_for_mode("full")[:2]]
        jobs, factory_calls = self._resume_saved_preprocess({
            "transcription_policy": "full", "whisper_jobs": tuple(legacy_jobs),
        })
        self.assertEqual([job.name for job in jobs], ["direct", "chunked", "qwen3"])
        self.assertEqual(factory_calls[0].args, ("full",))

    def test_build_trans_defaults_to_full_and_accepts_qwen_only(self):
        for strategy, expected in (("", "full"), ("qwen3", "qwen3"), ("none", "none")):
            answers = iter(["", "", strategy, ""])
            output = io.StringIO()
            with self.subTest(strategy=strategy):
                recursive, options = _questions(
                    "trans", rebuild=False, cwd=Path("."),
                    input_fn=lambda _: next(answers), output=output,
                )
                self.assertFalse(recursive)
                self.assertEqual(options["strategy"], expected)
                self.assertEqual(options["transcription_language"], "ja")
                self.assertIn("Qwen3-ASR", output.getvalue())

    def test_build_custom_transcription_defaults_model_by_engine(self):
        for engine, expected_model in (("", "mlx-community/whisper-large-v3-turbo"),
                                       ("qwen3", QWEN_MODEL)):
            answers = iter(["", "", "custom", "", engine, "", ""])
            with self.subTest(engine=engine):
                _, options = _questions(
                    "trans", rebuild=False, cwd=Path("."),
                    input_fn=lambda _: next(answers), output=io.StringIO(),
                )
                self.assertEqual(options["engine"], engine or "whisper")
                self.assertEqual(options["model"], expected_model)
                self.assertEqual(options["transcription_mode"], "direct")

    def test_build_custom_whisper_chunk_settings_are_preserved(self):
        answers = iter(["", "", "custom", "ja", "whisper", "chunked", "my/model", "120", "3"])
        _, options = _questions(
            "trans", rebuild=True, cwd=Path("."),
            input_fn=lambda _: next(answers), output=io.StringIO(),
        )
        self.assertEqual(options["model"], "my/model")
        self.assertEqual(options["chunk_seconds"], 120.0)
        self.assertEqual(options["overlap_seconds"], 3.0)

    def test_build_custom_qwen_rejects_external_chunking(self):
        answers = iter(["", "", "custom", "", "qwen3", "chunked"])
        with self.assertRaisesRegex(ValueError, "Qwen3-ASR requires direct mode"):
            _questions(
                "trans", rebuild=False, cwd=Path("."),
                input_fn=lambda _: next(answers), output=io.StringIO(),
            )

    def test_summary_labels_qwen_transcription_and_text_export(self):
        payload = {
            "status": "succeeded", "episode_id": "17",
            "summary": {"preprocess": {"status": "succeeded", "steps": {
                "preprocess.transcribe.qwen3": "succeeded",
                "preprocess.export_transcript_text.qwen3": "succeeded",
            }}},
        }
        for language in ("zh", "en"):
            with self.subTest(language=language):
                summary = format_workstation_summary(payload, language=language)
                self.assertGreaterEqual(summary.count("Qwen3-ASR"), 2)
                self.assertNotIn("preprocess.transcribe.qwen3", summary)
                self.assertNotIn("preprocess.export_transcript_text.qwen3", summary)


if __name__ == "__main__":
    unittest.main()
