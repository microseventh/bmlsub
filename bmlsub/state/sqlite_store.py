"""SQLite-backed local run, stage, artifact, and event ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..execution.errors import SchemaVersionError, StateStoreError, StateTransitionError
from ..production.models import (
    ProductionOperation,
    ProductionRequestInput,
    ProductionRequestRecord,
    ProductionRequestStatus,
)
from .models import (
    ArtifactPurpose,
    ArtifactRecord,
    AssetMatchCandidateRecord,
    AssetMatchSelectionRecord,
    AssetMatchSetRecord,
    AssetMatchStatus,
    Diagnostic,
    RunRecord,
    RunStatus,
    StageInputBinding,
    StageInputRecord,
    StageRecord,
    StageStatus,
    ValidationStatus,
    _assert_no_secret_keys,
    datetime_from_text,
    datetime_to_text,
    utc_now,
)
from .paths import state_database_path


SCHEMA_VERSION = 4

_RUN_TRANSITIONS = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED,
                        RunStatus.NEEDS_REVIEW, RunStatus.INTERRUPTED},
    RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED,
                        RunStatus.NEEDS_REVIEW, RunStatus.INTERRUPTED},
}
_STAGE_TRANSITIONS = {
    StageStatus.PENDING: {StageStatus.RUNNING, StageStatus.FAILED,
                          StageStatus.SKIPPED, StageStatus.NEEDS_REVIEW},
    StageStatus.RUNNING: {StageStatus.SUCCEEDED, StageStatus.FAILED,
                          StageStatus.SKIPPED, StageStatus.NEEDS_REVIEW},
    StageStatus.SUCCEEDED: {StageStatus.STALE},
    StageStatus.SKIPPED: {StageStatus.STALE},
}

_PRODUCTION_REQUEST_TRANSITIONS = {
    ProductionRequestStatus.PENDING: {
        ProductionRequestStatus.RUNNING,
        ProductionRequestStatus.FAILED,
        ProductionRequestStatus.NEEDS_REVIEW,
    },
    ProductionRequestStatus.RUNNING: {
        ProductionRequestStatus.SUCCEEDED,
        ProductionRequestStatus.FAILED,
        ProductionRequestStatus.NEEDS_REVIEW,
    },
    ProductionRequestStatus.SUCCEEDED: {ProductionRequestStatus.RUNNING},
    ProductionRequestStatus.FAILED: {ProductionRequestStatus.RUNNING},
    ProductionRequestStatus.NEEDS_REVIEW: {ProductionRequestStatus.RUNNING},
}

_SCHEMA_SQL = """
CREATE TABLE schema_info (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    episode_id TEXT,
    command_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'needs_review', 'interrupted')),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_code TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE stages (
    stage_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped', 'stale', 'needs_review')),
    input_fingerprint TEXT,
    parameter_fingerprint TEXT,
    tool_fingerprint TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
    needs_review INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0, 1)),
    error_code TEXT,
    diagnostics_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (run_id, stage_name)
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage_id TEXT NOT NULL REFERENCES stages(stage_id) ON DELETE CASCADE,
    episode_id TEXT,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    mtime_ns INTEGER NOT NULL CHECK (mtime_ns >= 0),
    content_hash TEXT,
    source_fingerprint TEXT,
    parameter_fingerprint TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    superseded_by_artifact_id TEXT REFERENCES artifacts(artifact_id),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('discovered', 'unverified', 'valid', 'invalid', 'stale')),
    created_at TEXT NOT NULL,
    UNIQUE (stage_id, artifact_type, path)
);

CREATE TABLE artifact_purposes (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    PRIMARY KEY (artifact_id, purpose)
);

CREATE TABLE stage_inputs (
    stage_id TEXT NOT NULL REFERENCES stages(stage_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    input_role TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    PRIMARY KEY (stage_id, input_role, ordinal)
);

CREATE TABLE asset_match_sets (
    match_set_id TEXT PRIMARY KEY,
    stage_id TEXT NOT NULL REFERENCES stages(stage_id) ON DELETE CASCADE,
    episode_id TEXT NOT NULL,
    anchor_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    input_role TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'inferred', 'ambiguous', 'unmatched')),
    rule_version TEXT NOT NULL,
    superseded_by_match_set_id TEXT REFERENCES asset_match_sets(match_set_id),
    created_at TEXT NOT NULL
);

CREATE TABLE asset_match_candidates (
    match_set_id TEXT NOT NULL REFERENCES asset_match_sets(match_set_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    rank INTEGER NOT NULL CHECK (rank >= 0),
    score INTEGER NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (match_set_id, artifact_id),
    UNIQUE (match_set_id, rank)
);

CREATE TABLE asset_match_selections (
    match_set_id TEXT NOT NULL REFERENCES asset_match_sets(match_set_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    input_role TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    stage_id TEXT NOT NULL REFERENCES stages(stage_id),
    PRIMARY KEY (match_set_id, input_role, ordinal)
);

CREATE TABLE production_requests (
    request_id TEXT PRIMARY KEY,
    workspace_path TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('encode', 'remux', 'hardsub', 'mux_subtitle')),
    output_profile TEXT NOT NULL,
    output_target TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'needs_review')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_id TEXT REFERENCES runs(run_id),
    stage_id TEXT REFERENCES stages(stage_id),
    artifact_id TEXT REFERENCES artifacts(artifact_id),
    error_code TEXT
);

