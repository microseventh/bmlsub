from __future__ import annotations

import unittest

from bmlsub.release.anibt import RequestsAnibtClient
from bmlsub.release.external_profiles import AnibtPublishProfile


class AnibtMultipartTests(unittest.TestCase):
    def test_nyaa_syndication_serializes_required_true_and_false_flags(self):
        profile = AnibtPublishProfile(
            anime_id="12345",
            title="Example 01",
            notes="release notes",
            trackers=(
                "https://tracker.anibt.net/announce",
                "http://nyaa.tracker.wf:7777/announce",
            ),
            nyaa=True,
            nyaa_category="1_4",
            nyaa_complete=False,
            nyaa_remake=False,
        )

        fields = RequestsAnibtClient._multipart_fields(profile)

        self.assertIn(("nyaa", "true"), fields)
        self.assertIn(("nyaaCategory", "1_4"), fields)
        self.assertIn(("nyaaComplete", "false"), fields)
        self.assertIn(("nyaaRemake", "false"), fields)
        self.assertIn(("notes", "release notes"), fields)
        self.assertFalse(any(name == "nyaaDescription" for name, _ in fields))

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
            nyaa_category="1_4",
        )

        self.assertNotEqual(plain.normalized(), syndicated.normalized())


if __name__ == "__main__":
    unittest.main()
