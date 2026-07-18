"""Transactional writer for committed artifacts."""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..execution.errors import ArtifactCommitError, OutputValidationError
from ..state.fingerprints import fingerprint_file
from ..state.models import ArtifactRecord, JsonValue, ValidationStatus, utc_now


Producer = Callable[[Path], None]
Validator = Callable[[Path], None]
BatchProducer = Callable[[tuple[Path, ...]], None]


@dataclass(frozen=True)
class ArtifactWriteResult:
    artifact: ArtifactRecord
    backup_path: Path | None = None


@dataclass(frozen=True)
class ArtifactWriteSpec:
    target: Path
    artifact_type: str
    validator: Validator
    metadata: Mapping[str, JsonValue] | None = None
    content_hash: bool = True


class ArtifactWriter:
    """Write, validate, and atomically commit one file on the target filesystem."""

    def __init__(self, target: Path | str, *, workspace: Path | str,
                 run_id: str, stage_id: str, artifact_type: str,
                 episode_id: str | None = None,
                 source_fingerprint: str | None = None,
                 parameter_fingerprint: str | None = None,
                 backup_dir: Path | str | None = None,
                 content_hash: bool = True,
                 metadata: Mapping[str, JsonValue] | None = None) -> None:
        self.target = Path(target).expanduser().resolve()
        self.workspace = Path(workspace).expanduser().resolve()
        self.run_id = run_id
        self.stage_id = stage_id
        self.artifact_type = artifact_type
        self.episode_id = episode_id
        self.source_fingerprint = source_fingerprint
        self.parameter_fingerprint = parameter_fingerprint
        self.backup_dir = (
            Path(backup_dir).expanduser().resolve()
            if backup_dir is not None else self.workspace / ".bmlsub" / "backups"
        )
        self.content_hash = content_hash
        self.metadata = dict(metadata or {})
        self._ensure_in_workspace(self.target)
        self._ensure_in_workspace(self.backup_dir)

    def write(self, producer: Producer, validator: Validator) -> ArtifactWriteResult:
        batch = ArtifactBatchWriter(
            workspace=self.workspace, run_id=self.run_id, stage_id=self.stage_id,
            episode_id=self.episode_id, source_fingerprint=self.source_fingerprint,
            parameter_fingerprint=self.parameter_fingerprint, backup_dir=self.backup_dir,
        )
        return batch.write(
            (ArtifactWriteSpec(self.target, self.artifact_type, validator,
                               self.metadata, self.content_hash),),
            lambda paths: producer(paths[0]),
        )[0]

    def _ensure_in_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"artifact path is outside workspace: {path}") from exc


class ArtifactBatchWriter:
    """Atomically commit a coordinated set of generated files."""

    def __init__(self, *, workspace: Path | str, run_id: str, stage_id: str,
                 episode_id: str | None = None,
                 source_fingerprint: str | None = None,
                 parameter_fingerprint: str | None = None,
                 backup_dir: Path | str | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.run_id = run_id
        self.stage_id = stage_id
        self.episode_id = episode_id
        self.source_fingerprint = source_fingerprint
        self.parameter_fingerprint = parameter_fingerprint
        self.backup_dir = (
            Path(backup_dir).expanduser().resolve()
            if backup_dir is not None else self.workspace / ".bmlsub" / "backups"
        )
        self._ensure_in_workspace(self.backup_dir)

    def write(self, specs: Sequence[ArtifactWriteSpec],
              producer: BatchProducer) -> tuple[ArtifactWriteResult, ...]:
        normalized = tuple(ArtifactWriteSpec(
            Path(spec.target).expanduser().resolve(), spec.artifact_type,
            spec.validator, spec.metadata, spec.content_hash,
        ) for spec in specs)
        if not normalized or len({spec.target for spec in normalized}) != len(normalized):
            raise ValueError("artifact batch targets must be nonempty and unique")
        for spec in normalized:
            self._ensure_in_workspace(spec.target)
            spec.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = tuple(self._temporary_path(spec.target) for spec in normalized)
        backups: dict[Path, Path] = {}
        existed = {spec.target: spec.target.exists() for spec in normalized}
        committed: list[Path] = []
        try:
            producer(temporary)
            for spec, candidate in zip(normalized, temporary):
                if not candidate.is_file():
                    raise ArtifactCommitError("artifact producer did not create every regular file")
                self._fsync_file(candidate)
                try:
                    spec.validator(candidate)
                except Exception as exc:
                    raise OutputValidationError(
                        f"artifact validation failed: {exc}",
                        details={"target": str(spec.target)},
                    ) from exc
            for spec in normalized:
                if existed[spec.target]:
                    backup = self._backup_path(spec.target)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(spec.target, backup)
                    backups[spec.target] = backup
            for spec, candidate in zip(normalized, temporary):
                os.replace(candidate, spec.target)
                committed.append(spec.target)
                self._fsync_directory(spec.target.parent)
            for spec in normalized:
                spec.validator(spec.target)
            results = []
            for spec in normalized:
                fingerprint = fingerprint_file(spec.target, content_hash=spec.content_hash)
                artifact = ArtifactRecord(
                    artifact_id=uuid.uuid4().hex, run_id=self.run_id,
                    stage_id=self.stage_id, episode_id=self.episode_id,
                    artifact_type=spec.artifact_type, path=spec.target,
                    size=fingerprint.size, mtime_ns=fingerprint.mtime_ns,
                    content_hash=fingerprint.content_hash,
                    source_fingerprint=self.source_fingerprint,
                    parameter_fingerprint=self.parameter_fingerprint,
                    validation_status=ValidationStatus.VALID, created_at=utc_now(),
                    metadata=dict(spec.metadata or {}),
                )
                results.append(ArtifactWriteResult(artifact, backups.get(spec.target)))
            return tuple(results)
        except (OutputValidationError, ArtifactCommitError):
            self._restore(normalized, backups, existed, committed)
            raise
        except Exception as exc:
            self._restore(normalized, backups, existed, committed)
            raise ArtifactCommitError(
                f"artifact batch production failed: {exc}",
                details={"targets": [str(spec.target) for spec in normalized]},
            ) from exc
        finally:
            for candidate in temporary:
                if candidate.exists():
                    candidate.unlink()

    def _restore(self, specs: tuple[ArtifactWriteSpec, ...], backups: dict[Path, Path],
                 existed: dict[Path, bool], committed: list[Path]) -> None:
        for spec in reversed(specs):
            target = spec.target
            backup = backups.get(target)
            if target in committed and target.exists():
                target.unlink()
            if backup is not None and backup.exists():
                os.replace(backup, target)
                self._fsync_directory(target.parent)
            elif not existed[target] and target.exists():
                target.unlink()

    def _temporary_path(self, target: Path) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.{self.run_id}.", suffix=target.suffix,
            dir=target.parent,
        )
        os.close(descriptor)
        path = Path(name)
        path.unlink()
        return path

    def _backup_path(self, target: Path) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return self.backup_dir / f"{target.stem}.{timestamp}.{self.run_id}{target.suffix}"

    def _ensure_in_workspace(self, path: Path) -> None:
        try:
            path.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError(f"artifact path is outside workspace: {path}") from exc

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
