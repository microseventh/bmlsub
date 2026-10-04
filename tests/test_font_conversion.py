from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmlsub.cli import main
from bmlsub.font_conversion import (
    FontFace,
    _build_index,
    convert_ass_content,
    read_font_faces,
    run_font_conversion,
)


ASS_TEXT = (
    "[Script Info]\nTitle: Test\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,中文字体,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
    "{\\fn@中文字体}测试\n"
)


def fake_faces() -> list[FontFace]:
    return [FontFace(
        path=Path("fake.ttf"),
        index=0,
        aliases={"中文字体", "englishfont"},
        english_names=["EnglishFont"],
        localized_names=["中文字体"],
    )]


def variant_faces() -> list[FontFace]:
    faces = []
    for weight in ("W15", "W22", "W24"):
        localized = f"梦源宋体 CN {weight}"
        english = f"Dream Han Serif CN {weight}"
        faces.append(FontFace(
            path=Path(f"{weight}.ttf"),
            index=0,
            aliases={localized.casefold(), english.casefold()},
            english_names=[english],
            localized_names=[localized],
            localized_to_english={localized.casefold(): english},
            english_to_localized={english.casefold(): localized},
        ))
    return faces


class FontConversionTests(unittest.TestCase):
    def test_content_converts_in_both_directions_and_preserves_at(self):
        english = convert_ass_content(ASS_TEXT, fake_faces(), to_chinese=False)
        self.assertIn("Style: Default,EnglishFont", english.content)
        self.assertIn(r"{\fn@EnglishFont}测试", english.content)

        chinese = convert_ass_content(english.content, fake_faces(), to_chinese=True)
        self.assertIn("Style: Default,中文字体", chinese.content)
        self.assertIn(r"{\fn@中文字体}测试", chinese.content)

    def test_variant_names_are_converted_individually_and_japanese_is_untouched(self):
        content = (
            "[V4+ Styles]\nFormat: Name, Fontname\n"
            "Style: Default,梦源宋体 CN W15\n"
            r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\fn@梦源宋体 CN W22}测试\N"
            r"{\fn梦源宋体 CN W24}测试 {\fnDream Han Serif JP W24}JP\n"
        )
        english = convert_ass_content(content, variant_faces(), to_chinese=False)
        self.assertIn("Style: Default,Dream Han Serif CN W15", english.content)
        self.assertIn(r"{\fn@Dream Han Serif CN W22}", english.content)
        self.assertIn(r"{\fnDream Han Serif CN W24}", english.content)
        self.assertIn(r"{\fnDream Han Serif JP W24}", english.content)

        chinese = convert_ass_content(english.content, variant_faces(), to_chinese=True)
        self.assertIn("Style: Default,梦源宋体 CN W15", chinese.content)
        self.assertIn(r"{\fn@梦源宋体 CN W22}", chinese.content)
        self.assertIn(r"{\fn梦源宋体 CN W24}", chinese.content)
        self.assertIn(r"{\fnDream Han Serif JP W24}", chinese.content)

    def test_real_japanese_font_variants_are_not_indexed(self):
        font_directory = Path("/Users/miwata/Movies/BML/Project/转生成为魔剑EP01/fonts")
        if not font_directory.is_dir():
            self.skipTest("real font fixture is not available")
        faces, warnings = read_font_faces(font_directory)
        self.assertEqual(warnings, [])
        chinese_index = _build_index(faces, localized=True)
        self.assertNotIn("dream han serif jp w22", chinese_index)
        self.assertNotIn("dream han serif jp", chinese_index)
        result = convert_ass_content(
            "Style: Default,Dream Han Serif JP W22\n"
            r"{\fnDream Han Serif JP W22}x\n",
            faces,
            to_chinese=True,
        )
        self.assertIn("Style: Default,Dream Han Serif JP W22", result.content)
        self.assertIn(r"{\fnDream Han Serif JP W22}", result.content)

    def test_cli_uses_default_fonts_directory_and_returns_json(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fonts = root / "fonts"
            fonts.mkdir()
            (fonts / "fake.ttf").write_bytes(b"placeholder")
            source = root / "episode.ass"
            source.write_text(ASS_TEXT, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("bmlsub.font_conversion.read_font_faces", return_value=(fake_faces(), [])), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["build", "fonts2en", str(source)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["operation"], "fonts2en")
            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(Path(payload["fonts_directory"]), fonts.resolve())
            self.assertEqual(stderr.getvalue(), "")
            output = root / "episode.english.ass"
            self.assertIn("EnglishFont", output.read_text(encoding="utf-8"))

    def test_directory_skips_generated_font_outputs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fonts = root / "fonts"
            fonts.mkdir()
            (fonts / "fake.ttf").write_bytes(b"placeholder")
            source = root / "episode.ass"
            source.write_text(ASS_TEXT, encoding="utf-8")
            (root / "already.english.ass").write_text(ASS_TEXT, encoding="utf-8")
            (root / "already.chinese.ass").write_text(ASS_TEXT, encoding="utf-8")

            with patch("bmlsub.font_conversion.read_font_faces", return_value=(fake_faces(), [])):
                payload = run_font_conversion(root)

            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(Path(payload["artifacts"][0]["source"]), source.resolve())

    def test_missing_fonts_directory_is_structured_error(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "episode.ass"
            source.write_text(ASS_TEXT, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(["build", "fonts2en", str(source)])

            self.assertEqual(exit_code, 1)
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"]["code"], "input_missing")
            self.assertIn("font directory not found", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
