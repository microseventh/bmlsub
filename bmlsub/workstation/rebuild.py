"""Identity checks and recoverable local replacement for rebuild operations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil


def backup_target(target: Path, backup_root: Path) -> Path:
    """Move one exact local target into a timestamped recoverable backup."""
    source = target.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_root.mkdir(parents=True, exist_ok=True)
    candidate = backup_root / f"{source.name}.{stamp}.bak"
    shutil.move(str(source), str(candidate))
    return candidate


def restore_backup(backup: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    shutil.move(str(backup), str(target))


def rebuild_refusal(operation: str) -> dict[str, object] | None:
    if operation != "anibt":
        return None
    return {
        "status": "refused",
        "operation": operation,
        "error": {
            "code": "operation_not_rebuildable",
            "message": "Anibt has no reliable delete or overwrite contract",
            "retryable": False,
        },
        "external_actions": [],
        "next_action": None,
    }
