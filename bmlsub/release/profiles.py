"""Versioned torrent creation profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


TORRENT_PROFILE_VERSION = "torrent-profile-v2"
TORRENT_NAMING_VERSION = "torrent-naming-v1"
DEFAULT_TRACKER_BEST_URL = "https://ngosang.github.io/trackerslist/trackers_best.txt"
_ALLOWED_PIECE_LENGTHS = {64 * 1024, 256 * 1024, 1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024}


@dataclass(frozen=True)
class TorrentProfile:
    format: str = "hybrid"
    piece_length: int | None = None
    private: bool = False
    comment: str = ""
    created_by: str = "BML"
    tracker_best_url: str = DEFAULT_TRACKER_BEST_URL
    tracker_timeout: float = 15.0

    def __post_init__(self) -> None:
        if self.format not in {"hybrid", "v1"}:
            raise ValueError("format must be hybrid or v1")
        if self.piece_length is not None and self.piece_length not in _ALLOWED_PIECE_LENGTHS:
            raise ValueError("piece_length must use a supported power-of-two policy value")
        if not isinstance(self.private, bool):
            raise ValueError("private must be boolean")
        if len(self.comment.encode("utf-8")) > 4096:
            raise ValueError("torrent comment is too long")
        if not self.created_by.strip() or len(self.created_by.encode("utf-8")) > 255:
            raise ValueError("created_by must be a short non-empty string")
        if not self.tracker_best_url.startswith(("https://", "http://")):
            raise ValueError("tracker_best_url must be HTTP or HTTPS")
        if not 0 < self.tracker_timeout <= 120:
            raise ValueError("tracker_timeout must be between 0 and 120 seconds")

    def normalized(self) -> dict[str, Any]:
        return {
            "version": TORRENT_PROFILE_VERSION,
            "format": self.format,
            "piece_length": self.piece_length,
            "private": self.private,
            "comment": self.comment,
            "created_by": self.created_by,
            "tracker_best_url": self.tracker_best_url,
            "tracker_timeout": self.tracker_timeout,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "TorrentProfile":
        if value is None:
            return cls()
        allowed = {
            "format", "piece_length", "private", "comment", "created_by",
            "tracker_best_url", "tracker_timeout",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown torrent profile fields: {sorted(unknown)}")
        return cls(**dict(value))


def normalize_torrent_profile(value: TorrentProfile | Mapping[str, Any] | None) -> TorrentProfile:
    return value if isinstance(value, TorrentProfile) else TorrentProfile.from_mapping(value)
