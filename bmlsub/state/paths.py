"""Default and configurable paths for local execution state."""

from __future__ import annotations

from pathlib import Path


DEFAULT_STATE_DIR_NAME = ".bmlsub"
STATE_DATABASE_FILENAME = "state.sqlite3"


def state_directory(workspace: Path | str, state_dir: Path | str | None = None) -> Path:
    workspace_path = Path(workspace).expanduser().resolve()
    if state_dir is None:
        return workspace_path / DEFAULT_STATE_DIR_NAME
    configured = Path(state_dir).expanduser()
    if not configured.is_absolute():
        configured = workspace_path / configured
    return configured.resolve()


def state_database_path(workspace: Path | str, state_dir: Path | str | None = None) -> Path:
    return state_directory(workspace, state_dir) / STATE_DATABASE_FILENAME
