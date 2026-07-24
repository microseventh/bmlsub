from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from bmlsub.state.fingerprints import fingerprint_file
from bmlsub.state.models import ArtifactRecord, ValidationStatus
from bmlsub.state.sqlite_store import SQLiteJobStore
from bmlsub.transcription.text_export import run_transcript_text_export


class TranscriptTextExportTests(unittest.TestCase):
    def test_empty_segments_export_to_empty_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "workstation" / "state"
            store = SQLiteJobStore.for_workspace(root, state)
            store.initialize()
            run = store.create_run(root, "test", episode_id="01")
            stage = store.create_stage(
                run.run_id, "test.transcript",
                input_fingerprint="input", parameter_fingerprint="parameters",
                tool_fingerprint="tools",
            )
            store.mark_stage_running(stage.stage_id)
            transcript = root / "source.transcript.json"
            transcript.write_text(json.dumps({
                "schema_version": "transcript-v1",
                "mode": "direct", "model": "model", "model_revision": "main",
                "language": "ja", "segments": [], "text": "",
            }), encoding="utf-8")
            fingerprint = fingerprint_file(transcript, content_hash=True)
            artifact = ArtifactRecord(
                artifact_id="transcript", run_id=run.run_id, stage_id=stage.stage_id,
                episode_id="01", artifact_type="generated.transcript.direct",
                path=transcript, size=fingerprint.size, mtime_ns=fingerprint.mtime_ns,
                content_hash=fingerprint.content_hash,
                source_fingerprint="input", parameter_fingerprint="parameters",
                validation_status=ValidationStatus.VALID,
                created_at=datetime.now(timezone.utc),
                metadata={"mode": "direct", "model": "model",
                          "model_revision": "main", "language": "ja"},
            )
            store.register_artifact(artifact)
            store.complete_stage(stage.stage_id)

            result = run_transcript_text_export(
                workspace=root, episode_id="01", transcript_artifact_id="transcript",
                mode="direct", model="model", job_name="direct",
                model_revision="main", language="ja", store=store,
            )

            self.assertEqual(result["status"], "succeeded")
            output = Path(result["artifacts"][0]["path"])
            self.assertEqual(output.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
