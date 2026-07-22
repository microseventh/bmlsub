from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from bmlsub.workstation import (
    DeliverySelection, SeriesMetadata, create_series_metadata, discover_episode_directories,
    discover_series_context,
    ensure_traditional_series_names, inspect_episode_stage, inspect_series_workspace,
    plan_delivery_execution, plan_rebuild, resolve_series_root, run_rebuild,
    transcription_jobs_for_mode, update_series_publish_config,
    write_series_metadata_template,
)


class WorkstationStartTests(unittest.TestCase):
    def make_series(self, root: Path, episodes=("01",), *, publish=None) -> None:
        create_series_metadata(
            root.name, parent_dir=root.parent, title_chs="测试", title_cht="測試",
            romanized_title="Test", group_chs="测试组", group_cht="測試組",
            publish=publish or {},
        )
        for episode in episodes:
            (root / episode).mkdir()

    def test_start_command_parser_has_explicit_delivery_subcommand(self):
        from bmlsub.cli import build_parser
        parser = build_parser()
        plain = parser.parse_args(["workstation", "start"])
        delivery = parser.parse_args([
            "workstation", "start", "delivery", "--episode-id", "01",
            "--confirm-external-action",
        ])
        self.assertEqual(plain.workstation_start_command, None)
        self.assertEqual(delivery.workstation_start_command, "delivery")
        self.assertEqual(delivery.episode_id, "01")
        self.assertTrue(delivery.confirm_external_action)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root, ("10", "02", "1"))
            self.assertEqual(resolve_series_root(root / "02"), root.resolve())
            self.assertEqual(
                [item.name for item in discover_episode_directories(root)],
                ["1", "02", "10"],
            )

    def test_missing_metadata_and_template(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "01").mkdir()
            inspected = inspect_series_workspace(root)
            self.assertEqual(inspected["status"], "needs_review")
            self.assertEqual(inspected["next_action"], "create_series_metadata")
            template = write_series_metadata_template(root)
            self.assertEqual(template.name, "series.template.json")
            self.assertFalse((root / "bgminfo" / "series.json").exists())
            with self.assertRaises(FileExistsError):
                write_series_metadata_template(root)

    def test_physical_stage_matrix(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "01.mkv").write_bytes(b"video")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "preprocess")
            self.assertTrue(inspected["executable"])
            self.assertFalse((episode / "workstation").exists())

            (episode / "01.en.ass").write_text("reference", encoding="utf-8")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "human_handoff")
            self.assertFalse(inspected["executable"])

            (episode / "01.CHS&JPN.ass").write_text("formal", encoding="utf-8")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "blocked")

            (episode / "font.ttf").write_bytes(b"font")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "local_production")
            self.assertEqual(inspected["recommended_action"], "run_delivery")

    def test_multiple_videos_are_ambiguous(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "a.mkv").write_bytes(b"a")
            (episode / "b.mp4").write_bytes(b"b")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "ambiguous")
            self.assertFalse(inspected["executable"])

    def test_registered_products_and_torrents_recommend_publish(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root, publish={
                "remote_root": "/data/releases", "ssh_alias": "host",
                "credential_aliases": {
                    "r2": "r2", "ssh": "ssh", "qbittorrent": "qb", "anibt": "anibt",
                },
            })
            episode = root / "01"
            state = episode / "workstation" / "state"
            state.mkdir(parents=True)
            manifest = {
                "schema_version": "workstation-manifest-v1",
                "source": {"video_artifact_id": "video"},
                "preprocess": {}, "subtitles": {}, "fonts": {"artifact_ids": []},
                "products": {
                    "hardsub_chs_artifact_id": "chs",
                    "hardsub_cht_artifact_id": "cht",
                    "muxed_mkv_artifact_id": "mkv",
                },
                "torrents": {"mp4_chs": "tc", "mp4_cht": "tt", "mkv_hevc": "tm"},
                "publish": {},
            }
            (state / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "publish")
            self.assertEqual(inspected["recommended_action"], "run_publish")
    def test_publish_receipt_shape_is_complete(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            state = episode / "workstation" / "state"
            state.mkdir(parents=True)
            keys = ("mp4_chs", "mp4_cht", "mkv_hevc")
            manifest = {
                "schema_version": "workstation-manifest-v1",
                "source": {"video_artifact_id": "video"},
                "preprocess": {}, "subtitles": {}, "fonts": {"artifact_ids": []},
                "products": {
                    "hardsub_chs_artifact_id": "chs", "hardsub_cht_artifact_id": "cht",
                    "muxed_mkv_artifact_id": "mkv",
                },
                "torrents": {key: f"torrent-{key}" for key in keys},
                "publish": {
                    "r2": {
                        f"{key}:{label}": f"r2-{key}-{label}"
                        for key in keys for label in ("content", "torrent")
                    },
                    "remote": {key: f"remote-{key}" for key in keys},
                    "qb": {key: f"qb-{key}" for key in keys},
                    "anibt": {key: f"anibt-{key}" for key in keys},
                },
            }
            (state / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["detected_phase"], "complete")
            self.assertFalse(inspected["executable"])

    def test_explicit_source_resolves_video_ambiguity(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "a.mkv").write_bytes(b"a")
            (episode / "b.mp4").write_bytes(b"b")
            inspected = inspect_episode_stage(root, "01", source_video="a.mkv")
            self.assertEqual(inspected["detected_phase"], "preprocess")
            self.assertTrue(inspected["executable"])

    def test_explicit_production_subtitle_enables_delivery(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "01.mkv").write_bytes(b"video")
            (episode / "translated.ass").write_text("formal", encoding="utf-8")
            (episode / "font.ttf").write_bytes(b"font")
            inspected = inspect_episode_stage(
                root, "01", production_subtitle="translated.ass",
            )
            self.assertEqual(inspected["detected_phase"], "local_production")
            self.assertTrue(inspected["executable"])

    def test_registered_preprocess_preserves_formal_subtitle_font_blocker(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "01.mkv").write_bytes(b"video")
            (episode / "01.chs&jpn.ass").write_text("formal", encoding="utf-8")
            state = episode / "workstation" / "state"
            state.mkdir(parents=True)
            manifest = {
                "schema_version": "workstation-manifest-v1",
                "source": {"video_artifact_id": "video"},
                "preprocess": {}, "subtitles": {}, "fonts": {"artifact_ids": []},
                "products": {}, "torrents": {}, "publish": {},
            }
            summary = {
                "schema_version": "workstation-summary-v1",
                "preprocess": {"status": "succeeded", "steps": {}},
            }
            (state / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (state / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

            blocked = inspect_episode_stage(root, "01")
            self.assertEqual(blocked["detected_phase"], "blocked")
            self.assertEqual(blocked["missing"], ["top-level Aegisub fonts"])
            self.assertTrue(any(
                item["code"] == "formal_chs_present" for item in blocked["evidence"]
            ))
            self.assertFalse(any("formal CHS subtitle" == item for item in blocked["missing"]))

            (episode / "font.ttf").write_bytes(b"font")
            ready = inspect_episode_stage(root, "01")
            self.assertEqual(ready["detected_phase"], "local_production")
            self.assertEqual(ready["recommended_action"], "run_delivery")
            self.assertTrue(ready["executable"])

    def test_sqlite_without_manifest_falls_back_to_physical_inspection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            state = episode / "workstation" / "state"
            state.mkdir(parents=True)
            (state / "state.sqlite3").write_bytes(b"")
            (episode / "01.mkv").write_bytes(b"video")
            inspected = inspect_episode_stage(root, "01")
            self.assertEqual(inspected["state_source"], "physical_files")
            self.assertEqual(inspected["detected_phase"], "preprocess")
    def test_transcription_job_policies(self):
        quick = transcription_jobs_for_mode("quick")
        self.assertEqual([(item.name, item.mode) for item in quick], [("direct", "direct")])
        full = transcription_jobs_for_mode("full")
        self.assertEqual(
            [(item.name, item.mode) for item in full],
            [("direct", "direct"), ("chunked", "chunked")],
        )
        self.assertEqual(full[1].chunk_seconds, 240.0)
        self.assertEqual(full[1].overlap_seconds, 5.0)
        self.assertEqual(transcription_jobs_for_mode("none"), ())
        with self.assertRaises(ValueError):
            transcription_jobs_for_mode("other")

    def test_delivery_selection_scopes_and_dependencies(self):
        full = DeliverySelection.for_scope("full")
        self.assertEqual(full.products, ("mp4_chs", "mp4_cht", "mkv_hevc"))
        self.assertIn("delivery.encode_hevc", full.steps)
        self.assertIn("delivery.create_torrents", full.steps)

        mkv = DeliverySelection.for_scope("mkv", create_torrents=False)
        self.assertEqual(mkv.products, ("mkv_hevc",))
        self.assertIn("delivery.encode_hevc", mkv.steps)
        self.assertIn("delivery.mux_subtitles", mkv.steps)
        self.assertNotIn("delivery.encode_hardsub_chs", mkv.steps)
        self.assertNotIn("delivery.create_torrents", mkv.steps)

        mp4 = DeliverySelection.for_scope("mp4")
        self.assertEqual(mp4.products, ("mp4_chs", "mp4_cht"))
        self.assertNotIn("delivery.encode_hevc", mp4.steps)
        self.assertNotIn("delivery.mux_subtitles", mp4.steps)

        custom = DeliverySelection.for_scope(
            "custom", products=("mkv_hevc", "mp4_chs"),
        )
        self.assertEqual(custom.products, ("mp4_chs", "mkv_hevc"))
        with self.assertRaises(ValueError):
            DeliverySelection.for_scope("custom")
        with self.assertRaises(ValueError):
            DeliverySelection.for_scope("mkv", products=("mkv_hevc",))

    def test_delivery_execution_plan_is_read_only_and_scoped(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "01.mkv").write_bytes(b"video")
            (episode / "01.CHS&JPN.ass").write_text("formal", encoding="utf-8")
            (episode / "01.CHT&JPN.ass").write_text("traditional", encoding="utf-8")
            (episode / "font.ttf").write_bytes(b"font")
            selection = DeliverySelection.for_scope("mkv", create_torrents=False)
            plan = plan_delivery_execution(episode, selection=selection)
            self.assertEqual(plan["status"], "succeeded")
            self.assertEqual(plan["traditional_subtitle"], str((episode / "01.CHT&JPN.ass").resolve()))
            self.assertEqual(plan["selection"]["products"], ["mkv_hevc"])
            self.assertEqual(plan["font_count"], 1)
            self.assertIn("delivery.encode_hevc", plan["steps"])
            self.assertNotIn("delivery.encode_hardsub_chs", plan["steps"])
            self.assertEqual(plan["targets"]["torrents"], {})
            self.assertFalse(plan["external_publish_allowed"])
            self.assertFalse((episode / "workstation").exists())

    def test_traditional_subtitle_is_optional_in_delivery_plan(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            episode = root / "01"
            (episode / "01.mkv").write_bytes(b"video")
            (episode / "01.CHS&JPN.ass").write_text("formal", encoding="utf-8")
            (episode / "font.ttf").write_bytes(b"font")

            plan = plan_delivery_execution(episode)

            self.assertEqual(plan["status"], "succeeded")
            self.assertIsNone(plan["traditional_subtitle"])

    def test_rebuild_plan_never_allows_publish(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            plan = plan_rebuild(root, "01", "encode_hevc")
            self.assertTrue(plan["force"])
            self.assertFalse(plan["external_publish_allowed"])
            self.assertEqual(run_rebuild(plan, confirmed=False)["status"], "awaiting_confirmation")
            with self.assertRaises(ValueError):
                plan_rebuild(root, "01", "publish")

    def test_anibt_profile_enables_nyaa_for_all_products(self):
        from bmlsub.workstation.models import PublishConfig
        from bmlsub.workstation.publish import _anibt_profile

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = PublishConfig(bgm_id=12345, notes="release notes")
            expected = {
                "mp4_chs": (["CHS", "JP"], "EMBEDDED", "MP4"),
                "mp4_cht": (["CHT", "JP"], "EMBEDDED", "MP4"),
                "mkv_hevc": (["CHS", "CHT", "JP"], "INTERNAL", "MKV"),
            }
            for product_key, (language, subtitle, format_name) in expected.items():
                path = root / f"{product_key}.{format_name.lower()}"
                path.write_bytes(b"release")
                profile = _anibt_profile(
                    config, "01", path, product_key, publish_nyaa=True,
                )
                self.assertEqual(profile["language"], language)
                self.assertEqual(profile["subtitle"], subtitle)
                self.assertEqual(profile["format"], format_name)
                self.assertTrue(profile["nyaa"])
                self.assertEqual(profile["nyaa_category"], "1_4")
                self.assertFalse(profile["nyaa_complete"])
                self.assertFalse(profile["nyaa_remake"])
                self.assertIn("http://nyaa.tracker.wf:7777/announce", profile["trackers"])
                self.assertNotIn("nyaa_description", profile)

            plain_path = root / "plain.mkv"
            plain_path.write_bytes(b"release")
            plain = _anibt_profile(config, "01", plain_path, "mkv_hevc")
            self.assertNotIn("nyaa", plain)
            self.assertNotIn("trackers", plain)

    def test_publish_remote_paths_are_flat_but_r2_keys_are_nested(self):
        from bmlsub.workstation import PublishConfig, WorkstationConfig
        config = PublishConfig(
            remote_dir="/data/dcapp/qb/downloads/", qb_save_path="/downloads",
        )
        filename = "release file.mp4"
        self.assertEqual(
            config.remote_target(filename, series_folder_name="example-series", episode_id="01"),
            "/data/dcapp/qb/downloads/release file.mp4",
        )
        self.assertEqual(config.remote_save_path(), "/data/dcapp/qb/downloads")
        self.assertEqual(config.qb_save_path, "/downloads")
        self.assertEqual(
            config.object_key("01", filename, series_folder_name="example-series"),
            "example-series/01/release file.mp4",
        )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root, publish={"notes": "keep me"})
            metadata_path = root / "bgminfo" / "series.json"
            before = SeriesMetadata.load(metadata_path)
            updated = update_series_publish_config(
                metadata_path,
                {
                    "remote_root": "/data/releases", "qb_save_path": "/downloads",
                    "ssh_alias": "media-vps",
                },
                credential_aliases={
                    "r2": "r2-main", "ssh": "media-vps",
                    "qbittorrent": "qb-main", "anibt": "anibt-main",
                },
            )
            self.assertEqual(updated.title_chs, before.title_chs)
            self.assertEqual(updated.production, before.production)
            self.assertEqual(updated.publish["notes"], "keep me")
            self.assertEqual(updated.publish["remote_root"], "/data/releases")
            self.assertEqual(updated.publish["qb_save_path"], "/downloads")
            context = discover_series_context(root / "01")
            inherited = WorkstationConfig.from_series_context(context).publish
            self.assertEqual(inherited.remote_dir, "/data/releases")
            self.assertEqual(inherited.qb_save_path, "/downloads")
            self.assertEqual(updated.publish["credential_aliases"]["r2"], "r2-main")
            with self.assertRaises(ValueError):
                update_series_publish_config(metadata_path, {"api_token": "secret"})

    def test_credential_service_initializes_empty_manifest_once(self):
        from bmlsub.credentials import CredentialService
        with TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "credentials.json"
            service = CredentialService(manifest_path=manifest)
            first = service.initialize_manifest(namespace="testing")
            second = service.initialize_manifest(namespace="other")
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(second["namespace"], "testing")
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["profiles"], {})
            self.assertNotIn("secret", manifest.read_text(encoding="utf-8").lower())

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "series"
            root.mkdir()
            metadata = create_series_metadata(
                root.name, parent_dir=root.parent, title_chs="测试", title_cht="測試",
                romanized_title="Test", group_chs="组", group_cht="組",
                publish={"notes": "![](image.jpg)\n\n[频道](https://example.com)"},
            )
            self.assertEqual(
                metadata.publish["notes"],
                "![](image.jpg)\n\n[频道](https://example.com)",
            )
    def test_traditionalization_success_failure_and_retry(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "series"
            root.mkdir()
            metadata = create_series_metadata(
                root.name, parent_dir=root.parent, title_chs="测试番名",
                romanized_title="Test", group_chs="测试组",
            )
            calls = []

            def failing(text, converter, api_url, timeout):
                calls.append(text)
                raise RuntimeError("provider unavailable")

            failed = ensure_traditional_series_names(metadata.path, provider=failing)
            self.assertEqual(failed["status"], "pending")
            stored = json.loads(metadata.path.read_text(encoding="utf-8"))
            self.assertIsNone(stored["series"]["title_cht"])
            self.assertEqual(
                stored["series"]["traditionalization"]["attempts"]["title_cht"]["source"],
                "测试番名",
            )

            def succeeding(text, converter, api_url, timeout):
                return {"测试番名": "測試番名", "测试组": "測試組"}[text]

            retried = ensure_traditional_series_names(metadata.path, provider=succeeding)
            self.assertEqual(retried["status"], "resolved")
            stored = json.loads(metadata.path.read_text(encoding="utf-8"))
            self.assertEqual(stored["series"]["title_cht"], "測試番名")
            self.assertEqual(stored["groups"]["cht"], "測試組")

    def test_pending_traditionalization_blocks_series_start(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_series(root)
            payload = json.loads((root / "bgminfo" / "series.json").read_text(encoding="utf-8"))
            payload["series"]["title_cht"] = None
            payload["series"]["traditionalization"] = {
                "status": "pending", "converter": "Taiwan",
                "api_url": "https://api.zhconvert.org/convert", "attempts": {},
            }
            (root / "bgminfo" / "series.json").write_text(json.dumps(payload), encoding="utf-8")
            inspected = inspect_series_workspace(root)
            self.assertEqual(inspected["status"], "needs_review")
            self.assertEqual(inspected["next_action"], "retry_traditionalization")


if __name__ == "__main__":
    unittest.main()
