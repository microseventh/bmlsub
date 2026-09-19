from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmlsub.cli import main
from bmlsub.execution.errors import BmlsubError, OutputValidationError
from bmlsub.traditionalization import run_fanhua


def write_ass(path: Path, text: str, *, style: str = "CHS") -> None:
    path.write_text(
        "[Script Info]\nTitle: Test\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {style},Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:01.00,0:00:02.00,{style},,0,0,0,,{text}\n",
        encoding="utf-8",
    )


def traditional_provider(text: str, converter: str, api_url: str, timeout: int) -> str:
    del converter, api_url, timeout
    return text.translate(str.maketrans({"测": "測", "试": "試", "发": "發"}))


class FanhuaTests(unittest.TestCase):
    def test_cli_dispatches_build_fanhua_and_prints_one_json_document(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cli.chs.ass"
            write_ass(source, "测试")
            stdout = io.StringIO()
            stderr = io.StringIO()

            with patch(
                "bmlsub.hanvert.fanhuaji_provider", side_effect=traditional_provider,
            ), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["build", "fanhua", str(source)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["operation"], "fanhua")
            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("測試", (root / "cli.cht.ass").read_text(encoding="utf-8"))

    def test_file_uses_existing_cht_naming_and_preserves_ass_structure(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "01.CHS&JPN.ass"
            write_ass(source, "{\\pos(10,20)}测试\\N发言")

            payload = run_fanhua(source, launch_directory=root, provider=traditional_provider)

            output = root / "01.cht&jpn.ass"
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(Path(payload["artifacts"][0]["path"]), output.resolve())
            converted = output.read_text(encoding="utf-8")
            self.assertIn("{\\pos(10,20)}測試\\N發言", converted)
            self.assertIn("Style: CHS,Arial", converted)

    def test_directory_is_non_recursive_stable_and_skips_cht_outputs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "subs"
            nested = source_dir / "nested"
            nested.mkdir(parents=True)
            write_ass(source_dir / "b.ass", "测试")
            write_ass(source_dir / "A.CHS.ass", "发言")
            write_ass(source_dir / "A.CHT.ass", "既有繁體", style="CHT")
            write_ass(nested / "c.ass", "测试")

            payload = run_fanhua(source_dir, launch_directory=root, provider=traditional_provider)

            self.assertEqual(payload["processed_count"], 2)
            self.assertEqual(
                [Path(item["source"]).name for item in payload["artifacts"]],
                ["A.CHS.ass", "b.ass"],
            )
            self.assertTrue((source_dir / "A.cht.ass").is_file())
            self.assertTrue((source_dir / "b.cht.ass").is_file())
            self.assertFalse((nested / "c.cht.ass").exists())

    def test_no_chinese_dialogue_is_reported_without_creating_output(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "jp.ass"
            write_ass(source, "日本語です", style="JPN")

            payload = run_fanhua(source, launch_directory=root, provider=traditional_provider)

            self.assertEqual(payload["status"], "skipped")
            self.assertEqual(payload["processed_count"], 0)
            self.assertEqual(payload["skipped_count"], 1)
            self.assertFalse((root / "jp.cht.ass").exists())

    def test_rejects_unsupported_existing_cht_and_output_collision(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = root / "subtitle.srt"
            text.write_text("text", encoding="utf-8")
            with self.assertRaises(BmlsubError):
                run_fanhua(text, launch_directory=root, provider=traditional_provider)

            existing = root / "subtitle.cht.ass"
            write_ass(existing, "繁體", style="CHT")
            with self.assertRaises(BmlsubError):
                run_fanhua(existing, launch_directory=root, provider=traditional_provider)

            collision = root / "collision"
            collision.mkdir()
            write_ass(collision / "same.ass", "测试")
            write_ass(collision / "same.chs.ass", "发言")
            with self.assertRaises(OutputValidationError):
                run_fanhua(collision, launch_directory=root, provider=traditional_provider)
            self.assertFalse((collision / "same.cht.ass").exists())


if __name__ == "__main__":
    unittest.main()
