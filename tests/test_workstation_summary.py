from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from bmlsub.cli import main
from bmlsub.workstation_summary import format_workstation_summary


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class FakeParser:
    def __init__(self, args: Namespace):
        self.args = args

    def parse_args(self, argv):
        return self.args


def quick_args(payload):
    return Namespace(
        command="workstation",
        workstation_command="start",
        workstation_start_command=None,
        handler=lambda args: payload,
    )


class WorkstationSummaryTests(unittest.TestCase):
    def test_formatter_reports_completed_reused_failed_pending_and_next(self):
        payload = {
            "status": "failed",
            "episode_id": "04",
            "summary": {
                "delivery": {
                    "status": "failed",
                    "steps": {
                        "delivery.snapshot_chs_subtitle": "succeeded",
                        "delivery.validate_subtitles_fonts": "skipped",
                        "delivery.encode_hevc": "failed",
                    },
                },
            },
            "last_step": {
                "step": "delivery.encode_hevc",
                "status": "failed",
                "error": {"message": "ffmpeg exited with code 1"},
            },
            "completed_products": ["mp4_chs"],
            "deferred_products": ["mkv_hevc"],
            "next_action": "run_delivery",
        }

        text = format_workstation_summary(payload)

        self.assertIn("单集 04", text)
        self.assertIn("已完成", text)
        self.assertIn("[完成] 登记正式 CHS 字幕", text)
        self.assertIn("已复用", text)
        self.assertIn("[复用] 检查字幕与字体", text)
        self.assertIn("失败", text)
        self.assertIn("ffmpeg exited with code 1", text)
        self.assertIn("待处理", text)
        self.assertIn("[待处理] 简繁内封 MKV", text)
        self.assertIn("下一步", text)
        self.assertIn("确认并执行本地压制", text)

    def test_formatter_infers_delivery_command_after_local_production(self):
        text = format_workstation_summary({
            "status": "succeeded",
            "episode_id": "04",
            "summary": {"delivery": {
                "status": "succeeded",
                "steps": {"delivery.create_torrents": "succeeded"},
            }},
            "completed_products": ["mp4_chs", "mp4_cht", "mkv_hevc"],
            "deferred_products": [],
        })
        self.assertIn("$ bmlsub ws end", text)

    def test_formatter_shows_template_path_and_required_fields(self):
        text = format_workstation_summary({
            "status": "needs_review",
            "template_path": "/series/bgminfo/series.template.json",
            "template_guide": {
                "required_fields": ["series.title_chs", "groups.chs"],
                "instructions": {"zh": "填写后重新运行 bmlsub ws start。"},
            },
            "next_action": "complete_template_and_rerun",
        })
        self.assertIn("/series/bgminfo/series.template.json", text)
        self.assertIn("series.title_chs, groups.chs", text)
        self.assertIn("重新运行 bmlsub ws start", text)

    def test_tty_quick_mode_prints_human_summary_instead_of_json(self):
        payload = {
            "status": "succeeded",
            "episode_id": "04",
            "summary": {"preprocess": {
                "status": "succeeded",
                "steps": {"preprocess.inspect_video": "succeeded"},
            }},
        }
        output = TTYBuffer()
        with patch("bmlsub.cli.build_parser", return_value=FakeParser(quick_args(payload))), \
             redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Workstation 快速模式", output.getvalue())
        self.assertIn("[完成] 检查并登记源视频", output.getvalue())
        self.assertNotIn('"status"', output.getvalue())

    def test_tty_quick_mode_formats_unexpected_errors_without_json(self):
        def fail(args):
            raise RuntimeError("encoder unavailable")

        args = quick_args({})
        args.handler = fail
        output = TTYBuffer()
        with patch("bmlsub.cli.build_parser", return_value=FakeParser(args)), \
             redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("encoder unavailable", output.getvalue())
        self.assertIn("失败", output.getvalue())
        self.assertNotIn('"error"', output.getvalue())

    def test_non_tty_quick_mode_keeps_json_stdout_contract(self):
        payload = {"status": "succeeded", "episode_id": "04"}
        output = io.StringIO()
        with patch("bmlsub.cli.build_parser", return_value=FakeParser(quick_args(payload))), \
             redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), payload)

    def test_other_tty_commands_keep_json_output(self):
        payload = {"status": "succeeded", "artifacts": []}
        args = Namespace(command="asset", handler=lambda value: payload)
        output = TTYBuffer()
        with patch("bmlsub.cli.build_parser", return_value=FakeParser(args)), \
             redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), payload)


if __name__ == "__main__":
    unittest.main()
