"""Shared episode discovery and workstation opening helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..pipeline import Pipeline
from ..state.sqlite_store import SQLiteJobStore
from .models import WorkstationConfig
from .series import SeriesContext, discover_series_context


VIDEO_SUFFIXES = {".mkv", ".mp4", ".mov", ".m4v", ".webm", ".avi", ".ts", ".m2ts"}
FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}


@dataclass(frozen=True)
class Workstation:
    config: WorkstationConfig
    pipeline: Pipeline
    store: SQLiteJobStore

    @property
    def workspace(self) -> Path:
        return self.config.workspace

    @property
    def root(self) -> Path:
        return self.workspace / "workstation"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"


def open_workstation(config: WorkstationConfig, *, create: bool = True) -> Workstation:
    if create:
        ensure_directories(config.workspace)
    store = SQLiteJobStore.for_workspace(config.workspace, config.state_dir)
    store.initialize()
    return Workstation(config, Pipeline(store=store, state_dir=config.state_dir), store)


def ensure_directories(workspace: Path | str) -> dict[str, Path]:
    root = Path(workspace).expanduser().resolve() / "workstation"
    paths = {
        "root": root,
        "state": root / "state",
        "steps": root / "state" / "steps",
        "artifacts": root / "state" / "artifacts",
        "reference": root / "preprocess" / "reference",
        "audio": root / "preprocess" / "audio",
        "audio_chunks": root / "preprocess" / "audio" / "chunks",
        "transcripts": root / "preprocess" / "transcripts",
        "subtitles": root / "delivery" / "subtitles",
        "subtitle_analysis": root / "delivery" / "subtitle-analysis",
        "intermediate": root / "delivery" / "intermediate",
        "products": root / "delivery" / "products",
        "torrents": root / "delivery" / "torrents",
        "receipts_r2": root / "delivery" / "receipts" / "r2",
        "receipts_remote": root / "delivery" / "receipts" / "remote",
        "receipts_qb": root / "delivery" / "receipts" / "qb",
        "receipts_anibt": root / "delivery" / "receipts" / "anibt",
        "tmp": root / "tmp",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def resolve_explicit_path(workspace: Path, value: Path | str) -> Path:
    path = Path(value).expanduser()
    target = path if path.is_absolute() else workspace / path
    target = target.resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("workstation input path must be inside the episode directory") from exc
    return target


def source_video_candidates(workspace: Path | str) -> tuple[Path, ...]:
    root = Path(workspace).expanduser().resolve()
    return tuple(sorted(
        item.resolve() for item in root.iterdir()
        if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES
    ))


def discover_source_video(workspace: Path | str, explicit: Path | str | None = None) -> tuple[Path | None, dict[str, Any] | None]:
    root = Path(workspace).expanduser().resolve()
    if explicit is not None:
        target = resolve_explicit_path(root, explicit)
        if not target.is_file() or target.suffix.lower() not in VIDEO_SUFFIXES:
            return None, {"code": "input_missing", "message": "source video does not exist"}
        return target, None
    candidates = source_video_candidates(root)
    if len(candidates) == 1:
        return candidates[0], None
    return None, {
        "code": "source_video_ambiguous" if candidates else "input_missing",
        "message": "multiple source videos require explicit selection" if candidates else "source video is missing",
        "candidates": [str(item) for item in candidates],
    }


def production_subtitle_candidates(workspace: Path | str, episode_id: str,
                                   reference_paths: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    root = Path(workspace).expanduser().resolve()
    references = {item.resolve() for item in reference_paths}
    values = []
    for item in root.glob("*.ass"):
        resolved = item.resolve()
        name = item.name.lower()
        if resolved in references or ".en.ass" in name or ".eng.ass" in name or ".cht" in name:
            continue
        values.append(resolved)
    exact = root / f"{episode_id}.chs&jpn.ass"
    if exact.resolve() in values:
        return (exact.resolve(),)
    return tuple(sorted(values))


def discover_production_subtitle(workspace: Path | str, episode_id: str,
                                 explicit: Path | str | None = None,
                                 reference_paths: tuple[Path, ...] = ()) -> tuple[Path | None, dict[str, Any] | None]:
    root = Path(workspace).expanduser().resolve()
    if explicit is not None:
        target = resolve_explicit_path(root, explicit)
        if not target.is_file() or target.suffix.lower() != ".ass":
            return None, {"code": "input_missing", "message": "production subtitle does not exist"}
        return target, None
    candidates = production_subtitle_candidates(root, episode_id, reference_paths)
    if len(candidates) == 1:
        return candidates[0], None
    return None, {
        "code": "production_subtitle_ambiguous" if candidates else "input_missing",
        "message": "multiple production subtitles require explicit selection" if candidates else "production subtitle is missing",
        "candidates": [str(item) for item in candidates],
    }


def top_level_fonts(workspace: Path | str) -> tuple[Path, ...]:
    root = Path(workspace).expanduser().resolve()
    return tuple(sorted(
        item.resolve() for item in root.iterdir()
        if item.is_file() and item.suffix.lower() in FONT_SUFFIXES
    ))
