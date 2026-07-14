from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from bmlsub.cli import build_parser, main, serialize_result
from bmlsub.config import PROJECT_CONFIG_FILENAME
from bmlsub.pipeline import StageStatus


class CliTests(unittest.TestCase):
    def test_serialize_result_handles_paths_and_summaries(self) -> None:
        result = serialize_result({"path": Path("sample.mkv"), "stage": StageStatus("inspect", True)})

        self.assertEqual(result, {
            "path": "sample.mkv",
            "stage": {
                "name": "inspect",
                "ready": True,
                "missing": [],
                "outputs": [],
                "notes": [],
            },
        })

    def test_inspect_episode_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "episode", "inspect",
                    "--episode-dir", directory,
                    "--episode-id", "01",
                ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["episode_id"], "01")
        self.assertEqual(stderr.getvalue(), "")

    def test_workstation_shortcut_parses_episode_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "workstation", "inspect",
                    "--root-dir", directory,
                    "--episodes", "01-03",
                ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["episode_ids"], ["01", "02", "03"])
        self.assertFalse(payload["stage0"]["ready"])

    def test_encode_missing_source_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "episode", "encode",
                    "--episode-dir", directory,
                    "--episode-id", "01",
                ])

        payload = json.loads(stderr.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertIn("源文件不存在", payload["error"])
        self.assertEqual(stdout.getvalue(), "")
    def test_process_local_only_maps_shortcuts_and_manual_cuts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch("bmlsub.cli.Pipeline.process_episode", return_value={"ok": True}) as process:
                with redirect_stdout(stdout):
                    exit_code = main([
                        "episode", "run",
                        "--episode-dir", directory,
                        "--episode-id", "01",
                        "--manual-cut", "01:30",
                        "--manual-cut", "22:00",
                        "--local-only",
                    ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"ok": True})
        kwargs = process.call_args.kwargs
        self.assertEqual(kwargs["manual_cuts"], {"01": ["01:30", "22:00"]})
        self.assertTrue(kwargs["skip_upload"])
        self.assertTrue(kwargs["skip_seed"])

    def test_detailed_workstation_command_dispatches_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch("bmlsub.cli.Pipeline.plan_workstation", return_value={"planned": True}) as plan:
                with redirect_stdout(stdout):
                    exit_code = main([
                        "plan-workstation",
                        "--root-dir", directory,
                        "--episodes", "03-01",
                    ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"planned": True})
        workstation = plan.call_args.args[0]
        self.assertEqual(workstation.episode_ids, ["03", "02", "01"])

    def test_grouped_commands_build_help(self) -> None:
        parser = build_parser()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(subparsers.choices), {"episode", "workstation", "upload", "seed", "config"})

        episode_parser = subparsers.choices["episode"]
        episode_actions = next(
            action for action in episode_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(set(episode_actions.choices), {
            "inspect", "plan", "audio", "subs", "media", "transcribe",
            "encode", "validate", "analyze-ass", "package", "run",
        })

        workstation_parser = subparsers.choices["workstation"]
        workstation_actions = next(
            action for action in workstation_parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertEqual(
            set(workstation_actions.choices),
            {"inspect", "plan", "validate", "encode", "release"},
        )

    def test_legacy_long_command_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "inspect-episode",
                    "--episode-dir", directory,
                    "--episode-id", "01",
                ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["episode_id"], "01")

    def test_legacy_episode_shortcut_defaults_to_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch("bmlsub.cli.Pipeline.process_episode", return_value={"ok": True}) as process:
                with redirect_stdout(stdout):
                    exit_code = main([
                        "episode",
                        "--episode-dir", directory,
                        "--episode-id", "01",
                        "--local-only",
                    ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})
        self.assertTrue(process.call_args.kwargs["skip_upload"])
        self.assertTrue(process.call_args.kwargs["skip_seed"])

    def test_legacy_workstation_actions_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch("bmlsub.cli.Pipeline.build_release_batch", return_value={"ready": True}) as release:
                with redirect_stdout(stdout):
                    exit_code = main([
                        "workstation", "build-release-batch",
                        "--root-dir", directory,
                        "--episodes", "01",
                    ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ready": True})
        self.assertEqual(release.call_args.args[0].episode_ids, ["01"])

    def test_config_init_show_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "config", "init",
                    "--group", "测试组",
                    "--name-chs", "简体作品",
                    "--name-cht", "繁體作品",
                    "--romaji", "Test Romaji",
                    "--episodes", "01-03",
                    "--r2-prefix", "release/season1",
                    "--bgm-id", "123",
                    "--notes", "首发",
                    "--qb-host", "http://qb:8080",
                ])
            self.assertEqual(exit_code, 0)
            config_path = Path(directory) / PROJECT_CONFIG_FILENAME
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("测试组", text)
            self.assertNotIn("secret", text.lower())
            self.assertEqual(json.loads(text)["episodes"], ["01", "02", "03"])

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(["config", "show"]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["release"]["bgm_id"], 123)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(["config", "update", "--notes", "修订"]), 0)
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["release"]["notes"], "修订")
            self.assertEqual(updated["project"]["group"], "测试组")
            self.assertEqual(updated["episodes"], ["01", "02", "03"])

    def test_config_init_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            self.assertEqual(main(["config", "init", "--group", "A"]), 0)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["config", "init", "--group", "B"]), 1)
            self.assertIn("已存在", json.loads(stderr.getvalue())["error"])
            self.assertEqual(main(["config", "init", "--group", "B", "--force"]), 0)
            payload = json.loads((Path(directory) / PROJECT_CONFIG_FILENAME).read_text())
            self.assertEqual(payload["project"]["group"], "B")

    def test_episode_uses_config_and_cli_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            self.assertEqual(main([
                "config", "init", "--group", "配置组", "--name-chs", "配置作品",
                "--name-cht", "配置繁體", "--romaji", "Configured", "--episodes", "07",
            ]), 0)
            with patch("bmlsub.cli.Pipeline.inspect_episode", return_value={"ok": True}) as inspect:
                self.assertEqual(main(["episode", "inspect"]), 0)
            kwargs = inspect.call_args.kwargs
            self.assertEqual(kwargs["episode_id"], "07")
            self.assertEqual(kwargs["project"].group, "配置组")

            with patch("bmlsub.cli.Pipeline.inspect_episode", return_value={"ok": True}) as inspect:
                self.assertEqual(main([
                    "episode", "inspect", "--episode-id", "08", "--group", "命令组"
                ]), 0)
            kwargs = inspect.call_args.kwargs
            self.assertEqual(kwargs["episode_id"], "08")
            self.assertEqual(kwargs["project"].group, "命令组")

    def test_workstation_and_release_fields_use_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            self.assertEqual(main([
                "config", "init", "--episodes", "01-02", "--r2-prefix", "r2/path",
                "--bgm-id", "42", "--notes", "note", "--qb-host", "http://qb:8080",
            ]), 0)
            with patch("bmlsub.cli.Pipeline.inspect_workstation", return_value={"ok": True}) as inspect:
                self.assertEqual(main(["workstation", "inspect"]), 0)
            workstation = inspect.call_args.args[0]
            self.assertEqual(workstation.root_dir, Path(directory).resolve())
            self.assertEqual(workstation.episode_ids, ["01", "02"])
            self.assertEqual(workstation.r2_prefix, "r2/path")
            self.assertEqual(workstation.bgm_id, 42)
            self.assertEqual(workstation.notes, "note")

            with patch("bmlsub.cli.Pipeline.process_episode", return_value={"ok": True}) as process:
                self.assertEqual(main(["episode", "run", "--episode-id", "01", "--local-only"]), 0)
            self.assertEqual(process.call_args.kwargs["r2_prefix"], "r2/path")
            self.assertEqual(process.call_args.kwargs["qb_host"], "http://qb:8080")

    def test_multiple_config_episodes_do_not_guess_single_episode(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            self.assertEqual(main(["config", "init", "--episodes", "01-02"]), 0)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["episode", "encode"])
            self.assertEqual(exit_code, 1)
            self.assertIn("缺少 episode_id", json.loads(stderr.getvalue())["error"])

    def test_invalid_config_returns_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            (Path(directory) / PROJECT_CONFIG_FILENAME).write_text("{broken", encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["episode", "inspect", "--episode-id", "01"])
            self.assertEqual(exit_code, 1)
            self.assertIn("JSON 无效", json.loads(stderr.getvalue())["error"])

    def test_upload_and_seed_use_release_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            self.assertEqual(main([
                "config", "init", "--r2-prefix", "stored/path", "--qb-host", "http://qb:8080"
            ]), 0)
            with patch("bmlsub.cli.Pipeline.upload_files_to_r2", return_value={"ok": True}) as upload:
                self.assertEqual(main(["upload", "release.mkv"]), 0)
            self.assertEqual(upload.call_args.kwargs["remote_folder"], "stored/path")

            with patch("bmlsub.cli.Pipeline.seed_torrents", return_value={"ok": True}) as seed:
                self.assertEqual(main(["seed", "release.mkv"]), 0)
            self.assertEqual(seed.call_args.kwargs["qb_host"], "http://qb:8080")

    def test_config_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch("pathlib.Path.cwd", return_value=Path(directory)):
            path = Path(directory) / PROJECT_CONFIG_FILENAME
            path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["config", "show"])
            self.assertEqual(exit_code, 1)
            self.assertIn("schema_version", json.loads(stderr.getvalue())["error"])

    def test_analyze_ass_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.ass"
            source.write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,main-CN,,0,0,0,,{\\b1}测试\n",
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["episode", "analyze-ass", "--ass-file", str(source)])

            payload = json.loads(stdout.getvalue())
            output = Path(payload["output"])
            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            self.assertEqual(payload["analysis"]["summary"]["language_counts"]["zh"], 1)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["languages"]["zh"][0]["text"],
                "测试",
            )

    def test_validate_passes_full_file_hanvert_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("bmlsub.cli.Pipeline.validate_subtitles", return_value={"ok": True}) as validate:
                self.assertEqual(main([
                    "episode", "validate",
                    "--episode-dir", directory,
                    "--episode-id", "01",
                    "--ensure-cht",
                    "--full-file-hanvert",
                    "--no-full-file-fallback",
                ]), 0)
        self.assertTrue(validate.call_args.kwargs["full_file"])
        self.assertFalse(validate.call_args.kwargs["fallback_to_full_file"])

    def test_unexpected_runtime_error_is_json(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("bmlsub.cli.Pipeline.inspect_episode", side_effect=OSError("broken pipe")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["episode", "inspect", "--episode-id", "01"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "broken pipe"})


if __name__ == "__main__":
    unittest.main()
