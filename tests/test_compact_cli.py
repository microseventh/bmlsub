from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bmlsub.cli import build_parser
from bmlsub.workstation import (
    BuildContext, OPERATION_NAMES, discover_inputs, execute_plan, plan_operation,
)
from bmlsub.workstation.commands import run_operation


class NonTTY(io.StringIO):
    def isatty(self) -> bool:
        return False


class CompactCliTests(unittest.TestCase):
    def test_parser_exposes_only_compact_surface(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["ws", "start"]).ws_command, "start")
        self.assertIsNone(parser.parse_args(["ws", "end"]).unattended)
        self.assertEqual(parser.parse_args(["ws", "end", "yes"]).unattended, "yes")
        for operation in OPERATION_NAMES:
            self.assertEqual(parser.parse_args(["build", operation]).operation, operation)
            self.assertEqual(parser.parse_args(["rebuild", operation]).operation, operation)

    def test_old_commands_flags_and_extra_arguments_are_rejected(self):
        parser = build_parser()
        for argv in (
            ["workstation", "start"], ["credentials", "list"],
            ["build", "encode", "--input", "x.mkv"],
            ["rebuild", "encode", "extra"], ["ws", "start", "yes"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                parser.parse_args(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_non_tty_menu_has_no_state_side_effect(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = run_operation(
                None, rebuild=False, directory=root, input_stream=NonTTY(), output=io.StringIO(),
            )
            self.assertEqual(result["status"], "needs_review")
            self.assertEqual([item["name"] for item in result["available_operations"]],
                             list(OPERATION_NAMES))
            self.assertFalse((root / ".bmlsub").exists())

    def test_rebuild_anibt_refuses_before_state_initialization(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = plan_operation("anibt", root, rebuild=True)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["external_actions"], [])
            self.assertFalse((root / ".bmlsub").exists())


class DiscoveryAndContextTests(unittest.TestCase):
    def test_discovery_is_non_recursive_filtered_and_stable(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "B.MKV").write_bytes(b"b")
            (root / "a.mp4").write_bytes(b"a")
            (root / ".hidden.mkv").write_bytes(b"hidden")
            (root / "partial.mkv.part").write_bytes(b"partial")
            nested = root / "nested"
            nested.mkdir()
            (nested / "c.mkv").write_bytes(b"c")
            state = root / ".bmlsub"
            state.mkdir()
            (state / "state.mkv").write_bytes(b"state")

            shallow = discover_inputs("encode", root)
            self.assertEqual([Path(item).name for item in shallow["found"]], ["a.mp4", "B.MKV"])
            recursive = discover_inputs("encode", root, recursive=True)
            self.assertEqual([Path(item).name for item in recursive["found"]],
                             ["a.mp4", "B.MKV", "c.mkv"])

    def test_bgminfo_build_and_rebuild_write_plan_receipt_and_backup(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "series"
            root.mkdir()
            first = plan_operation("bgminfo", root, options={
                "title_chs": "测试", "title_cht": "測試", "romanized_title": "Test",
                "group_chs": "字幕组", "group_cht": "字幕組",
            })
            self.assertEqual(first["status"], "planned")
            result = execute_plan(first)
            self.assertEqual(result["status"], "succeeded")
            context = BuildContext(root)
            self.assertTrue(context.database.is_file())
            self.assertTrue(Path(result["receipt"]).is_file())
            self.assertTrue((root / "bgminfo" / "series.json").is_file())

            second = plan_operation("bgminfo", root, rebuild=True, options={
                "title_chs": "新标题", "title_cht": "新標題", "romanized_title": "New",
                "group_chs": "字幕组", "group_cht": "字幕組",
            })
            rebuilt = execute_plan(second)
            self.assertEqual(rebuilt["status"], "succeeded")
            self.assertTrue(any(context.backups.iterdir()))
            self.assertEqual(len(context.manifest()["operations"]["bgminfo"]), 2)
            self.assertEqual(context.manifest()["operations"]["bgminfo"][0]["status"], "stale")

            repeated = plan_operation("bgminfo", root, options={})
            self.assertEqual(repeated["status"], "planned")
            self.assertEqual(repeated["mappings"][0]["outputs"][0]["action"], "skip")
            self.assertEqual(execute_plan(repeated)["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