CREATE TABLE production_request_inputs (
    request_id TEXT NOT NULL REFERENCES production_requests(request_id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    input_role TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    PRIMARY KEY (request_id, input_role, ordinal)
);

CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage_id TEXT REFERENCES stages(stage_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_runs_episode_created ON runs(episode_id, created_at DESC);
CREATE INDEX idx_stages_reusable ON stages(stage_name, status, input_fingerprint, parameter_fingerprint, tool_fingerprint, finished_at DESC);
CREATE INDEX idx_stages_run ON stages(run_id, created_at);
CREATE INDEX idx_artifacts_stage ON artifacts(stage_id, created_at);
CREATE INDEX idx_artifacts_episode_type ON artifacts(episode_id, artifact_type, validation_status, created_at DESC);
CREATE INDEX idx_artifact_purposes_lookup ON artifact_purposes(purpose, is_default, artifact_id);
CREATE INDEX idx_stage_inputs_stage ON stage_inputs(stage_id, input_role, ordinal);
CREATE INDEX idx_stage_inputs_artifact ON stage_inputs(artifact_id, stage_id);
CREATE INDEX idx_match_sets_current ON asset_match_sets(episode_id, anchor_artifact_id, input_role, superseded_by_match_set_id, created_at DESC);
CREATE INDEX idx_match_candidates_artifact ON asset_match_candidates(artifact_id, match_set_id);
CREATE INDEX idx_match_selections_artifact ON asset_match_selections(artifact_id, match_set_id);
CREATE INDEX idx_production_requests_episode ON production_requests(episode_id, created_at DESC);
CREATE INDEX idx_production_request_inputs_artifact ON production_request_inputs(artifact_id, request_id);
CREATE INDEX idx_events_run ON events(run_id, created_at);
"""


class SQLiteJobStore:
    """Small synchronous SQLite ledger for single-machine execution state."""

    def __init__(self, database_path: Path | str, *, timeout: float = 5.0) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.timeout = timeout

    @classmethod
    def for_workspace(cls, workspace: Path | str, state_dir: Path | str | None = None,
                      *, timeout: float = 5.0) -> "SQLiteJobStore":
        return cls(state_database_path(workspace, state_dir), timeout=timeout)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                has_schema = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_info'"
                ).fetchone()
                if not has_schema:
                    connection.executescript(_SCHEMA_SQL)
                    connection.execute(
                        "INSERT INTO schema_info(singleton, version, applied_at) VALUES (1, ?, ?)",
                        (SCHEMA_VERSION, datetime_to_text(utc_now())),
                    )
                else:
                    version = self._read_version(connection)
                    if version == 1:
                        self._migrate_v1_to_v2(connection)
                        self._migrate_v2_to_v3(connection)
                        self._migrate_v3_to_v4(connection)
                    elif version == 2:
                        self._migrate_v2_to_v3(connection)
                        self._migrate_v3_to_v4(connection)
                    elif version == 3:
                        self._migrate_v3_to_v4(connection)
                    else:
                        self._check_version(connection)
                connection.commit()
        except (SchemaVersionError, sqlite3.Error):
            raise

    def schema_version(self) -> int:
        with self._connect() as connection:
            return self._check_version(connection)

    def create_run(self, workspace_path: Path | str, command_name: str, *,
                   episode_id: str | None = None,
                   metadata: Mapping[str, Any] | None = None,
                   run_id: str | None = None,
                   status: RunStatus = RunStatus.RUNNING,
                   now: datetime | None = None) -> RunRecord:
        self.initialize()
        if status not in {RunStatus.PENDING, RunStatus.RUNNING}:
            raise StateTransitionError("new run must start as pending or running")
        safe_metadata = dict(metadata or {})
        _assert_no_secret_keys(safe_metadata)
        timestamp = now or utc_now()
        record = RunRecord(
            run_id=run_id or uuid.uuid4().hex,
            workspace_path=Path(workspace_path),
            episode_id=episode_id,
            command_name=command_name,
            status=status,
            created_at=timestamp,
            started_at=timestamp if status is RunStatus.RUNNING else None,
            metadata=safe_metadata,
        )
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO runs(
                    run_id, workspace_path, episode_id, command_name, status, created_at,
                    started_at, finished_at, error_code, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.run_id, str(record.workspace_path), record.episode_id,
                    record.command_name, record.status.value, datetime_to_text(record.created_at),
                    datetime_to_text(record.started_at), None, None,
                    self._json(dict(record.metadata)),
                ),
            )
            self._add_event(connection, record.run_id, None, "run_created", {"status": status.value})
        return record

    def finish_run(self, run_id: str, status: RunStatus, *, error_code: str | None = None,
                   now: datetime | None = None) -> RunRecord:
        if status not in {RunStatus.SUCCEEDED, RunStatus.FAILED,
                          RunStatus.NEEDS_REVIEW, RunStatus.INTERRUPTED}:
            raise StateTransitionError(f"run cannot finish with status {status.value}")
        with self._transaction() as connection:
            row = self._require_row(connection, "SELECT * FROM runs WHERE run_id = ?", (run_id,), "run")
            current = RunStatus(row["status"])
            self._validate_transition(current, status, _RUN_TRANSITIONS, "run")
            finished_at = now or utc_now()
            connection.execute(
                "UPDATE runs SET status = ?, finished_at = ?, error_code = ? WHERE run_id = ?",
                (status.value, datetime_to_text(finished_at), error_code, run_id),
            )
            self._add_event(connection, run_id, None, f"run_{status.value}", {"error_code": error_code})
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_from_row(updated)

    def create_stage(self, run_id: str, stage_name: str, *,
                     input_fingerprint: str | None = None,
                     parameter_fingerprint: str | None = None,
                     tool_fingerprint: str | None = None,
                     stage_id: str | None = None,
                     now: datetime | None = None) -> StageRecord:
        timestamp = now or utc_now()
        record = StageRecord(
            stage_id=stage_id or uuid.uuid4().hex,
            run_id=run_id,
            stage_name=stage_name,
            status=StageStatus.PENDING,
            created_at=timestamp,
            input_fingerprint=input_fingerprint,
            parameter_fingerprint=parameter_fingerprint,
            tool_fingerprint=tool_fingerprint,
        )
        with self._transaction() as connection:
            self._require_row(connection, "SELECT run_id FROM runs WHERE run_id = ?", (run_id,), "run")
            connection.execute(
                """INSERT INTO stages(
                    stage_id, run_id, stage_name, status, input_fingerprint,
                    parameter_fingerprint, tool_fingerprint, created_at, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')""",
                (
                    record.stage_id, record.run_id, record.stage_name, record.status.value,
                    record.input_fingerprint, record.parameter_fingerprint,
                    record.tool_fingerprint, datetime_to_text(record.created_at),
                ),
            )
        return record

    def mark_stage_running(self, stage_id: str, *, now: datetime | None = None) -> StageRecord:
        return self._transition_stage(stage_id, StageStatus.RUNNING, now=now)

    def complete_stage(self, stage_id: str, *,
                       diagnostics: Sequence[Diagnostic] = (),
                       now: datetime | None = None) -> StageRecord:
        return self._transition_stage(
            stage_id, StageStatus.SUCCEEDED, diagnostics=diagnostics, now=now
        )

    def fail_stage(self, stage_id: str, error_code: str, *, retryable: bool = False,
                   diagnostics: Sequence[Diagnostic] = (),
                   now: datetime | None = None) -> StageRecord:
        return self._transition_stage(
            stage_id, StageStatus.FAILED, error_code=error_code, retryable=retryable,
            diagnostics=diagnostics, now=now,
        )

    def require_review(self, stage_id: str, *, error_code: str = "review_required",
                       diagnostics: Sequence[Diagnostic] = (),
                       now: datetime | None = None) -> StageRecord:
        return self._transition_stage(
            stage_id, StageStatus.NEEDS_REVIEW, error_code=error_code,
            needs_review=True, diagnostics=diagnostics, now=now,
        )

    def skip_stage(self, stage_id: str, *, diagnostics: Sequence[Diagnostic] = (),
                   now: datetime | None = None) -> StageRecord:
        return self._transition_stage(
            stage_id, StageStatus.SKIPPED, diagnostics=diagnostics, now=now
        )

    def mark_stage_stale(self, stage_id: str, *, diagnostics: Sequence[Diagnostic] = (),
                         now: datetime | None = None) -> StageRecord:
        return self._transition_stage(
            stage_id, StageStatus.STALE, diagnostics=diagnostics, now=now
        )

    def register_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        with self._transaction() as connection:
            stage = self._require_row(
                connection, "SELECT run_id FROM stages WHERE stage_id = ?", (artifact.stage_id,), "stage"
            )
            if stage["run_id"] != artifact.run_id:
                raise StateStoreError("artifact run_id does not match its stage")
            connection.execute(
                """INSERT INTO artifacts(
                    artifact_id, run_id, stage_id, episode_id, artifact_type, path, size,
                    mtime_ns, content_hash, source_fingerprint, parameter_fingerprint,
                    metadata_json, superseded_by_artifact_id, validation_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact.artifact_id, artifact.run_id, artifact.stage_id, artifact.episode_id,
                    artifact.artifact_type, str(artifact.path), artifact.size, artifact.mtime_ns,
                    artifact.content_hash, artifact.source_fingerprint,
                    artifact.parameter_fingerprint, self._json(dict(artifact.metadata)),
                    artifact.superseded_by_artifact_id, artifact.validation_status.value,
                    datetime_to_text(artifact.created_at),
                ),
            )
            for purpose in artifact.purposes:
                if purpose.is_default and artifact.episode_id is not None:
                    connection.execute(
                        """UPDATE artifact_purposes SET is_default = 0
                           WHERE purpose = ? AND artifact_id IN (
                               SELECT artifact_id FROM artifacts
                               WHERE episode_id IS ? AND validation_status = ?
                           )""",
                        (purpose.purpose, artifact.episode_id, ValidationStatus.VALID.value),
                    )
                connection.execute(
                    "INSERT INTO artifact_purposes(artifact_id, purpose, is_default) VALUES (?, ?, ?)",
                    (artifact.artifact_id, purpose.purpose, int(purpose.is_default)),
                )
            connection.execute(
                """UPDATE artifacts SET validation_status = ?, superseded_by_artifact_id = ?
                   WHERE artifact_id != ? AND episode_id IS ? AND artifact_type = ? AND path = ?
                     AND validation_status = ?""",
                (
                    ValidationStatus.STALE.value, artifact.artifact_id, artifact.artifact_id,
                    artifact.episode_id, artifact.artifact_type, str(artifact.path),
                    ValidationStatus.VALID.value,
                ),
            )
            self._add_event(
                connection, artifact.run_id, artifact.stage_id, "artifact_committed",
                {"artifact_id": artifact.artifact_id, "artifact_type": artifact.artifact_type,
                 "path": str(artifact.path)},
            )
        return artifact

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return self._artifact_from_row(connection, row) if row else None

    def list_artifacts(self, *, episode_id: str | None = None,
                       artifact_type: str | None = None,
                       current_only: bool = True) -> list[ArtifactRecord]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if episode_id is not None:
            clauses.append("episode_id IS ?")
            parameters.append(episode_id)
        if artifact_type is not None:
            clauses.append("artifact_type = ?")
            parameters.append(artifact_type)
        if current_only:
            clauses.append("validation_status = ?")
            parameters.append(ValidationStatus.VALID.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts" + where + " ORDER BY created_at DESC, artifact_id",
                tuple(parameters),
            ).fetchall()
            return [self._artifact_from_row(connection, row) for row in rows]

    def resolve_artifact_by_purpose(self, episode_id: str, purpose: str, *,
                                    artifact_types: Sequence[str] = (
                                        "source.video", "reference.video",
                                    )) -> tuple[ArtifactRecord | None, bool]:
        placeholders = ",".join("?" for _ in artifact_types)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT a.*, p.is_default FROM artifacts a
                    JOIN artifact_purposes p ON p.artifact_id = a.artifact_id
                    WHERE a.episode_id IS ? AND p.purpose = ?
                      AND a.artifact_type IN ({placeholders})
                      AND a.validation_status = ?
                    ORDER BY p.is_default DESC, a.created_at DESC, a.artifact_id""",
                (episode_id, purpose, *artifact_types, ValidationStatus.VALID.value),
            ).fetchall()
            if not rows:
                return None, False
            defaults = [row for row in rows if bool(row["is_default"])]
            if len(defaults) == 1:
                return self._artifact_from_row(connection, defaults[0]), False
            if len(rows) == 1:
                return self._artifact_from_row(connection, rows[0]), False
            return None, True

    def register_stage_inputs(self, stage_id: str,
                              bindings: Sequence[StageInputBinding]) -> list[StageInputRecord]:
        records = [
            StageInputRecord(stage_id, item.artifact_id, item.input_role, item.ordinal)
            for item in bindings
        ]
        with self._transaction() as connection:
            self._require_row(connection, "SELECT stage_id FROM stages WHERE stage_id = ?", (stage_id,), "stage")
            for record in records:
                self._require_row(
                    connection, "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                    (record.artifact_id,), "artifact",
                )
                connection.execute(
                    "INSERT INTO stage_inputs(stage_id, artifact_id, input_role, ordinal) VALUES (?, ?, ?, ?)",
                    (record.stage_id, record.artifact_id, record.input_role, record.ordinal),
                )
        return records

    def get_stage_inputs(self, stage_id: str) -> list[StageInputRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT stage_id, artifact_id, input_role, ordinal FROM stage_inputs
                   WHERE stage_id = ? ORDER BY input_role, ordinal""",
                (stage_id,),
            ).fetchall()
        return [StageInputRecord(**dict(row)) for row in rows]

    def register_match_set(self, record: AssetMatchSetRecord, *, replace_confirmed: bool = False) -> AssetMatchSetRecord:
        with self._transaction() as connection:
            stage = self._require_row(
                connection, "SELECT run_id FROM stages WHERE stage_id = ?", (record.stage_id,), "stage"
            )
            del stage
            anchor = self._require_row(
                connection, "SELECT episode_id, validation_status FROM artifacts WHERE artifact_id = ?",
                (record.anchor_artifact_id,), "anchor artifact",
            )
            if anchor["episode_id"] != record.episode_id:
                raise StateStoreError("match anchor does not belong to the episode")
            current = connection.execute(
                """SELECT * FROM asset_match_sets WHERE episode_id = ? AND anchor_artifact_id = ?
                   AND input_role = ? AND superseded_by_match_set_id IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (record.episode_id, record.anchor_artifact_id, record.input_role),
            ).fetchone()
            if current is not None and current["status"] == AssetMatchStatus.CONFIRMED.value and not replace_confirmed:
                return self._match_set_from_row(connection, current)
            connection.execute(
                """INSERT INTO asset_match_sets(
                    match_set_id, stage_id, episode_id, anchor_artifact_id, input_role,
                    status, rule_version, superseded_by_match_set_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.match_set_id, record.stage_id, record.episode_id,
                    record.anchor_artifact_id, record.input_role, record.status.value,
                    record.rule_version, record.superseded_by_match_set_id,
                    datetime_to_text(record.created_at),
                ),
            )
            for candidate in record.candidates:
                artifact = self._require_row(
                    connection, "SELECT episode_id FROM artifacts WHERE artifact_id = ?",
                    (candidate.artifact_id,), "candidate artifact",
                )
                if artifact["episode_id"] != record.episode_id:
                    raise StateStoreError("match candidate does not belong to the episode")
                connection.execute(
                    """INSERT INTO asset_match_candidates(
                        match_set_id, artifact_id, rank, score, evidence_json
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        record.match_set_id, candidate.artifact_id, candidate.rank,
                        candidate.score, self._json(dict(candidate.evidence)),
                    ),
                )
            if current is not None:
                connection.execute(
                    "UPDATE asset_match_sets SET superseded_by_match_set_id = ? WHERE match_set_id = ?",
                    (record.match_set_id, current["match_set_id"]),
                )
            row = connection.execute(
                "SELECT * FROM asset_match_sets WHERE match_set_id = ?", (record.match_set_id,)
            ).fetchone()
            return self._match_set_from_row(connection, row)

    def get_current_match_set(self, episode_id: str, anchor_artifact_id: str,
                              input_role: str) -> AssetMatchSetRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM asset_match_sets WHERE episode_id = ? AND anchor_artifact_id = ?
                   AND input_role = ? AND superseded_by_match_set_id IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (episode_id, anchor_artifact_id, input_role),
            ).fetchone()
            return self._match_set_from_row(connection, row) if row else None

    def list_current_match_sets(self, episode_id: str) -> list[AssetMatchSetRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM asset_match_sets WHERE episode_id = ?
                   AND superseded_by_match_set_id IS NULL ORDER BY anchor_artifact_id, input_role""",
                (episode_id,),
            ).fetchall()
            return [self._match_set_from_row(connection, row) for row in rows]

    def confirm_match_set(self, match_set_id: str, stage_id: str,
                          artifact_ids: Sequence[str]) -> AssetMatchSetRecord:
        if not artifact_ids:
            raise ValueError("at least one artifact must be selected")
        with self._transaction() as connection:
            row = self._require_row(
                connection, "SELECT * FROM asset_match_sets WHERE match_set_id = ?",
                (match_set_id,), "match set",
            )
            self._require_row(connection, "SELECT stage_id FROM stages WHERE stage_id = ?", (stage_id,), "stage")
            connection.execute("DELETE FROM asset_match_selections WHERE match_set_id = ?", (match_set_id,))
            for ordinal, artifact_id in enumerate(artifact_ids):
                artifact = self._require_row(
                    connection,
                    "SELECT episode_id, validation_status FROM artifacts WHERE artifact_id = ?",
                    (artifact_id,), "selected artifact",
                )
                if artifact["episode_id"] != row["episode_id"]:
                    raise StateStoreError("selected artifact does not belong to the episode")
                if artifact["validation_status"] != ValidationStatus.VALID.value:
                    raise StateStoreError("selected artifact is not current")
                connection.execute(
                    """INSERT INTO asset_match_selections(
                        match_set_id, artifact_id, input_role, ordinal, stage_id
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (match_set_id, artifact_id, row["input_role"], ordinal, stage_id),
                )
            connection.execute(
                "UPDATE asset_match_sets SET status = ? WHERE match_set_id = ?",
                (AssetMatchStatus.CONFIRMED.value, match_set_id),
            )
            updated = connection.execute(
                "SELECT * FROM asset_match_sets WHERE match_set_id = ?", (match_set_id,)
            ).fetchone()
            return self._match_set_from_row(connection, updated)

    def get_episode_manifest(self, episode_id: str) -> dict[str, Any]:
        return {
            "episode_id": episode_id,
            "artifacts": [item.to_dict() for item in self.list_artifacts(episode_id=episode_id)],
            "matches": [item.to_dict() for item in self.list_current_match_sets(episode_id)],
        }

    def mark_artifact_stale(self, artifact_id: str) -> ArtifactRecord:
        with self._transaction() as connection:
            self._require_row(
                connection, "SELECT artifact_id FROM artifacts WHERE artifact_id = ?",
                (artifact_id,), "artifact",
            )
            connection.execute(
                "UPDATE artifacts SET validation_status = ? WHERE artifact_id = ?",
                (ValidationStatus.STALE.value, artifact_id),
            )
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            return self._artifact_from_row(connection, row)

    def create_production_request(self, record: ProductionRequestRecord) -> ProductionRequestRecord:
        if record.status is not ProductionRequestStatus.PENDING:
            raise StateTransitionError("new production request must start as pending")
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO production_requests(
                    request_id, workspace_path, episode_id, operation, output_profile,
                    output_target, parameters_json, status, created_at, updated_at,
                    run_id, stage_id, artifact_id, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.request_id, str(record.workspace_path), record.episode_id,
                    record.operation.value, record.output_profile, str(record.output_target),
                    self._json(dict(record.parameters)), record.status.value,
                    datetime_to_text(record.created_at), datetime_to_text(record.updated_at),
                    record.run_id, record.stage_id, record.artifact_id, record.error_code,
                ),
            )
            for item in record.inputs:
                artifact = self._require_row(
                    connection,
                    "SELECT episode_id, validation_status FROM artifacts WHERE artifact_id = ?",
                    (item.artifact_id,), "production input artifact",
                )
                if artifact["episode_id"] != record.episode_id:
                    raise StateStoreError("production input artifact does not belong to the episode")
                if artifact["validation_status"] != ValidationStatus.VALID.value:
                    raise StateStoreError("production input artifact is not current")
                connection.execute(
                    """INSERT INTO production_request_inputs(
                        request_id, artifact_id, input_role, ordinal
                    ) VALUES (?, ?, ?, ?)""",
                    (record.request_id, item.artifact_id, item.input_role, item.ordinal),
                )
        return record

    def get_production_request(self, request_id: str) -> ProductionRequestRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM production_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            return self._production_request_from_row(connection, row) if row else None

    def list_production_requests(self, *, episode_id: str | None = None) -> list[ProductionRequestRecord]:
        parameters: tuple[Any, ...] = ()
        where = ""
        if episode_id is not None:
            where = " WHERE episode_id = ?"
            parameters = (episode_id,)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM production_requests" + where +
                " ORDER BY created_at DESC, request_id", parameters,
            ).fetchall()
            return [self._production_request_from_row(connection, row) for row in rows]

    def transition_production_request(
        self, request_id: str, status: ProductionRequestStatus, *,
        run_id: str | None = None, stage_id: str | None = None,
        artifact_id: str | None = None, error_code: str | None = None,
        now: datetime | None = None,
    ) -> ProductionRequestRecord:
        with self._transaction() as connection:
            row = self._require_row(
                connection, "SELECT * FROM production_requests WHERE request_id = ?",
                (request_id,), "production request",
            )
            current = ProductionRequestStatus(row["status"])
            self._validate_transition(
                current, status, _PRODUCTION_REQUEST_TRANSITIONS, "production request"
            )
            timestamp = now or utc_now()
            connection.execute(
                """UPDATE production_requests SET status = ?, updated_at = ?,
                   run_id = COALESCE(?, run_id), stage_id = COALESCE(?, stage_id),
                   artifact_id = COALESCE(?, artifact_id), error_code = ?
                   WHERE request_id = ?""",
                (
                    status.value, datetime_to_text(timestamp), run_id, stage_id,
                    artifact_id, error_code, request_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM production_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            return self._production_request_from_row(connection, updated)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def get_run_stages(self, run_id: str) -> list[StageRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM stages WHERE run_id = ? ORDER BY created_at, stage_id", (run_id,)
            ).fetchall()
        return [self._stage_from_row(row) for row in rows]

    def get_stage_artifacts(self, stage_id: str) -> list[ArtifactRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE stage_id = ? ORDER BY created_at, artifact_id",
                (stage_id,),
            ).fetchall()
            return [self._artifact_from_row(connection, row) for row in rows]

    def get_run_detail(self, run_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        stages = self.get_run_stages(run_id)
        return {
            "run": run.to_dict(),
            "stages": [
                {
                    **stage.to_dict(),
                    "inputs": [item.to_dict() for item in self.get_stage_inputs(stage.stage_id)],
                    "artifacts": [
                        artifact.to_dict()
                        for artifact in self.get_stage_artifacts(stage.stage_id)
                    ],
                }
                for stage in stages
            ],
        }

    def find_reusable_stage(self, episode_id: str | None, stage_name: str,
                            input_fingerprint: str, parameter_fingerprint: str,
                            tool_fingerprint: str, *, exclude_run_id: str | None = None) -> StageRecord | None:
        exclusion_sql = " AND s.run_id != ?" if exclude_run_id is not None else ""
        parameters: list[Any] = [
            episode_id, stage_name, StageStatus.SUCCEEDED.value,
            input_fingerprint, parameter_fingerprint, tool_fingerprint,
        ]
        if exclude_run_id is not None:
            parameters.append(exclude_run_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.* FROM stages s
                   JOIN runs r ON r.run_id = s.run_id
                   WHERE r.episode_id IS ? AND s.stage_name = ? AND s.status = ?
                     AND s.input_fingerprint = ? AND s.parameter_fingerprint = ?
                     AND s.tool_fingerprint = ?""" + exclusion_sql + """
                   ORDER BY s.finished_at DESC, s.created_at DESC LIMIT 1""",
                tuple(parameters),
            ).fetchone()
        return self._stage_from_row(row) if row else None

    def mark_interrupted_runs(self, *, now: datetime | None = None) -> list[str]:
        timestamp = now or utc_now()
        interrupted: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT run_id FROM runs WHERE status = ?", (RunStatus.RUNNING.value,)
            ).fetchall()
            for row in rows:
                run_id = row["run_id"]
                interrupted.append(run_id)
                connection.execute(
                    """UPDATE stages SET status = ?, finished_at = ?, retryable = 1,
                       error_code = ?, needs_review = 0
                       WHERE run_id = ? AND status = ?""",
                    (
                        StageStatus.FAILED.value, datetime_to_text(timestamp), "interrupted",
                        run_id, StageStatus.RUNNING.value,
                    ),
                )
                connection.execute(
                    "UPDATE runs SET status = ?, finished_at = ?, error_code = ? WHERE run_id = ?",
                    (RunStatus.INTERRUPTED.value, datetime_to_text(timestamp), "interrupted", run_id),
                )
                self._add_event(connection, run_id, None, "run_interrupted", {})
        return interrupted

    def _transition_stage(self, stage_id: str, target: StageStatus, *,
                          error_code: str | None = None, retryable: bool = False,
                          needs_review: bool = False,
                          diagnostics: Sequence[Diagnostic] = (),
                          now: datetime | None = None) -> StageRecord:
        diagnostics_tuple = tuple(diagnostics)
        timestamp = now or utc_now()
        with self._transaction() as connection:
            row = self._require_row(
                connection, "SELECT * FROM stages WHERE stage_id = ?", (stage_id,), "stage"
            )
            current = StageStatus(row["status"])
            self._validate_transition(current, target, _STAGE_TRANSITIONS, "stage")
            started_at = datetime_to_text(timestamp) if target is StageStatus.RUNNING else row["started_at"]
            finished_at = None if target is StageStatus.RUNNING else datetime_to_text(timestamp)
            connection.execute(
                """UPDATE stages SET status = ?, started_at = ?, finished_at = ?, retryable = ?,
                   needs_review = ?, error_code = ?, diagnostics_json = ? WHERE stage_id = ?""",
                (
                    target.value, started_at, finished_at, int(retryable), int(needs_review),
                    error_code, self._json([item.to_dict() for item in diagnostics_tuple]), stage_id,
                ),
            )
            event_type = {
                StageStatus.RUNNING: "stage_started",
                StageStatus.SUCCEEDED: "stage_succeeded",
                StageStatus.FAILED: "stage_failed",
                StageStatus.NEEDS_REVIEW: "stage_review_required",
                StageStatus.SKIPPED: "stage_skipped",
                StageStatus.STALE: "stage_stale",
            }[target]
            self._add_event(
                connection, row["run_id"], stage_id, event_type,
                {"error_code": error_code, "retryable": retryable},
            )
            updated = connection.execute(
                "SELECT * FROM stages WHERE stage_id = ?", (stage_id,)
            ).fetchone()
        return self._stage_from_row(updated)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except sqlite3.Error as exc:
            raise StateStoreError(
                f"SQLite state operation failed: {exc}",
                retryable=isinstance(exc, sqlite3.OperationalError),
            ) from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=self.timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _read_version(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT version FROM schema_info WHERE singleton = 1").fetchone()
        if row is None:
            raise SchemaVersionError("state database is missing its schema version")
        return int(row["version"])

    def _check_version(self, connection: sqlite3.Connection) -> int:
        version = self._read_version(connection)
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"unsupported state schema version: {version}; expected {SCHEMA_VERSION}",
                details={"found": version, "expected": SCHEMA_VERSION},
            )
        return version

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE artifacts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        connection.execute("ALTER TABLE artifacts ADD COLUMN superseded_by_artifact_id TEXT")
        connection.executescript("""
            CREATE TABLE artifact_purposes (
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
                purpose TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                PRIMARY KEY (artifact_id, purpose)
            );
            CREATE TABLE stage_inputs (
                stage_id TEXT NOT NULL REFERENCES stages(stage_id) ON DELETE CASCADE,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                input_role TEXT NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
                PRIMARY KEY (stage_id, input_role, ordinal)
            );
            CREATE INDEX idx_artifacts_episode_type
                ON artifacts(episode_id, artifact_type, validation_status, created_at DESC);
            CREATE INDEX idx_artifact_purposes_lookup
                ON artifact_purposes(purpose, is_default, artifact_id);
            CREATE INDEX idx_stage_inputs_stage
                ON stage_inputs(stage_id, input_role, ordinal);
            CREATE INDEX idx_stage_inputs_artifact
                ON stage_inputs(artifact_id, stage_id);
        """)
        connection.execute(
            "UPDATE schema_info SET version = ?, applied_at = ? WHERE singleton = 1",
            (2, datetime_to_text(utc_now())),
        )

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS asset_match_sets (
                match_set_id TEXT PRIMARY KEY,
                stage_id TEXT NOT NULL REFERENCES stages(stage_id) ON DELETE CASCADE,
                episode_id TEXT NOT NULL,
                anchor_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                input_role TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('confirmed', 'inferred', 'ambiguous', 'unmatched')),
                rule_version TEXT NOT NULL,
                superseded_by_match_set_id TEXT REFERENCES asset_match_sets(match_set_id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asset_match_candidates (
                match_set_id TEXT NOT NULL REFERENCES asset_match_sets(match_set_id) ON DELETE CASCADE,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                rank INTEGER NOT NULL CHECK (rank >= 0),
                score INTEGER NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (match_set_id, artifact_id),
                UNIQUE (match_set_id, rank)
            );
            CREATE TABLE IF NOT EXISTS asset_match_selections (
                match_set_id TEXT NOT NULL REFERENCES asset_match_sets(match_set_id) ON DELETE CASCADE,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                input_role TEXT NOT NULL,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                stage_id TEXT NOT NULL REFERENCES stages(stage_id),
                PRIMARY KEY (match_set_id, input_role, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_match_sets_current
                ON asset_match_sets(episode_id, anchor_artifact_id, input_role, superseded_by_match_set_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_match_candidates_artifact
                ON asset_match_candidates(artifact_id, match_set_id);
            CREATE INDEX IF NOT EXISTS idx_match_selections_artifact
                ON asset_match_selections(artifact_id, match_set_id);
        """)
        connection.execute(
            "UPDATE schema_info SET version = ?, applied_at = ? WHERE singleton = 1",
            (3, datetime_to_text(utc_now())),
        )

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS production_requests (
                request_id TEXT PRIMARY KEY,
                workspace_path TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                operation TEXT NOT NULL CHECK (operation IN ('encode', 'remux', 'hardsub', 'mux_subtitle')),
                output_profile TEXT NOT NULL,
                output_target TEXT NOT NULL,
                parameters_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'needs_review')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                run_id TEXT REFERENCES runs(run_id),
                stage_id TEXT REFERENCES stages(stage_id),
                artifact_id TEXT REFERENCES artifacts(artifact_id),
                error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS production_request_inputs (
                request_id TEXT NOT NULL REFERENCES production_requests(request_id) ON DELETE CASCADE,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                input_role TEXT NOT NULL,
                ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
                PRIMARY KEY (request_id, input_role, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_production_requests_episode
                ON production_requests(episode_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_production_request_inputs_artifact
                ON production_request_inputs(artifact_id, request_id);
        """)
        connection.execute(
            "UPDATE schema_info SET version = ?, applied_at = ? WHERE singleton = 1",
            (SCHEMA_VERSION, datetime_to_text(utc_now())),
        )

    @staticmethod
    def _validate_transition(current: Any, target: Any, transitions: Mapping[Any, set[Any]],
                             record_type: str) -> None:
        if target not in transitions.get(current, set()):
            raise StateTransitionError(
                f"invalid {record_type} status transition: {current.value} -> {target.value}",
                details={"current": current.value, "target": target.value},
            )

    @staticmethod
    def _require_row(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...],
                     label: str) -> sqlite3.Row:
        row = connection.execute(sql, parameters).fetchone()
        if row is None:
            raise StateStoreError(f"{label} not found")
        return row

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _add_event(self, connection: sqlite3.Connection, run_id: str,
                   stage_id: str | None, event_type: str, payload: Mapping[str, Any]) -> None:
        _assert_no_secret_keys(payload)
        connection.execute(
            "INSERT INTO events(run_id, stage_id, event_type, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, stage_id, event_type, datetime_to_text(utc_now()), self._json(payload)),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"], workspace_path=Path(row["workspace_path"]),
            episode_id=row["episode_id"], command_name=row["command_name"],
            status=RunStatus(row["status"]), created_at=datetime_from_text(row["created_at"]),
            started_at=datetime_from_text(row["started_at"]),
            finished_at=datetime_from_text(row["finished_at"]), error_code=row["error_code"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _stage_from_row(row: sqlite3.Row) -> StageRecord:
        return StageRecord(
            stage_id=row["stage_id"], run_id=row["run_id"], stage_name=row["stage_name"],
            status=StageStatus(row["status"]), created_at=datetime_from_text(row["created_at"]),
            input_fingerprint=row["input_fingerprint"],
            parameter_fingerprint=row["parameter_fingerprint"],
            tool_fingerprint=row["tool_fingerprint"],
            started_at=datetime_from_text(row["started_at"]),
            finished_at=datetime_from_text(row["finished_at"]),
            retryable=bool(row["retryable"]), needs_review=bool(row["needs_review"]),
            error_code=row["error_code"],
            diagnostics=tuple(Diagnostic.from_dict(item) for item in json.loads(row["diagnostics_json"])),
        )

    @staticmethod
    def _match_set_from_row(connection: sqlite3.Connection,
                            row: sqlite3.Row) -> AssetMatchSetRecord:
        candidate_rows = connection.execute(
            """SELECT match_set_id, artifact_id, rank, score, evidence_json
               FROM asset_match_candidates WHERE match_set_id = ? ORDER BY rank""",
            (row["match_set_id"],),
        ).fetchall()
        selection_rows = connection.execute(
            """SELECT match_set_id, artifact_id, input_role, ordinal, stage_id
               FROM asset_match_selections WHERE match_set_id = ? ORDER BY input_role, ordinal""",
            (row["match_set_id"],),
        ).fetchall()
        return AssetMatchSetRecord(
            match_set_id=row["match_set_id"], stage_id=row["stage_id"],
            episode_id=row["episode_id"], anchor_artifact_id=row["anchor_artifact_id"],
            input_role=row["input_role"], status=AssetMatchStatus(row["status"]),
            rule_version=row["rule_version"], created_at=datetime_from_text(row["created_at"]),
            superseded_by_match_set_id=row["superseded_by_match_set_id"],
            candidates=tuple(
                AssetMatchCandidateRecord(
                    item["match_set_id"], item["artifact_id"], item["rank"], item["score"],
                    json.loads(item["evidence_json"]),
                ) for item in candidate_rows
            ),
            selections=tuple(AssetMatchSelectionRecord(**dict(item)) for item in selection_rows),
        )

    @staticmethod
    def _production_request_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> ProductionRequestRecord:
        input_rows = connection.execute(
            """SELECT artifact_id, input_role, ordinal FROM production_request_inputs
               WHERE request_id = ? ORDER BY input_role, ordinal""",
            (row["request_id"],),
        ).fetchall()
        return ProductionRequestRecord(
            request_id=row["request_id"], workspace_path=Path(row["workspace_path"]),
            episode_id=row["episode_id"], operation=ProductionOperation(row["operation"]),
            output_profile=row["output_profile"], output_target=Path(row["output_target"]),
            parameters=json.loads(row["parameters_json"]),
            status=ProductionRequestStatus(row["status"]),
            created_at=datetime_from_text(row["created_at"]),
            updated_at=datetime_from_text(row["updated_at"]),
            inputs=tuple(ProductionRequestInput(**dict(item)) for item in input_rows),
            run_id=row["run_id"], stage_id=row["stage_id"],
            artifact_id=row["artifact_id"], error_code=row["error_code"],
        )

    @staticmethod
    def _artifact_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> ArtifactRecord:
        purpose_rows = connection.execute(
            "SELECT purpose, is_default FROM artifact_purposes WHERE artifact_id = ? ORDER BY purpose",
            (row["artifact_id"],),
        ).fetchall()
        return ArtifactRecord(
            artifact_id=row["artifact_id"], run_id=row["run_id"], stage_id=row["stage_id"],
            episode_id=row["episode_id"], artifact_type=row["artifact_type"], path=Path(row["path"]),
            size=row["size"], mtime_ns=row["mtime_ns"], content_hash=row["content_hash"],
            source_fingerprint=row["source_fingerprint"],
            parameter_fingerprint=row["parameter_fingerprint"],
            validation_status=ValidationStatus(row["validation_status"]),
            created_at=datetime_from_text(row["created_at"]),
            metadata=json.loads(row["metadata_json"]),
            purposes=tuple(
                ArtifactPurpose(item["purpose"], bool(item["is_default"]))
                for item in purpose_rows
            ),
            superseded_by_artifact_id=row["superseded_by_artifact_id"],
        )
