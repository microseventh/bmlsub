from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bmlsub.hanvert import (
    HanvertConversionError,
    classify_ass_language,
    convert_ass_with_fanhuaji,
    extract_ass_analysis,
    strip_ass_tags,
)


SAMPLE_ASS = """[Script Info]
Title: 简体标题不得转换
[V4+ Styles]
Format: Name, Fontname
Style: main-CN,简体字体
Style: main-JP,日本語字体
Style: Sign,Sign Font
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:01.00,main-CN,,0,0,0,,注释简体
Dialogue: 0,0:00:01.00,0:00:02.00,main-CN,,0,0,0,,这{\\b1}里面{\\b0}有东西
Dialogue: 0,0:00:02.00,0:00:03.00,main-JP,,0,0,0,,母さん
Dialogue: 0,0:00:03.00,0:00:04.00,Sign,,0,0,0,,本故事纯属虚构。\\Nこの物語はフィクションです。
Dialogue: 0,0:00:04.00,0:00:05.00,Sign,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}
"""


class HanvertTests(unittest.TestCase):
    def test_strip_ass_tags_handles_escapes_and_drawings(self) -> None:
        text = r"这{\b1}里面{\b0}有东西\N下一行\h空格{\p1}m 0 0 l 10 10{\p0}结束"
        self.assertEqual(strip_ass_tags(text), "这里面有东西\n下一行 空格结束")

    def test_language_classification_uses_text_safety_and_style_tokens(self) -> None:
        self.assertEqual(classify_ass_language("main-CN", "母さん"), "mixed")
        self.assertEqual(classify_ass_language("OP-JP", "中文"), "ja")
        self.assertEqual(classify_ass_language("NINJA", "中文"), "zh")
        self.assertEqual(classify_ass_language("PROJECT", "中文"), "zh")
        self.assertEqual(classify_ass_language("Sign", "本故事纯属虚构"), "zh")
        self.assertEqual(classify_ass_language("Sign", "この物語"), "ja")
        self.assertEqual(classify_ass_language("Sign", "本故事。\nこの物語"), "mixed")
        self.assertEqual(classify_ass_language("Sign", "测试この物語"), "mixed")
        self.assertEqual(classify_ass_language("Sign", "123 ABC"), "other")

    def test_extract_ass_analysis_writes_grouped_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.ass"
            output = Path(directory) / "sample.json"
            source.write_text(SAMPLE_ASS, encoding="utf-8")
            result = extract_ass_analysis(source, output)

            self.assertEqual(result["summary"]["dialogue_count"], 4)
            self.assertEqual(result["summary"]["comment_count"], 1)
            self.assertEqual(result["summary"]["language_counts"], {
                "zh": 1, "ja": 1, "mixed": 1, "other": 1,
            })
            self.assertEqual(result["summary"]["drawing_event_count"], 1)
            self.assertEqual(result["languages"]["zh"][0]["text"], "这里面有东西")
            self.assertIn("\n", result["languages"]["mixed"][0]["text"])
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["file"]["name"], "sample.ass")
            self.assertIn("这里面有东西", output.read_text(encoding="utf-8"))

    def test_extract_ass_analysis_can_include_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.ass"
            source.write_text(SAMPLE_ASS, encoding="utf-8")
            result = extract_ass_analysis(source, include_comments=True)
        self.assertEqual(result["summary"]["language_counts"]["zh"], 2)
        self.assertTrue(result["summary"]["comments_included"])

    @patch("bmlsub.hanvert.requests.post")
    def test_conversion_sends_only_visible_chinese_text(self, post: Mock) -> None:
        def response_for(url, data, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            self.assertEqual(data["text"], "这里面有东西\n本故事纯属虚构。")
            response.json.return_value = {
                "code": 0,
                "data": {"text": "這裡面有東西\n本故事純屬虛構。"},
            }
            return response

        post.side_effect = response_for
        result, stats = convert_ass_with_fanhuaji(SAMPLE_ASS)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["text"], "这里面有东西\n本故事纯属虚构。")
        self.assertIn(r"這{\b1}裡面{\b0}有東西", result)
        self.assertIn("Title: 简体标题不得转换", result)
        self.assertIn("Style: main-CN,简体字体", result)
        self.assertIn("母さん", result)
        self.assertIn("注释简体", result)
        self.assertIn(r"{\p1}m 0 0 l 10 10{\p0}", result)
        self.assertIn("本故事純屬虛構。", result)
        self.assertIn("この物語はフィクションです。", result)
        self.assertEqual(stats["converted_events"], 2)
        self.assertEqual(stats["skipped_mixed_groups"], 0)

    @patch("bmlsub.hanvert.requests.post")
    def test_length_change_across_tags_is_rejected(self, post: Mock) -> None:
        def response_for(url, data, timeout):
            response = Mock()
            response.raise_for_status.return_value = None
            lines = data["text"].splitlines()
            converted = []
            for text in lines:
                if text == "这里面有东西":
                    converted.append("這個裡面有東西")
                else:
                    converted.append(text.replace("纯属虚构", "純屬虛構"))
            response.json.return_value = {"code": 0, "data": {"text": "\n".join(converted)}}
            return response

        post.side_effect = response_for
        with self.assertRaisesRegex(HanvertConversionError, "多个 ASS 标签文本节点"):
            convert_ass_with_fanhuaji(SAMPLE_ASS)

    @patch("bmlsub.hanvert.requests.post")
    def test_full_file_mode_bypasses_ass_analysis(self, post: Mock) -> None:
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "code": 0,
            "data": {"text": SAMPLE_ASS.replace("简体标题", "繁體標題")},
        }
        result, stats = convert_ass_with_fanhuaji(SAMPLE_ASS, full_file=True)
        self.assertEqual(post.call_args.kwargs["data"]["text"], SAMPLE_ASS)
        self.assertIn("繁體標題", result)
        self.assertEqual(stats["conversion_mode"], "full_file")
        self.assertEqual(stats["fallback_reason"], "requested")

    @patch("bmlsub.hanvert.requests.post")
    def test_missing_events_format_falls_back_to_full_file(self, post: Mock) -> None:
        source = "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,main-CN,,0,0,0,,测试\n"
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "code": 0,
            "data": {"text": source.replace("测试", "測試")},
        }
        result, stats = convert_ass_with_fanhuaji(source)
        self.assertIn("測試", result)
        self.assertEqual(post.call_args.kwargs["data"]["text"], source)
        self.assertEqual(stats["fallback_reason"], "events_format_missing")

    def test_missing_events_format_can_disable_fallback(self) -> None:
        source = "[Events]\nDialogue: 0,0:00:00.00,0:00:01.00,main-CN,,0,0,0,,测试\n"
        with self.assertRaisesRegex(HanvertConversionError, "缺少有效 Format"):
            convert_ass_with_fanhuaji(source, fallback_to_full_file=False)

    @patch("bmlsub.hanvert.requests.post")
    def test_api_error_is_wrapped(self, post: Mock) -> None:
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"code": 1, "msg": "bad"}
        with self.assertRaisesRegex(HanvertConversionError, "bad"):
            convert_ass_with_fanhuaji(SAMPLE_ASS)


if __name__ == "__main__":
    unittest.main()
