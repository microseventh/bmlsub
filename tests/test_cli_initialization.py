from __future__ import annotations

from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import io
import json
import unittest

from bmlsub.cli import (
    _prompt_markdown_notes, _prompt_series_initialization_mode,
    _legacy_build_parser, _workstation_start,
)
from bmlsub.interactive import set_ui_language


class CliInitializationTests(unittest.TestCase):
    def test_initialization_menu_defaults_to_questions_and_supports_template(self):
        set_ui_language("zh")
        output = io.StringIO()
        with redirect_stderr(output), patch("bmlsub.cli._prompt_stderr", return_value=""):
            self.assertEqual(_prompt_series_initialization_mode(), "questions")
        self.assertIn("1. 通过问答创建", output.getvalue())
        self.assertIn("2. 生成可手动填写", output.getvalue())

        with redirect_stderr(io.StringIO()), patch("bmlsub.cli._prompt_stderr", return_value="2"):
            self.assertEqual(_prompt_series_initialization_mode(), "template")

    def test_tty_template_choice_writes_only_template(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = _legacy_build_parser().parse_args([
                "workstation", "start", "--series-root", str(root),
            ])
            with redirect_stderr(io.StringIO()), \
                 patch("bmlsub.cli._ensure_ui_language"), \
                 patch("bmlsub.cli.sys.stdin.isatty", return_value=True), \
                 patch("bmlsub.cli._prompt_series_initialization_mode", return_value="template"):
                result = _workstation_start(args)

            template = root / "bgminfo" / "series.template.json"
            self.assertEqual(Path(result["template_path"]), template.resolve())
            self.assertEqual(result["status"], "needs_review")
            self.assertTrue(template.is_file())
            self.assertFalse((root / "bgminfo" / "series.json").exists())
            self.assertIn("series.title_chs", result["template_guide"]["required_fields"])

    def test_numeric_standalone_start_writes_template_locally(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "Project"
            episode = root / "04"
            episode.mkdir(parents=True)
            args = _legacy_build_parser().parse_args([
                "workstation", "start", "--series-root", str(episode),
            ])
            with patch("bmlsub.cli._ensure_ui_language"), \
                 patch("bmlsub.cli.sys.stdin.isatty", return_value=True), \
                 patch("bmlsub.cli._prompt_series_initialization_mode", return_value="template"):
                result = _workstation_start(args)

            expected = episode / "bgminfo" / "series.template.json"
            self.assertEqual(Path(result["series_root"]), episode.resolve())
            self.assertEqual(Path(result["template_path"]), expected.resolve())
            self.assertTrue(expected.is_file())
            self.assertFalse((episode / "bgminfo" / "series.json").exists())

    def test_markdown_notes_accept_plain_single_line_without_lowercasing(self):
        note = "Release **RC1**: Keep Original Case"
        with patch("bmlsub.cli._prompt_stderr", return_value=note):
            self.assertEqual(_prompt_markdown_notes(), note)

        with patch("bmlsub.cli._prompt_stderr", return_value="bad\x00note"):
            with self.assertRaisesRegex(ValueError, "NUL"):
                _prompt_markdown_notes()

    def test_start_auto_promotes_completed_template_before_inspection(self):
        with TemporaryDirectory() as temporary:
            episode = Path(temporary) / "04"
            episode.mkdir()
            template = episode / "bgminfo" / "series.template.json"
            from bmlsub.workstation import write_series_metadata_template
            write_series_metadata_template(episode)
            payload = json.loads(template.read_text(encoding="utf-8"))
            payload["series"]["title_chs"] = "测试"
            payload["series"]["romanized_title"] = "Test"
            payload["groups"]["chs"] = "BML"
            template.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            args = _legacy_build_parser().parse_args([
                "workstation", "start", "--series-root", str(episode),
            ])

            with redirect_stderr(io.StringIO()), \
                 patch("bmlsub.cli._ensure_ui_language"), \
                 patch("bmlsub.cli.sys.stdin.isatty", return_value=False):
                result = _workstation_start(args)

            self.assertFalse(template.exists())
            self.assertTrue((episode / "bgminfo" / "series.json").is_file())
            self.assertEqual(result["next_action"], "retry_traditionalization")


if __name__ == "__main__":
    unittest.main()
