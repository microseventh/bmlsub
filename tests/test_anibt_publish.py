from __future__ import annotations

from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

from bmlsub.release.anibt import (
    ANIBT_LEGACY_ARRAY_MODE,
    ANIBT_REPEATED_FIELDS_MODE,
    RequestsAnibtClient,
)
from bmlsub.release.external_profiles import AnibtPublishProfile


class AnibtMultipartTests(unittest.TestCase):
    def _profile(self):
        return AnibtPublishProfile(
            anime_id="12345",
            title="Example 01",
            notes="release notes",
            language=("CHS", "JP"),
            trackers=(
                "https://tracker.anibt.net/announce",
                "http://nyaa.tracker.wf:7777/announce",
            ),
            nyaa=True,
            nyaa_category="1_3",
            nyaa_complete=False,
            nyaa_remake=False,
        )

    def test_nyaa_syndication_serializes_legacy_json_arrays_by_default(self):
        profile = self._profile()

        fields = RequestsAnibtClient._multipart_fields(profile)

        self.assertIn(("nyaa", "true"), fields)
        self.assertIn(("nyaaCategory", "1_3"), fields)
        self.assertIn(("nyaaComplete", "false"), fields)
        self.assertIn(("nyaaRemake", "false"), fields)
        self.assertIn(("notes", "release notes"), fields)
        self.assertFalse(any(name == "nyaaDescription" for name, _ in fields))
        self.assertEqual(dict(fields)["language"], '["CHS", "JP"]')
        self.assertEqual(
            dict(fields)["trackers"],
            '["https://tracker.anibt.net/announce", "http://nyaa.tracker.wf:7777/announce"]',
        )

    def test_nyaa_syndication_serializes_repeated_arrays_as_fallback(self):
        profile = AnibtPublishProfile(
            anime_id="12345",
            title="Example 01",
            notes="release notes",
            language=("CHS", "JP"),
            trackers=(
                "https://tracker.anibt.net/announce",
                "http://nyaa.tracker.wf:7777/announce",
            ),
            nyaa=True,
            nyaa_category="1_3",
            nyaa_complete=False,
            nyaa_remake=False,
        )

        fields = RequestsAnibtClient._multipart_fields(
            profile, mode=ANIBT_REPEATED_FIELDS_MODE,
        )

        self.assertIn(("nyaa", "true"), fields)
        self.assertIn(("nyaaCategory", "1_3"), fields)
        self.assertIn(("nyaaComplete", "false"), fields)
        self.assertIn(("nyaaRemake", "false"), fields)
        self.assertIn(("notes", "release notes"), fields)
        self.assertFalse(any(name == "nyaaDescription" for name, _ in fields))
        self.assertEqual(
            [value for name, value in fields if name == "language"],
            ["CHS", "JP"],
        )
        self.assertEqual(
            [value for name, value in fields if name == "trackers"],
            [
                "https://tracker.anibt.net/announce",
                "http://nyaa.tracker.wf:7777/announce",
            ],
        )

    def test_publish_falls_back_to_repeated_fields_after_legacy_failure(self):
        profile = AnibtPublishProfile(
            anime_id="12345", title="Example 01", language=("CHS", "JP"),
        )
        response_error = requests.Response()
        response_error.status_code = 422
        response_error._content = b'{"ok":false,"message":"Invalid request body"}'
        response_ok = requests.Response()
        response_ok.status_code = 200
        response_ok._content = b'{"ok":true,"data":{"releaseId":"release-1"}}'
        with TemporaryDirectory() as directory:
            torrent = Path(directory) / "file.torrent"
            torrent.write_bytes(b"torrent")
            with patch(
                "bmlsub.release.anibt.requests.post",
                side_effect=[response_error, response_ok],
            ) as post:
                client = RequestsAnibtClient()
                result = client.publish(
                    torrent_path=torrent, profile=profile,
                    api_url="https://anibt.test/api/releases/publish", token="token",
                )

        self.assertEqual(result["data"]["releaseId"], "release-1")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[0].kwargs["data"],
            RequestsAnibtClient._multipart_fields(
                AnibtPublishProfile(
                    anime_id="12345", title="Example 01", language=("CHS", "JP"),
                    trackers=("https://tracker.anibt.net/announce",),
                ), mode=ANIBT_LEGACY_ARRAY_MODE,
            ),
        )
        self.assertEqual(
            post.call_args_list[1].kwargs["data"],
            RequestsAnibtClient._multipart_fields(
                AnibtPublishProfile(
                    anime_id="12345", title="Example 01", language=("CHS", "JP"),
                    trackers=("https://tracker.anibt.net/announce",),
                ), mode=ANIBT_REPEATED_FIELDS_MODE,
            ),
        )
        self.assertEqual(client.last_publish_mode, ANIBT_REPEATED_FIELDS_MODE)

    def test_publish_does_not_fallback_after_success(self):
        profile = AnibtPublishProfile(anime_id="12345", title="Example 01")
        response_ok = requests.Response()
        response_ok.status_code = 200
        response_ok._content = b'{"ok":true}'
        with TemporaryDirectory() as directory:
            torrent = Path(directory) / "file.torrent"
            torrent.write_bytes(b"torrent")
            with patch("bmlsub.release.anibt.requests.post", return_value=response_ok) as post:
                client = RequestsAnibtClient()
                client.publish(
                    torrent_path=torrent, profile=profile,
                    api_url="https://anibt.test/api/releases/publish", token="token",
                )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(client.last_publish_mode, ANIBT_LEGACY_ARRAY_MODE)

    def test_anibt_only_profile_omits_nyaa_fields(self):
        profile = AnibtPublishProfile(
            anime_id="12345",
            title="Example 01",
        )

        fields = RequestsAnibtClient._multipart_fields(profile)
        names = {name for name, _ in fields}

        self.assertNotIn("nyaa", names)
        self.assertNotIn("nyaaCategory", names)
        self.assertNotIn("nyaaComplete", names)
        self.assertNotIn("nyaaRemake", names)

    def test_nyaa_changes_normalized_profile_identity(self):
        plain = AnibtPublishProfile(anime_id="12345", title="Example 01")
        syndicated = AnibtPublishProfile(
            anime_id="12345",
            title="Example 01",
            trackers=("http://nyaa.tracker.wf:7777/announce",),
            nyaa=True,
            nyaa_category="1_3",
        )

        self.assertNotEqual(plain.normalized(), syndicated.normalized())


if __name__ == "__main__":
    unittest.main()
