from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmlsub.cli import _compact_ws_end, _compact_ws_start, build_parser, main
from bmlsub.media.models import MediaStreamSummary, MediaSummary
from bmlsub.state.sqlite_store import SQLiteJobStore
from bmlsub.workstation import (
    DeliverySelection, create_series_metadata, plan_delivery_execution,
    run_delivery, validate_translation_delivery,
)
from bmlsub.workstation.state import load_manifest, step_payload, write_step


ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 240

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,EndTest,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.20,Default,,0,0,0,,A
"""


def make_font(target: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((300, 700))
    pen.lineTo((500, 0))
    pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (600, 0), "A": (600, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "EndTest", "styleName": "Regular",
                           "uniqueFontIdentifier": "EndTest-Regular",
                           "fullName": "EndTest Regular", "psName": "EndTest-Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200,
                    usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(target)


class WorkstationEndTests(unittest.TestCase):
    def make_episode(self, root: Path, *, standalone=False) -> Path:
        create_series_metadata(
            root.name, parent_dir=root.parent, title_chs="测试", title_cht="測試",
            romanized_title="Test", group_chs="BML", group_cht="BML", bgm_id=123,
        )
        episode = root if standalone else root / "13"
        episode.mkdir(exist_ok=True)
        (episode / "13.mkv").write_bytes(b"video fixture")
        (episode / "13.chs&jpn.ass").write_text(ASS, encoding="utf-8")
        make_font(episode / "EndTest.ttf")
        return episode.resolve()

    def args(self, yes=True):
        return build_parser().parse_args(["ws", "end"] + (["yes"] if yes else []))

    def guard_start(self, stack: ExitStack) -> None:
        for target in (
            "bmlsub.cli._workstation_start",
            "bmlsub.workstation.inspect_episode_stage",
            "bmlsub.workstation.execute_recommended_action",
            "bmlsub.workstation.run_preprocess",
            "bmlsub.workstation.preprocess.run_preprocess",
        ):
            stack.enter_context(patch(target, side_effect=AssertionError("end called start workflow")))

    def run_end(self, launch: Path, *, yes=True, tty=False, local_result=None,
                publish_ready=True, confirm=True):
        with ExitStack() as stack:
            self.guard_start(stack)
            stack.enter_context(redirect_stderr(io.StringIO()))
            stack.enter_context(patch("pathlib.Path.cwd", return_value=launch))
            stack.enter_context(patch("bmlsub.cli.sys.stdin.isatty", return_value=tty))
            if yes:
                stack.enter_context(patch("bmlsub.cli._ensure_ui_language",
                                          side_effect=AssertionError("yes opened language menu")))
                stack.enter_context(patch("bmlsub.cli._prompt_stderr",
                                          side_effect=AssertionError("yes prompted for input")))
                stack.enter_context(patch("bmlsub.cli._confirm_stderr",
                                          side_effect=AssertionError("yes asked for confirmation")))
            else:
                language = stack.enter_context(patch("bmlsub.cli._ensure_ui_language"))
                stack.enter_context(patch("bmlsub.cli._prompt_delivery_selection",
                                          return_value=DeliverySelection.for_scope("full")))
                stack.enter_context(patch("bmlsub.cli._confirm_stderr", return_value=confirm))
                stack.enter_context(patch("bmlsub.cli._select_nyaa_syndication", return_value=False))
            media = MediaSummary("matroska", 250, (
                MediaStreamSummary(0, "video", "h264", width=320, height=240),
            ))
            stack.enter_context(patch("bmlsub.media.probe.FFprobeClient.version", return_value="test-ffprobe"))
            stack.enter_context(patch("bmlsub.media.probe.FFprobeClient.inspect", return_value=media))

            def validate_local(episode, **kwargs):
                result = validate_translation_delivery(episode, episode_id=kwargs["episode_id"])
                self.assertIn(result["status"], {"succeeded", "skipped"})
                return {"status": "succeeded"}

            local = stack.enter_context(patch(
                "bmlsub.workstation.run_delivery",
                side_effect=validate_local if local_result is None else None,
                return_value=local_result,
            ))
            plan = {"status": "succeeded" if publish_ready else "failed",
                    "missing": [] if publish_ready else ["publication_identity"]}
            stack.enter_context(patch("bmlsub.workstation.plan_publish", return_value=plan))
            stack.enter_context(patch("bmlsub.cli._delivery_credential_status",
                                      return_value={"status": "succeeded"}))
            stack.enter_context(patch("bmlsub.cli._print_delivery_credentials"))
            stack.enter_context(patch("bmlsub.cli._print_publish_plan"))
            publish = stack.enter_context(patch("bmlsub.workstation.run_publish",
                                                return_value={"status": "succeeded"}))
            result = _compact_ws_end(self.args(yes))
            if not yes:
                language.assert_called_once()
            return result, local, publish

    def test_fresh_end_initializes_without_start_in_both_modes(self):
        for yes in (False, True):
            with self.subTest(yes=yes), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                self.assertFalse((episode / "workstation").exists())
                result, local, publish = self.run_end(episode, yes=yes, tty=True)
                self.assertEqual(result["status"], "succeeded")
                local.assert_called_once()
                publish.assert_called_once()
                manifest = load_manifest(episode)
                self.assertTrue(manifest["source"]["video_artifact_id"])
                self.assertTrue(manifest["subtitles"]["chs_source_artifact_id"])
                self.assertEqual(len(manifest["fonts"]["artifact_ids"]), 1)
                self.assertFalse(list((episode / "workstation/state/steps").glob("preprocess.*")))

    def test_all_preprocess_states_are_ignored_and_history_is_preserved(self):
        for yes in (False, True):
            for status in (None, "succeeded", "failed", "needs_review", "blocked",
                           "running", "interrupted", "partial"):
                with self.subTest(yes=yes, status=status), TemporaryDirectory() as temporary:
                    episode = self.make_episode(Path(temporary) / "Series")
                    state = episode / "workstation/state"
                    state.mkdir(parents=True)
                    (state / "manifest.json").write_text(json.dumps({
                        "source": {"video_artifact_id": "historical-id-not-in-database"},
                    }))
                    (state / "summary.json").write_text(json.dumps({
                        "preprocess": {"status": status},
                    }))
                    failed_step = step_payload(
                        workflow_id="episode-13", phase="preprocess",
                        step="preprocess.extract_reference_subtitles", status=status or "pending",
                    )
                    history = write_step(episode, failed_step)
                    before = history.read_bytes()
                    result, local, publish = self.run_end(episode, yes=yes, tty=True)
                    self.assertEqual(result["status"], "succeeded")
                    local.assert_called_once()
                    publish.assert_called_once()
                    self.assertEqual(history.read_bytes(), before)
                    self.assertNotEqual(load_manifest(episode)["source"]["video_artifact_id"],
                                        "historical-id-not-in-database")

    def test_missing_inputs_report_the_actual_problem_without_start(self):
        for missing, message in (("13.mkv", "source video"),
                                 ("13.chs&jpn.ass", "production subtitle"),
                                 ("EndTest.ttf", "font package")):
            with self.subTest(missing=missing), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                (episode / missing).unlink()
                result, local, publish = self.run_end(episode, tty=True)
                self.assertEqual(result["status"], "needs_review")
                self.assertIn(message, json.dumps(result["error"]["details"]))
                self.assertNotIn("ws start", json.dumps(result))
                local.assert_not_called()
                publish.assert_not_called()
                self.assertFalse((episode / "workstation").exists())

    def test_ambiguous_video_does_not_create_state_or_publish(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            (episode / "second.mp4").write_bytes(b"second")
            result, local, publish = self.run_end(episode)
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["error"]["details"][0]["code"], "source_video_ambiguous")
            local.assert_not_called()
            publish.assert_not_called()
            self.assertFalse((episode / "workstation").exists())

    def test_lone_ai_subtitle_is_not_a_formal_handoff(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            (episode / "13.chs&jpn.ass").rename(episode / "13.chs&jpn.ai.ass")
            result, local, publish = self.run_end(episode)
            self.assertEqual(result["status"], "needs_review")
            local.assert_not_called()
            publish.assert_not_called()

    def test_local_failure_never_starts_publication(self):
        for status in ("failed", "needs_review", "partial", "interrupted"):
            with self.subTest(status=status), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                result, local, publish = self.run_end(episode, local_result={"status": status})
                self.assertEqual(result["status"], status)
                local.assert_called_once()
                publish.assert_not_called()

    def test_start_still_stops_at_formal_handoff(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            with patch("pathlib.Path.cwd", return_value=episode), \
                 patch("bmlsub.cli._ensure_ui_language"), \
                 patch("bmlsub.workstation.run_delivery", side_effect=AssertionError("start produced")), \
                 patch("bmlsub.workstation.run_publish", side_effect=AssertionError("start published")), \
                 redirect_stderr(io.StringIO()):
                result = _compact_ws_start(build_parser().parse_args(["ws", "start"]))
            self.assertEqual(result["recommended_command"], "bmlsub ws end")
            self.assertFalse((episode / "workstation").exists())

    def test_registration_errors_return_diagnostics_without_indexing_empty_outputs(self):
        for invalid in ("13.chs&jpn.ass", "EndTest.ttf"):
            with self.subTest(invalid=invalid), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                (episode / invalid).write_bytes(b"invalid")
                media = MediaSummary("matroska", 250, (
                    MediaStreamSummary(0, "video", "h264", width=320, height=240),
                ))
                with patch("bmlsub.media.probe.FFprobeClient.version", return_value="test"), \
                     patch("bmlsub.media.probe.FFprobeClient.inspect", return_value=media):
                    result = validate_translation_delivery(episode)
                self.assertNotIn(result["status"], {"succeeded", "skipped"})
                self.assertEqual(result["step"], "translation.validate_delivery")
                self.assertTrue(result["error"] or result["diagnostics"])

    def test_equivalent_requests_reuse_legacy_id_but_changed_contracts_do_not(self):
        from bmlsub.production.models import ProductionOperation
        from bmlsub.production.requests import create_production_request
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            media = MediaSummary("matroska", 250, (
                MediaStreamSummary(0, "video", "h264", width=320, height=240),
            ))
            with patch("bmlsub.media.probe.FFprobeClient.version", return_value="test"), \
                 patch("bmlsub.media.probe.FFprobeClient.inspect", return_value=media):
                validate_translation_delivery(episode)
            store = SQLiteJobStore.for_workspace(episode, episode / "workstation/state")
            values = dict(workspace=episode, episode_id="13", operation=ProductionOperation.ENCODE,
                          video_artifact_id=load_manifest(episode)["source"]["video_artifact_id"],
                          output_target=episode / "output.mkv", store=store)
            legacy = create_production_request(**values)
            same = create_production_request(**values, reuse_existing=True)
            self.assertEqual(same.request_id, legacy.request_id)
            changed = create_production_request(**values, reuse_existing=True, parameters={"quality": 40})
            self.assertNotEqual(changed.request_id, legacy.request_id)
            moved = create_production_request(**dict(values, output_target=episode / "other.mkv"),
                                              reuse_existing=True)
            self.assertNotEqual(moved.request_id, legacy.request_id)
            explicit_new = create_production_request(**values)
            self.assertNotEqual(explicit_new.request_id, legacy.request_id)

    def test_end_without_confirmation_does_not_initialize_or_publish(self):
        for tty in (False, True):
            with self.subTest(tty=tty), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                result, local, publish = self.run_end(episode, yes=False, tty=tty, confirm=False)
                self.assertEqual(result["status"], "awaiting_confirmation")
                local.assert_not_called()
                publish.assert_not_called()
                self.assertFalse((episode / "workstation").exists())

    def test_yes_missing_publish_config_never_opens_configuration_menu(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            result, local, publish = self.run_end(episode, tty=True, publish_ready=False)
            self.assertEqual(result["status"], "needs_review")
            local.assert_called_once()
            publish.assert_not_called()
            self.assertNotIn("ws start", json.dumps(result))

    def test_yes_multiple_episodes_requires_a_directory_without_prompting(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "Series"
            self.make_episode(root)
            (root / "14").mkdir()
            result, local, publish = self.run_end(root.resolve(), tty=True)
            self.assertEqual(result["error"]["code"], "episode_selection_required")
            self.assertEqual(len(result["episodes"]), 2)
            local.assert_not_called()
            publish.assert_not_called()

    def test_standalone_numeric_episode_works_without_parent_metadata(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "13", standalone=True)
            result, local, publish = self.run_end(episode)
            self.assertEqual(result["status"], "succeeded")
            local.assert_called_once()
            publish.assert_called_once()

    def test_yes_missing_metadata_reports_configuration_without_prompting(self):
        with TemporaryDirectory() as temporary:
            episode = Path(temporary) / "13"
            episode.mkdir()
            result, local, publish = self.run_end(episode.resolve(), tty=True)
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["blocking"][0]["code"], "series_metadata_missing")
            self.assertNotIn("ws start", json.dumps(result))
            local.assert_not_called()
            publish.assert_not_called()

    def test_end_promotes_parent_template_without_running_start(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "Series"
            episode = self.make_episode(root)
            (root / "bgminfo/series.json").rename(root / "bgminfo/series.template.json")
            result, local, publish = self.run_end(episode, tty=True)
            self.assertEqual(result["status"], "succeeded")
            local.assert_called_once()
            publish.assert_called_once()
            self.assertTrue((root / "bgminfo/series.json").is_file())
            self.assertFalse((root / "bgminfo/series.template.json").exists())

    def test_cli_missing_inputs_returns_one_json_and_exit_two(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            (episode / "EndTest.ttf").unlink()
            stdout = io.StringIO()
            with ExitStack() as stack:
                self.guard_start(stack)
                stack.enter_context(patch("pathlib.Path.cwd", return_value=episode))
                stack.enter_context(patch("bmlsub.cli.sys.stdin.isatty", return_value=False))
                stack.enter_context(redirect_stdout(stdout))
                stack.enter_context(redirect_stderr(io.StringIO()))
                self.assertEqual(main(["ws", "end", "yes"]), 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "delivery_inputs_incomplete")
            self.assertFalse((episode / "workstation").exists())

    def test_formal_subtitle_overrides_historical_reference_classification(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            formal = episode / "13.chs&jpn.ass"
            state = episode / "workstation/state"
            state.mkdir(parents=True)
            (state / "manifest.json").write_text(json.dumps({"preprocess": {
                "reference_subtitles": [{"delivery_path": str(formal)}],
            }}))
            plan = plan_delivery_execution(episode)
            self.assertEqual(plan["status"], "succeeded")
            self.assertEqual(plan["production_subtitle"], str(formal))

    def test_resume_revalidates_even_with_claimed_completed_products(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            state = episode / "workstation/state"
            state.mkdir(parents=True)
            (state / "manifest.json").write_text(json.dumps({
                "products": {key: "stale" for key in (
                    "hardsub_chs_artifact_id", "hardsub_cht_artifact_id", "muxed_mkv_artifact_id")},
                "torrents": {key: "stale" for key in ("mp4_chs", "mp4_cht", "mkv_hevc")},
                "publish": {"anibt": {"mp4_chs": "old-receipt"}},
            }))
            result, local, publish = self.run_end(episode)
            self.assertEqual(result["status"], "succeeded")
            local.assert_called_once()
            publish.assert_called_once()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "media tools unavailable")
    def test_real_input_registration_reuses_then_replaces_changed_video(self):
        with TemporaryDirectory() as temporary:
            episode = self.make_episode(Path(temporary) / "Series")
            source = episode / "13.mkv"
            for color in ("black", "white"):
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=24",
                    "-t", "0.25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
                ], check=True, capture_output=True, timeout=30)
                result = validate_translation_delivery(episode)
                self.assertIn(result["status"], {"succeeded", "skipped"})
                current_id = load_manifest(episode)["source"]["video_artifact_id"]
                if color == "black":
                    first_id = current_id
                    repeat = validate_translation_delivery(episode)
                    self.assertIn(repeat["status"], {"succeeded", "skipped"})
                    self.assertEqual(load_manifest(episode)["source"]["video_artifact_id"], first_id)
                else:
                    self.assertNotEqual(current_id, first_id)
            store = SQLiteJobStore.for_workspace(episode, episode / "workstation/state")
            self.assertEqual(store.get_artifact(current_id).path, source)
            self.assertFalse(list((episode / "workstation/state/steps").glob("preprocess.*")))

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe", "mkvmerge")),
                         "media tools unavailable")
    def test_full_local_delivery_without_start_and_second_run_reuses_products(self):
        for manual_cht in (True, False):
            with self.subTest(manual_cht=manual_cht), TemporaryDirectory() as temporary:
                episode = self.make_episode(Path(temporary) / "Series")
                chs = episode / "13.chs&jpn.ass"
                chs.write_text(ASS.replace("Default", "CHS").replace(",,A\n", ",,A测试\n"), encoding="utf-8")
                if manual_cht:
                    (episode / "13.cht&jpn.ass").write_text(ASS, encoding="utf-8")
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "0.25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(episode / "13.mkv"),
                ], check=True, capture_output=True, timeout=30)
                with ExitStack() as stack:
                    self.guard_start(stack)
                    stack.enter_context(patch("requests.sessions.Session.request",
                                              side_effect=AssertionError("unexpected network request")))
                    converter = stack.enter_context(patch(
                        "bmlsub.hanvert.fanhuaji_provider",
                        side_effect=lambda text, *args: text.replace("测试", "測試"),
                    ))
                    parameters = {
                        "hevc_parameters": {"video_codec": "libx265", "pixel_format": "yuv420p10le", "quality": 28},
                        "hardsub_parameters": {"preset": "medium"},
                    }
                    first = run_delivery(episode, **parameters)
                    self.assertEqual(first["status"], "succeeded", first)
                    self.assertEqual(set(first["products"]), {"mp4_chs", "mp4_cht", "mkv_hevc"})
                    self.assertEqual(set(first["torrents"]), set(first["products"]))
                    store = SQLiteJobStore.for_workspace(episode, episode / "workstation/state")
                    before = {key: store.get_artifact(value).path.stat().st_mtime_ns
                              for key, value in first["products"].items()}
                    second = run_delivery(episode, **parameters)
                    self.assertEqual(second["status"], "succeeded", second)
                    self.assertEqual(second["products"], first["products"])
                    self.assertEqual(second["torrents"], first["torrents"])
                    self.assertEqual(before, {key: store.get_artifact(value).path.stat().st_mtime_ns
                                              for key, value in second["products"].items()})
                    self.assertEqual(converter.call_count, 0 if manual_cht else 1)
                    # Configuration changes invalidate only dependent products.
                    changed = dict(parameters, hardsub_parameters={"preset": "medium", "crf": 30})
                    third = run_delivery(episode, **changed)
                    self.assertEqual(third["status"], "succeeded", third)
                    self.assertEqual(third["products"]["mkv_hevc"], first["products"]["mkv_hevc"])
                    for key in ("mp4_chs", "mp4_cht"):
                        self.assertNotEqual(third["products"][key], first["products"][key])
                    if not manual_cht:
                        # A human edit to generated CHT becomes a manual input;
                        # resumed conversion must never overwrite that edit.
                        top_cht = episode / "13.cht&jpn.ass"
                        top_cht.write_text(top_cht.read_text().replace("測試", "校对"), encoding="utf-8")
                        edited = run_delivery(episode, **changed)
                        self.assertEqual(edited["status"], "succeeded", edited)
                        self.assertIn("校对", top_cht.read_text())
                        self.assertTrue(load_manifest(episode)["subtitles"]["cht_source_artifact_id"])
                        self.assertEqual(converter.call_count, 1)
                        self.assertEqual(edited["products"]["mp4_chs"], third["products"]["mp4_chs"])
                        self.assertNotEqual(edited["products"]["mp4_cht"], third["products"]["mp4_cht"])
                self.assertFalse(list((episode / "workstation/state/steps").glob("preprocess.*")))



if __name__ == "__main__":
    unittest.main()
