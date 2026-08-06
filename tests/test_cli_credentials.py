from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmlsub.cli import (
    _choose_or_create_profile, _delivery_credential_status, _delivery_public_values,
    _prompt_default, _select_nyaa_syndication, _select_ui_language,
    _print_publish_plan, build_parser,
)
from bmlsub.credentials import load_secure_json
from bmlsub.interactive import set_ui_language


class FakeCredentialService:
    def __init__(self, profiles):
        self.profiles = profiles
        self.created = []
        self.updated = []
        self.validated = []

    def list_profiles(self):
        return {"profiles": list(self.profiles)}

    def create_profile(self, **kwargs):
        self.created.append(kwargs)

    def update_profile(self, alias, **kwargs):
        self.updated.append((alias, kwargs))

    def validate_profile(self, alias):
        self.validated.append(alias)
        return {"valid": True}


class CredentialWizardTests(unittest.TestCase):
    def test_invalid_credential_json_reports_path_and_location(self):
        with TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "credentials.json"
            manifest.write_text('{"profiles": {},}\n', encoding="utf-8")
            manifest.chmod(0o600)
            with self.assertRaisesRegex(
                ValueError,
                rf"credential JSON is invalid: {manifest} .*line 1, column 17",
            ):
                load_secure_json(manifest)

    def test_delivery_status_rejects_invalid_manifest_before_profile_checks(self):
        with TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "credentials.json"
            manifest.write_text('{"profiles": {},}\n', encoding="utf-8")
            manifest.chmod(0o600)
            config = SimpleNamespace(
                credential_manifest=manifest,
                r2_credential_profile=None,
                ssh_profile=None,
                qb_credential_profile=None,
                anibt_credential_profile=None,
            )
            status = _delivery_credential_status(config)
        self.assertEqual(status["status"], "invalid")
        self.assertIn("line 1, column 17", status["manifest_error"])
        self.assertEqual(status["profiles"], [])

    def test_initial_delivery_configuration_requires_qb_origin_and_port(self):
        prompts = iter([
            "/data/dcapp/qb/downloads", "8081", "https://qb.microseventh.eu.org",
        ])
        captured = []
        with patch(
            "bmlsub.cli._prompt_stderr",
            side_effect=lambda prompt: captured.append(prompt) or next(prompts),
        ):
            values = _delivery_public_values(
                {"qb_save_path": "/downloads"}, "uk-vps", edit_existing=False,
            )
        self.assertEqual(values["qb_port"], 8081)
        self.assertEqual(values["qb_webui_origin"], "https://qb.microseventh.eu.org")
        self.assertTrue(any("qBittorrent WebUI 端口" in item for item in captured))
        self.assertTrue(any("qBittorrent WebUI 来源地址" in item for item in captured))

    def test_delivery_configuration_rejects_invalid_qb_origin(self):
        prompts = iter(["/data/releases", "8081", "http://qb.example.test"])
        with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: next(prompts)):
            with self.assertRaisesRegex(ValueError, "HTTPS origin"):
                _delivery_public_values({}, "uk-vps", edit_existing=False)

    def test_delivery_configure_flag_is_explicit(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["ws", "end", "--configure"])

    def test_delivery_parser_supports_yes_and_recovery_modes(self):
        args = build_parser().parse_args(["ws", "end", "yes"])
        self.assertEqual(args.unattended, "yes")
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["ws", "end", "yes", "--resume"])

    def test_yes_mode_defaults_to_nyaa_without_prompting(self):
        with patch("bmlsub.cli._confirm_stderr", side_effect=AssertionError("prompted")):
            self.assertTrue(_select_nyaa_syndication(unattended=True))

    def test_interactive_nyaa_selection_uses_whitelist_confirmation(self):
        with patch("bmlsub.cli.sys.stdin.isatty", return_value=True), \
             patch("bmlsub.cli._confirm_stderr", side_effect=(True, False)) as confirm:
            self.assertTrue(_select_nyaa_syndication(unattended=False))
            self.assertFalse(_select_nyaa_syndication(unattended=False))
        self.assertIn("Nyaa", confirm.call_args_list[0].args[0])

    def test_noninteractive_non_yes_mode_does_not_enable_nyaa(self):
        with patch("bmlsub.cli.sys.stdin.isatty", return_value=False), \
             patch("bmlsub.cli._confirm_stderr", side_effect=AssertionError("prompted")):
            self.assertFalse(_select_nyaa_syndication(unattended=False))

    def test_default_publish_plan_is_concise(self):
        plan = {
            "episode_dir": "/series/01",
            "anibt": {"nyaa": True, "nyaa_category": "1_4"},
            "config": {
                "r2_bucket": "bml", "remote_dir": "/host/downloads",
                "qb_save_path": "/downloads", "r2_credential_profile": "r2",
                "ssh_profile": "ssh", "qb_credential_profile": "qb",
                "anibt_credential_profile": "anibt",
            },
            "deliveries": [{
                "product_key": "mkv_hevc", "content_path": "/very/long/video.mkv",
                "torrent_path": "/very/long/video.mkv.torrent",
                "r2_object_key": "series/01/video.mkv",
                "r2_torrent_object_key": "series/01/video.mkv.torrent",
                "remote_content_path": "/host/downloads/video.mkv",
                "remote_torrent_path": "/host/downloads/video.mkv.torrent",
            }],
            "missing": [],
        }
        output = io.StringIO()
        with redirect_stderr(output):
            _print_publish_plan(plan)
        text = output.getvalue()
        self.assertIn("文件交付摘要", text)
        self.assertIn("Nyaa", text)
        self.assertIn("1_4", text)
        self.assertNotIn("/very/long/video.mkv", text)
        output = io.StringIO()
        with redirect_stderr(output):
            _print_publish_plan(plan, verbose=True)
        self.assertIn("/very/long/video.mkv", output.getvalue())
    def test_prompt_default_explicitly_describes_enter_in_both_languages(self):
        for language, expected in (("zh", "直接按 Enter 使用默认值：main"),
                                   ("en", "Press Enter to use the default: main")):
            set_ui_language(language)
            prompts = []
            with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: prompts.append(prompt) or ""):
                self.assertEqual(_prompt_default("Namespace", "main"), "main")
            self.assertIn(expected, prompts[0])

    def test_language_selector_defaults_to_chinese_and_supports_english(self):
        with patch("bmlsub.cli._prompt_stderr", return_value=""):
            self.assertEqual(_select_ui_language(), "zh")
        with patch("bmlsub.cli._prompt_stderr", return_value="2"):
            self.assertEqual(_select_ui_language(), "en")

    def test_ssh_profile_name_and_openssh_alias_are_distinct_prompts(self):
        set_ui_language("zh")
        service = FakeCredentialService([])
        prompts = iter(["staging-vps-profile", "media-vps"])
        captured = []
        with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: captured.append(prompt) or next(prompts)), \
             patch("bmlsub.credentials.SSHConfigResolver.resolve") as resolve:
            resolve.return_value.bounded.return_value = {"host": "example.test"}
            resolve.return_value.host = "example.test"
            resolve.return_value.user = "root"
            resolve.return_value.port = 22
            result = _choose_or_create_profile(service, "ssh")
        self.assertEqual(result, ("staging-vps-profile", "created"))
        self.assertEqual(service.created[0]["alias"], "staging-vps-profile")
        self.assertEqual(service.created[0]["settings"]["ssh_alias"], "media-vps")
        self.assertIn("凭据配置名称", captured[0])
        self.assertIn("不是 bmlsub 凭据配置名称", captured[1])

    def test_new_r2_prompts_full_keychain_payload(self):
        service = FakeCredentialService([])
        prompts = iter(["r2-main", "account", "access-id", ""])
        with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: next(prompts)), \
             patch("bmlsub.cli._secret_stderr", return_value="secret-access"):
            result = _choose_or_create_profile(service, "r2")
        self.assertEqual(result, ("r2-main", "created"))
        self.assertEqual(service.created[0]["secret"], {
            "account_id": "account", "access_key_id": "access-id",
            "secret_access_key": "secret-access",
        })
        self.assertEqual(service.validated, ["r2-main"])

    def test_new_qbittorrent_prompts_username_and_hidden_password(self):
        service = FakeCredentialService([])
        prompts = iter(["qb-main", "operator"])
        with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: next(prompts)), \
             patch("bmlsub.cli._secret_stderr", return_value="qb-password"):
            result = _choose_or_create_profile(service, "qbittorrent")
        self.assertEqual(result, ("qb-main", "created"))
        self.assertEqual(service.created[0]["secret"], {
            "username": "operator", "password": "qb-password",
        })
        self.assertEqual(service.validated, ["qb-main"])

    def test_qbittorrent_profile_menu_names_username_password_input(self):
        set_ui_language("zh")
        service = FakeCredentialService([
            {"alias": "qb-main", "kind": "qbittorrent", "available": True},
        ])
        output = io.StringIO()
        prompts = iter(["2", "qb-new", "operator"])
        with redirect_stderr(output), \
             patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: next(prompts)), \
             patch("bmlsub.cli._secret_stderr", return_value="qb-password"):
            result = _choose_or_create_profile(service, "qbittorrent", reselect=True)
        self.assertEqual(result, ("qb-new", "created"))
        self.assertIn("输入 qBittorrent 用户名和密码", output.getvalue())

    def test_available_r2_is_reused_without_secret_prompt(self):
        service = FakeCredentialService([{"alias": "r2-main", "kind": "r2", "available": True}])
        with patch("bmlsub.cli._prompt_stderr", return_value=""), \
             patch("bmlsub.cli._secret_stderr", side_effect=AssertionError("secret prompted")):
            result = _choose_or_create_profile(service, "r2")
        self.assertEqual(result, ("r2-main", "reused"))
        self.assertEqual(service.created, [])
        self.assertEqual(service.updated, [])

    def test_unavailable_r2_is_repaired_with_full_keychain_payload(self):
        service = FakeCredentialService([{"alias": "r2-old", "kind": "r2", "available": False}])
        prompts = iter(["1", "account", "access-id", ""])
        with patch("bmlsub.cli._prompt_stderr", side_effect=lambda prompt: next(prompts)), \
             patch("bmlsub.cli._secret_stderr", return_value="secret-access"):
            result = _choose_or_create_profile(service, "r2")
        self.assertEqual(result, ("r2-old", "repaired"))
        self.assertEqual(service.created, [])
        self.assertEqual(service.updated[0][0], "r2-old")
        self.assertEqual(service.updated[0][1]["secret"]["secret_access_key"], "secret-access")
        self.assertEqual(service.validated, ["r2-old"])


if __name__ == "__main__":
    unittest.main()
