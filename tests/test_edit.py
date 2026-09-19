from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unicodedata
import unittest

from bmlsub.cli import main
from bmlsub.editing import (
    JAPANESE_SPACE,
    canonicalize_edited_text,
    run_edit,
    validate_edited_text,
)
from bmlsub.execution.errors import BmlsubError, OutputValidationError


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def write_srt(path: Path, *texts: str) -> None:
    blocks = []
    for index, text in enumerate(texts, 1):
        blocks.append(
            f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\n{text}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


class EditTextContractTests(unittest.TestCase):
    def assert_text_contract(self, path: Path, *, expected_lines: int | None = None) -> str:
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8")
        self.assertNotIn("\r", text)
        lines = text.splitlines()
        if expected_lines is not None:
            self.assertEqual(len(lines), expected_lines)
        self.assertTrue(all(lines))
        self.assertTrue(all(line == line.strip() for line in lines))
        self.assertFalse(any(
            unicodedata.category(character)[0] in {"P", "S", "C"}
            for line in lines
            for character in line
        ))
        self.assertTrue(all(
            character == JAPANESE_SPACE or not character.isspace()
            for line in lines
            for character in line
        ))
        self.assertNotIn(" ", text)
        self.assertNotIn(JAPANESE_SPACE * 2, text)
        self.assertTrue(not text or text.endswith("\n"))
        return text

    def test_canonicalization_removes_punctuation_symbols_controls_and_blank_lines(self):
        source = (
            "  『日本語』\tテスト！？　♪😀☕️\n"
            "\u200b\n"
            "長音ー　反復々　カ\u3099\n"
            "記号だけ…!?♪\n"
        )
        result = canonicalize_edited_text(source)
        self.assertEqual(result, "日本語　テスト\n長音ー　反復々　ガ\n記号だけ\n")
        self.assertTrue(validate_edited_text(result)["valid"])

    def test_valid_all_symbol_subtitle_may_produce_empty_text(self):
        result = canonicalize_edited_text("！？♪😀\n…\n")
        self.assertEqual(result, "")
        checks = validate_edited_text(result)
        self.assertEqual(checks["line_count"], 0)
        self.assertTrue(checks["valid"])

    def test_srt_golden_output_and_external_input_writes_to_launch_directory(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "external"
            launch = root / "launch"
            source_dir.mkdir()
            launch.mkdir()
            source = source_dir / "日本語 [cc].srt"
            write_srt(
                source,
                "（清子）まあまあ！\n今は 楽しい歓迎会の場です。",
                "ＡＢＣ １２３／ﾖｼｭｱさん♪♪",
            )

            payload = run_edit(source, launch_directory=launch)

            output = launch / "日本語 [cc]_processed.txt"
            self.assertEqual(Path(payload["output_directory"]), launch.resolve())
            self.assertEqual(Path(payload["artifacts"][0]["path"]), output.resolve())
            self.assertEqual(
                self.assert_text_contract(output, expected_lines=2),
                "まあまあ　今は　楽しい歓迎会の場です\nABC　123ヨシュアさん\n",
            )
            self.assertEqual(source.read_text(encoding="utf-8").count("-->"), 2)
            self.assertFalse((launch / ".bmlsub").exists())

    def test_directory_is_non_recursive_stable_and_case_insensitive(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "subs"
            launch = root / "out"
            nested = source_dir / "nested"
            nested.mkdir(parents=True)
            launch.mkdir()
            write_srt(source_dir / "b.SRT", "二番！")
            (source_dir / "A.VTT").write_text(
                "WEBVTT\n\ncue-a\n00:00:01.000 --> 00:00:02.000 align:start\n一番。\n",
                encoding="utf-8",
            )
            write_srt(nested / "c.srt", "処理しない")
            (source_dir / "notes.txt").write_text("ignore", encoding="utf-8")

            payload = run_edit(source_dir, launch_directory=launch)

            self.assertEqual(payload["processed_count"], 2)
            self.assertEqual(
                [Path(item["source"]).name for item in payload["artifacts"]],
                ["A.VTT", "b.SRT"],
            )
            self.assertEqual((launch / "A_processed.txt").read_text(encoding="utf-8"), "一番\n")
            self.assertEqual((launch / "b_processed.txt").read_text(encoding="utf-8"), "二番\n")
            self.assertFalse((launch / "c_processed.txt").exists())

    def test_ass_extracts_only_dialogue_and_removes_formatting(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sample.ass"
            source.write_text(
                "[Script Info]\nTitle: Ignore me\nPlayResX: 1920\nPlayResY: 1080\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Ignore\n"
                "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
                "{\\pos(100,100)\\c&H00FFFF&}（清子）まあまあ！\\N今は 楽しい。\n",
                encoding="utf-8",
            )

            payload = run_edit(source, launch_directory=root)
            output = Path(payload["artifacts"][0]["path"])
            text = self.assert_text_contract(output, expected_lines=1)
            self.assertNotIn("Script", text)
            self.assertNotIn("Comment", text)
            self.assertNotIn("pos", text)
            self.assertEqual(text, "まあまあ　今は　楽しい\n")

    def test_bom_input_and_atomic_replacement_are_supported(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "bom.srt"
            source.write_bytes(
                b"\xef\xbb\xbf1\n00:00:01,000 --> 00:00:02,000\n"
                + "新しい！\n".encode("utf-8")
            )
            output = root / "bom_processed.txt"
            output.write_text("old\n", encoding="utf-8")

            run_edit(source, launch_directory=root)

            self.assertEqual(output.read_text(encoding="utf-8"), "新しい\n")
            self.assertFalse(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertFalse(any(root.glob(".bom_processed.txt.*.tmp")))


class EditFailureTests(unittest.TestCase):
    def test_missing_unsupported_empty_invalid_and_no_event_inputs_fail(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            unsupported = root / "subtitle.txt"
            unsupported.write_text("text", encoding="utf-8")
            empty_dir = root / "empty"
            empty_dir.mkdir()
            invalid = root / "invalid.srt"
            invalid.write_bytes(b"\xff\xfe\x00")
            no_events = root / "empty.srt"
            no_events.write_text("not a subtitle", encoding="utf-8")
            for value in (root / "missing.srt", unsupported, empty_dir, invalid, no_events):
                with self.subTest(value=value), self.assertRaises(BmlsubError):
                    run_edit(value, launch_directory=root)

    def test_batch_collision_is_rejected_before_writing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "subs"
            source_dir.mkdir()
            write_srt(source_dir / "same.srt", "SRT")
            (source_dir / "same.ass").write_text(
                "[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,ASS\n",
                encoding="utf-8",
            )
            with self.assertRaises(OutputValidationError):
                run_edit(source_dir, launch_directory=root)
            self.assertFalse((root / "same_processed.txt").exists())

    def test_batch_parse_failure_does_not_write_successful_sibling(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "subs"
            source_dir.mkdir()
            write_srt(source_dir / "a.srt", "valid")
            (source_dir / "b.srt").write_text("invalid", encoding="utf-8")
            with self.assertRaises(BmlsubError):
                run_edit(source_dir, launch_directory=root)
            self.assertFalse((root / "a_processed.txt").exists())
            self.assertFalse((root / "b_processed.txt").exists())


class EditCliTests(unittest.TestCase):
    def test_cli_defaults_to_current_directory_and_prints_one_json_document(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_srt(root / "episode.srt", "字幕です！")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with working_directory(root), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["build", "editsub"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["operation"], "editsub")
            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(payload["artifacts"][0]["line_count"], 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual((root / "episode_processed.txt").read_text(encoding="utf-8"), "字幕です\n")

    def test_cli_failure_is_structured_and_nonzero(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with working_directory(root), redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["build", "editsub", "missing.srt"])

            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "input_missing")


class EditLicenseTests(unittest.TestCase):
    def test_subsrefine_license_and_notice_are_present(self):
        root = Path(__file__).resolve().parents[1]
        license_text = (root / "LICENSES" / "SubsRefine-MIT.txt").read_text(encoding="utf-8")
        notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2025 MingYSub", license_text)
        self.assertIn("MIT License", license_text)
        self.assertIn("https://github.com/MingYSub/SubsRefine", notice)
        self.assertIn("c47cec799fb5615d74a6561a7b6a91054c669c4b", notice)


if __name__ == "__main__":
    unittest.main()
