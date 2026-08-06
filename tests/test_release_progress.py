from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bmlsub.progress import progress_reporter
from bmlsub.release.external_profiles import R2UploadProfile
from bmlsub.release.r2 import Boto3R2Client


class _Collector:
    def __init__(self) -> None:
        self.events = []

    def report(self, event) -> None:
        self.events.append(event)


class _S3Client:
    def upload_file(self, filename, bucket, object_key, **kwargs) -> None:
        self.call = (filename, bucket, object_key, kwargs)
        callback = kwargs["Callback"]
        callback(4)
        callback(6)


class ReleaseProgressTests(unittest.TestCase):
    def test_r2_upload_reports_real_transferred_bytes(self):
        client = Boto3R2Client.__new__(Boto3R2Client)
        client.client = _S3Client()
        collector = _Collector()
        profile = R2UploadProfile(bucket="release", object_key="01/video.mkv")

        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "video.mkv"
            source.write_bytes(b"0123456789")
            with progress_reporter(collector):
                client.upload(source, profile, metadata={"sha256": "test"})

        self.assertEqual([event.current for event in collector.events], [4, 10])
        self.assertTrue(all(event.total == 10 for event in collector.events))
        self.assertTrue(all(event.unit == "bytes" for event in collector.events))


if __name__ == "__main__":
    unittest.main()
