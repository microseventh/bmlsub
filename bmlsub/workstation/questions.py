"""Safe input discovery and question helpers for standalone operations."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .operations import operation_spec


VIDEO_SUFFIXES = frozenset({".mkv", ".mp4", ".mov", ".m4v", ".webm", ".avi"})
AUDIO_SUFFIXES = frozenset({".mka", ".wav", ".flac", ".aac", ".m4a", ".mp3", ".ogg", ".opus"})
SUBTITLE_SUFFIXES = frozenset({".ass", ".ssa", ".srt", ".vtt"})
FONT_SUFFIXES = frozenset({".ttf", ".ttc", ".otf", ".otc", ".woff", ".woff2"})
CHAPTER_SUFFIXES = frozenset({".xml", ".txt"})
TORRENT_SUFFIXES = frozenset({".torrent"})
TEMP_SUFFIXES = frozenset({".tmp", ".temp", ".part", ".partial", ".bak", ".lock", ".log"})
IGNORED_NAMES = frozenset({".bmlsub", "workstation", "bgminfo", "__pycache__"})


def _supported(path: Path, kind: str) -> bool:
    suffix = path.suffix.lower()
    if kind == "video":
        return suffix in VIDEO_SUFFIXES
    if kind == "media":
        return suffix in VIDEO_SUFFIXES | AUDIO_SUFFIXES
    if kind == "torrent":
        return suffix in TORRENT_SUFFIXES
    if kind == "content":
        return suffix not in TORRENT_SUFFIXES | TEMP_SUFFIXES
    if kind == "receipt":
        return False
    return False


def _ignore_reason(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root)
    if any(part in IGNORED_NAMES or part.startswith(".") for part in relative.parts):
        return "hidden_or_state"
    if path.is_symlink():
        return "symbolic_link"
    if not path.is_file():
        return "not_regular_file"
    if path.suffix.lower() in TEMP_SUFFIXES or path.name.endswith("~"):
        return "temporary_or_backup"
    return None


def discover_inputs(operation: str, directory: Path | str = ".", *,
                    recursive: bool = False) -> dict[str, object]:
    """Discover only safe, supported regular files in stable filename order."""
    root = Path(directory).expanduser().resolve()
    spec = operation_spec(operation)
    if not root.is_dir():
        raise NotADirectoryError(root)
    if spec.input_kind == "config":
        return {"root": str(root), "recursive": recursive, "found": [], "skipped": []}

    candidates: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    found: list[str] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix().casefold()):
        reason = _ignore_reason(path, root)
        if reason is None and not _supported(path, spec.input_kind):
            reason = "unsupported_type"
        if reason is None:
            found.append(str(path.resolve()))
        elif path.is_file() or path.is_symlink():
            skipped.append({"path": str(path.absolute()), "reason": reason})
    return {"root": str(root), "recursive": recursive, "found": found, "skipped": skipped}


def associated_files(video: Path, candidates: Iterable[Path]) -> dict[str, list[str]]:
    """Find same-stem subtitle, font, and chapter candidates for one video."""
    stem = video.stem.casefold()
    subtitles: list[str] = []
    fonts: list[str] = []
    chapters: list[str] = []
    for path in candidates:
        suffix = path.suffix.lower()
        if suffix in SUBTITLE_SUFFIXES and (path.stem.casefold() == stem or path.stem.casefold().startswith(stem + ".")):
            subtitles.append(str(path.resolve()))
        elif suffix in FONT_SUFFIXES:
            fonts.append(str(path.resolve()))
        elif suffix in CHAPTER_SUFFIXES and path.stem.casefold().startswith(stem):
            chapters.append(str(path.resolve()))
    return {
        "subtitles": sorted(subtitles, key=str.casefold),
        "fonts": sorted(fonts, key=str.casefold),
        "chapters": sorted(chapters, key=str.casefold),
    }
