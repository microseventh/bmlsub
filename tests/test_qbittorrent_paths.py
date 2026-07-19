from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmlsub.execution.errors import BmlsubError
from bmlsub.release.external_profiles import QBittorrentSeedProfile
from bmlsub.release.qbittorrent import SSHQBittorrentClient, SeedIdentity


class _Response:
    def __init__(self, status_code=200, *, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class _Session:
    def __init__(self, rows, *, start_status=200):
        self.rows = list(rows)
        self.start_status = start_status
        self.posts = []

    def get(self, url, *, params=None, timeout=None):
        if url.endswith("/torrents/info"):
            row = self.rows.pop(0) if self.rows else None
            return _Response(payload=[] if row is None else [row])
        raise AssertionError(url)

    def post(self, url, *, data=None, files=None, timeout=None):
        self.posts.append((url.rsplit("/", 1)[-1], dict(data or {}), files))
        if url.endswith("/torrents/start"):
            return _Response(self.start_status)
        return _Response(200, text="Ok." if url.endswith("/torrents/add") else "")


def _row(*, save_path="/downloads", progress=1.0, amount_left=0, state="stalledUP"):
    return {
        "hash": "a" * 40,
        "name": "release.mkv",
        "total_size": 123,
        "save_path": save_path,
        "progress": progress,
        "amount_left": amount_left,
        "state": state,
    }


class QBittorrentPathTests(unittest.TestCase):
    def test_add_fields_are_explicit(self):
        profile = QBittorrentSeedProfile(ssh_alias="media-vps", save_path="/downloads")
        fields = SSHQBittorrentClient._add_fields(profile)
        self.assertEqual(fields["savepath"], "/downloads")
        for key in (
            "paused", "skip_checking", "sequentialDownload",
            "firstLastPiecePrio", "root_folder",
        ):
            self.assertEqual(fields[key], "false")

    def test_start_uses_v5_endpoint_and_resume_fallback(self):
        session = _Session([], start_status=404)
        SSHQBittorrentClient._start_task(session, "http://qb", "a" * 40)
        self.assertEqual([item[0] for item in session.posts], ["start", "resume"])

    def test_legacy_host_path_is_replaced_without_deleting_files(self):
        session = _Session([
            _row(save_path="/data/dcapp/qb/downloads", progress=0.0,
                 amount_left=123, state="stalledDL"),
            _row(save_path="/downloads", progress=0.0, amount_left=123,
                 state="checkingDL"),
            _row(save_path="/downloads"),
        ])
        client = SSHQBittorrentClient.__new__(SSHQBittorrentClient)

        @contextmanager
        def fake_session(profile):
            yield session, "http://qb"

        client._session = fake_session
        profile = QBittorrentSeedProfile(
            ssh_alias="media-vps", save_path="/downloads",
            legacy_host_save_path="/data/dcapp/qb/downloads",
            poll_interval=0.2, poll_timeout=2,
        )
        with TemporaryDirectory() as temporary, patch("bmlsub.release.qbittorrent.time.sleep"):
            torrent = Path(temporary) / "release.torrent"
            torrent.write_bytes(b"torrent")
            identity = client.add_and_verify(
                torrent_path=torrent, magnet_uri="magnet:?xt=urn:btih:" + "a" * 40,
                expected_hash="a" * 40, expected_name="release.mkv",
                expected_size=123, profile=profile,
            )
        self.assertEqual(identity.save_path, "/downloads")
        delete = next(item for item in session.posts if item[0] == "delete")
        self.assertEqual(delete[1]["deleteFiles"], "false")
        self.assertIn("add", [item[0] for item in session.posts])
        self.assertIn("start", [item[0] for item in session.posts])
        self.assertIn("recheck", [item[0] for item in session.posts])

    def test_unknown_save_path_blocks_without_delete(self):
        session = _Session([_row(save_path="/unexpected")])
        client = SSHQBittorrentClient.__new__(SSHQBittorrentClient)

        @contextmanager
        def fake_session(profile):
            yield session, "http://qb"

        client._session = fake_session
        profile = QBittorrentSeedProfile(
            ssh_alias="media-vps", save_path="/downloads",
            legacy_host_save_path="/data/dcapp/qb/downloads",
        )
        with TemporaryDirectory() as temporary:
            torrent = Path(temporary) / "release.torrent"
            torrent.write_bytes(b"torrent")
            with self.assertRaises(BmlsubError):
                client.add_and_verify(
                    torrent_path=torrent, magnet_uri="magnet:?xt=urn:btih:" + "a" * 40,
                    expected_hash="a" * 40, expected_name="release.mkv",
                    expected_size=123, profile=profile,
                )
        self.assertNotIn("delete", [item[0] for item in session.posts])


if __name__ == "__main__":
    unittest.main()
