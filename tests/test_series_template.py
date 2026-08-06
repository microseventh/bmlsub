from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from bmlsub.workstation import (
    SeriesMetadata, promote_series_metadata_template, series_metadata_template_guide,
    write_series_metadata_template,
)


class SeriesTemplateTests(unittest.TestCase):
    def test_template_has_annotations_and_expanded_production_defaults(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)

            payload = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(payload["schema_version"], "bmlsub-series-v1")
            self.assertEqual(
                payload["_template"]["required_fields"],
                ["series.title_chs", "series.romanized_title", "groups.chs"],
            )
            self.assertTrue(payload["_template"]["do_not_edit"])
            self.assertIn("请勿在这里填写内容", payload["_template"]["comment"])
            self.assertIn("必填", payload["series"]["title_chs"])
            self.assertIn("REQUIRED", payload["series"]["romanized_title"])
            self.assertEqual(payload["production"]["hardsub_parameters"], {})
            self.assertEqual(payload["production"]["hevc_parameters"]["video_codec"], "hevc_videotoolbox")
            self.assertEqual(payload["production"]["hevc_parameters"]["quality"], 60)
            self.assertEqual(payload["production"]["torrent_profile"]["format"], "v1")
            self.assertEqual(payload["publish"], {
                "credential_aliases": {},
                "notes": "",
                "qb_port": 8080,
                "qb_save_path": "/downloads",
                "qb_webui_origin": "https://127.0.0.1:8080",
                "r2_access": "private",
                "r2_bucket": "bml",
                "rclone_remote": "r2",
            })
            self.assertIn("publish.ssh_alias", payload["_template"]["optional_fields"])
            self.assertNotIn("ssh_alias", payload["publish"])
            self.assertNotIn("remote_root", payload["publish"])
            self.assertFalse((root / "bgminfo" / "series.json").exists())

    def test_incomplete_template_is_kept_and_reports_required_fields(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)

            result = promote_series_metadata_template(root)

            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["missing_required_fields"], [
                "series.title_chs", "series.romanized_title", "groups.chs",
            ])
            self.assertTrue(target.is_file())
            self.assertFalse((root / "bgminfo" / "series.json").exists())

    def test_completed_template_is_promoted_and_annotations_are_removed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["series"]["title_chs"] = "测试"
            payload["series"]["romanized_title"] = "Test"
            payload["groups"]["chs"] = "BML"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = promote_series_metadata_template(root)

            live = root / "bgminfo" / "series.json"
            self.assertEqual(result["status"], "succeeded")
            self.assertFalse(target.exists())
            self.assertTrue(live.is_file())
            stored = json.loads(live.read_text(encoding="utf-8"))
            self.assertNotIn("_template", stored)
            metadata = SeriesMetadata.load(live)
            self.assertEqual(metadata.title_chs, "测试")
            self.assertEqual(metadata.production["hardsub_parameters"], {})
            self.assertEqual(metadata.production["hevc_parameters"]["quality"], 60)

    def test_values_entered_in_legacy_required_fields_are_recovered(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["_template"].pop("do_not_edit")
            payload["_template"]["required_fields"] = {
                "groups.chs": "测试组",
                "series.romanized_title": "test",
                "series.title_chs": "测试",
            }
            payload["groups"]["chs"] = "测试组"
            payload["series"]["title_chs"] = "测试"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = promote_series_metadata_template(root)

            self.assertEqual(result["status"], "succeeded")
            metadata = SeriesMetadata.load(root / "bgminfo" / "series.json")
            self.assertEqual(metadata.title_chs, "测试")
            self.assertEqual(metadata.romanized_title, "test")
            self.assertEqual(metadata.group_chs, "测试组")
            self.assertFalse(target.exists())

    def test_legacy_annotation_descriptions_are_not_treated_as_values(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["_template"]["required_fields"] = {
                "series.title_chs": "必填：简体中文作品名",
                "series.romanized_title": "必填：用于发布文件名的罗马音作品名",
                "groups.chs": "必填：简体制作组名",
            }
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = promote_series_metadata_template(root)

            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["missing_required_fields"], [
                "series.title_chs", "series.romanized_title", "groups.chs",
            ])
            self.assertFalse((root / "bgminfo" / "series.json").exists())

    def test_pre_1_2_placeholders_are_never_promoted_as_metadata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = write_series_metadata_template(root)
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload.pop("_template")
            payload["series"]["title_chs"] = "请填写简体中文番名"
            payload["series"]["romanized_title"] = "PleaseFillRomanizedTitle"
            payload["groups"]["chs"] = "请填写简体制作组名"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            result = promote_series_metadata_template(root)

            self.assertEqual(result["status"], "needs_review")
            self.assertEqual(result["missing_required_fields"], [
                "series.title_chs", "series.romanized_title", "groups.chs",
            ])
            self.assertTrue(target.exists())
            self.assertFalse((root / "bgminfo" / "series.json").exists())

    def test_template_guide_names_required_fields_and_meanings(self):
        guide = series_metadata_template_guide()

        self.assertEqual(guide["required_fields"], [
            "series.title_chs", "series.romanized_title", "groups.chs",
        ])
        fields = {item["path"]: item for item in guide["fields"]}
        self.assertTrue(fields["series.title_chs"]["required"])
        self.assertFalse(fields["series.bgm_id"]["required"])
        self.assertIn("bgm_id/anime_id", fields["series.bgm_id"]["zh"])
        self.assertIn("bgminfo/series.json", guide["instructions"]["zh"])


if __name__ == "__main__":
    unittest.main()
