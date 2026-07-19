from __future__ import annotations

import unittest
from unittest.mock import patch

from bmlsub.cli import (
    _choose_or_create_profile, _prompt_default, _select_ui_language,
    _print_publish_plan, build_parser,
)
from bmlsub.interactive import set_ui_language
from contextlib import redirect_stderr
import io


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
    def test_delivery_configure_flag_is_explicit(self):
        args = build_parser().parse_args(["workstation", "start", "delivery", "--configure"])
        self.assertTrue(args.configure)

    def test_delivery_parser_supports_yes_and_recovery_modes(self):
        args = build_parser().parse_args([
            "workstation", "start", "delivery", "-y", "--resume", "--verbose-plan",
        ])
        self.assertTrue(args.yes)
        self.assertTrue(args.resume)
        self.assertTrue(args.verbose_plan)

    def test_default_publish_plan_is_concise(self):
        plan = {
            "episode_dir": "/series/01",
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
