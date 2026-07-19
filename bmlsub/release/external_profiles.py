"""Validated profiles for external release distribution stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Mapping
from urllib.parse import urlparse


R2_UPLOAD_PROFILE_VERSION = "r2-upload-profile-v1"
REMOTE_PULL_PROFILE_VERSION = "remote-pull-profile-v1"
QB_SEED_PROFILE_VERSION = "qb-seed-profile-v1"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9._@-]+$")


def _mapping(value: Mapping[str, Any] | None, allowed: set[str], label: str) -> dict[str, Any]:
    result = dict(value or {})
    unknown = set(result) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    return result


def _object_key(value: str) -> str:
    key = value.strip().lstrip("/")
    if not key or key.endswith("/") or "\x00" in key:
        raise ValueError("object_key must identify one non-empty object")
    if any(part in {"", ".", ".."} for part in PurePosixPath(key).parts):
        raise ValueError("object_key contains an unsafe path segment")
    return key


@dataclass(frozen=True)
class R2UploadProfile:
    bucket: str
    object_key: str
    content_type: str = "application/octet-stream"
    access: str = "private"
    public_base_url: str | None = None
    multipart_threshold: int = 50 * 1024 * 1024
    multipart_chunk_size: int = 50 * 1024 * 1024
    max_concurrency: int = 3

    def __post_init__(self) -> None:
        if not _SAFE_NAME.fullmatch(self.bucket):
            raise ValueError("bucket contains unsupported characters")
        object.__setattr__(self, "object_key", _object_key(self.object_key))
        if not self.content_type.strip() or len(self.content_type) > 255:
            raise ValueError("content_type must be a short non-empty value")
        if self.access not in {"private", "public"}:
            raise ValueError("access must be private or public")
        if self.access == "public":
            if not self.public_base_url:
                raise ValueError("public access requires public_base_url")
            parsed = urlparse(self.public_base_url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("public_base_url must be a clean HTTPS origin/path")
        elif self.public_base_url is not None:
            raise ValueError("private access must not define public_base_url")
        if not 5 * 1024 * 1024 <= self.multipart_chunk_size <= 5 * 1024 * 1024 * 1024:
            raise ValueError("multipart_chunk_size is outside the supported range")
        if self.multipart_threshold < self.multipart_chunk_size:
            raise ValueError("multipart_threshold must be at least multipart_chunk_size")
        if not 1 <= self.max_concurrency <= 16:
            raise ValueError("max_concurrency must be between 1 and 16")

    def normalized(self) -> dict[str, Any]:
        return {
            "version": R2_UPLOAD_PROFILE_VERSION, "bucket": self.bucket,
            "object_key": self.object_key, "content_type": self.content_type,
            "access": self.access, "public_base_url": self.public_base_url,
            "multipart_threshold": self.multipart_threshold,
            "multipart_chunk_size": self.multipart_chunk_size,
            "max_concurrency": self.max_concurrency,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "R2UploadProfile":
        return cls(**_mapping(value, {
            "bucket", "object_key", "content_type", "access", "public_base_url",
            "multipart_threshold", "multipart_chunk_size", "max_concurrency",
        }, "R2 upload profile"))


@dataclass(frozen=True)
class RemotePullProfile:
    ssh_alias: str
    rclone_remote: str
    bucket: str
    object_key: str
    target_path: str
    timeout: float = 3600.0

    def __post_init__(self) -> None:
        if not _SAFE_ALIAS.fullmatch(self.ssh_alias):
            raise ValueError("ssh_alias contains unsupported characters")
        if not _SAFE_NAME.fullmatch(self.rclone_remote) or not _SAFE_NAME.fullmatch(self.bucket):
            raise ValueError("rclone_remote or bucket contains unsupported characters")
        object.__setattr__(self, "object_key", _object_key(self.object_key))
        target = PurePosixPath(self.target_path)
        if not target.is_absolute() or any(part in {"", ".", ".."} for part in target.parts[1:]):
            raise ValueError("target_path must be a normalized absolute POSIX path")
        if not 1 <= self.timeout <= 24 * 3600:
            raise ValueError("timeout must be between 1 second and 24 hours")

    def normalized(self) -> dict[str, Any]:
        return {
            "version": REMOTE_PULL_PROFILE_VERSION, "ssh_alias": self.ssh_alias,
            "rclone_remote": self.rclone_remote, "bucket": self.bucket,
            "object_key": self.object_key, "target_path": self.target_path,
            "timeout": self.timeout,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RemotePullProfile":
        return cls(**_mapping(value, {
            "ssh_alias", "rclone_remote", "bucket", "object_key", "target_path", "timeout",
        }, "remote pull profile"))


@dataclass(frozen=True)
class QBittorrentSeedProfile:
    ssh_alias: str
    host: str = "127.0.0.1"
    port: int = 8080
    save_path: str = "/downloads"
    legacy_host_save_path: str | None = None
    webui_origin: str | None = None
    category: str = ""
    tags: tuple[str, ...] = ()
    poll_interval: float = 2.0
    poll_timeout: float = 1800.0
    allow_magnet_fallback: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_ALIAS.fullmatch(self.ssh_alias):
            raise ValueError("ssh_alias contains unsupported characters")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("qBittorrent host must be loopback on the remote server")
        if not 1 <= self.port <= 65535:
            raise ValueError("qBittorrent port is invalid")
        path = PurePosixPath(self.save_path)
        if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
            raise ValueError("save_path must be a normalized absolute POSIX path")
        if self.legacy_host_save_path is not None:
            legacy = PurePosixPath(self.legacy_host_save_path)
            if (not legacy.is_absolute()
                    or any(part in {"", ".", ".."} for part in legacy.parts[1:])):
                raise ValueError("legacy_host_save_path must be a normalized absolute POSIX path")
            object.__setattr__(self, "legacy_host_save_path", str(legacy))
        if self.webui_origin is not None:
            parsed = urlparse(self.webui_origin)
            if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
                    or parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment):
                raise ValueError("webui_origin must be a clean HTTPS origin")
            object.__setattr__(self, "webui_origin", f"https://{parsed.netloc}")
        if len(self.category) > 128 or any(len(tag) > 128 for tag in self.tags):
            raise ValueError("category or tag is too long")
        if not 0.2 <= self.poll_interval <= 60 or not 1 <= self.poll_timeout <= 24 * 3600:
            raise ValueError("poll timing is outside the supported range")
        if not isinstance(self.allow_magnet_fallback, bool):
            raise ValueError("allow_magnet_fallback must be boolean")
        object.__setattr__(self, "tags", tuple(self.tags))

    def normalized(self) -> dict[str, Any]:
        return {
            "version": QB_SEED_PROFILE_VERSION, "ssh_alias": self.ssh_alias,
            "host": self.host, "port": self.port, "save_path": self.save_path,
            "legacy_host_save_path": self.legacy_host_save_path,
            "webui_origin": self.webui_origin,
            "category": self.category, "tags": list(self.tags),
            "poll_interval": self.poll_interval, "poll_timeout": self.poll_timeout,
            "allow_magnet_fallback": self.allow_magnet_fallback,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "QBittorrentSeedProfile":
        data = _mapping(value, {
            "ssh_alias", "host", "port", "save_path", "legacy_host_save_path",
            "webui_origin", "category", "tags",
            "poll_interval", "poll_timeout", "allow_magnet_fallback",
        }, "qBittorrent seed profile")
        if "tags" in data:
            if not isinstance(data["tags"], (list, tuple)):
                raise ValueError("tags must be an array")
            data["tags"] = tuple(str(item) for item in data["tags"])
        return cls(**data)


ANIBT_PUBLISH_PROFILE_VERSION = "anibt-publish-profile-v2"

_VALID_ANIME_ID_TYPES = frozenset({"bgm", "anilist", "mal", "anidb"})
_VALID_RESOLUTIONS = frozenset({"2160p", "1080p", "720p", "480p", "360p"})
_VALID_SUBTITLE_MODES = frozenset({"EXTERNAL", "INTERNAL", "EMBEDDED", "NONE"})
_VALID_FORMATS = frozenset({"MKV", "MP4", "AVI", "WEBM"})
_VALID_LANGUAGES = frozenset({
    "CHS", "CHT", "JP", "EN", "KO", "ES", "PT", "FR", "DE", "IT",
    "RU", "AR", "HI", "ID", "MS", "TH", "VI", "TL", "TR", "PL", "UK",
})


@dataclass(frozen=True)
class AnibtPublishProfile:
    """Profile for publishing a release to anibt.net."""

    anime_id_type: str = "bgm"
    anime_id: str = ""
    title: str = ""
    bgm_id: int | None = None
    episode_key: str = ""
    resolution: str = "1080p"
    language: tuple[str, ...] = ()
    subtitle: str = "INTERNAL"
    format: str = "MKV"
    version: str = ""
    file_size: int | None = None
    trackers: tuple[str, ...] = ()
    notes: str = ""
    preview: bool = False
    nyaa: bool = False
    nyaa_category: str = ""
    nyaa_complete: bool = False
    nyaa_remake: bool = False
    nyaa_description: str = ""
    nyaa_information: str = ""
    use_torrent_file: bool = True

    def __post_init__(self) -> None:
        if self.anime_id_type not in _VALID_ANIME_ID_TYPES:
            raise ValueError(f"anime_id_type must be one of {sorted(_VALID_ANIME_ID_TYPES)}")
        if not self.anime_id.strip():
            raise ValueError("anime_id must be a non-empty string")
        object.__setattr__(self, "anime_id", self.anime_id.strip())
        if not self.title.strip():
            raise ValueError("title must be a non-empty string")
        object.__setattr__(self, "title", self.title.strip())
        if self.bgm_id is not None and self.bgm_id <= 0:
            raise ValueError("bgm_id must be positive")
        if self.resolution not in _VALID_RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(_VALID_RESOLUTIONS)}")
        if self.subtitle not in _VALID_SUBTITLE_MODES:
            raise ValueError(f"subtitle must be one of {sorted(_VALID_SUBTITLE_MODES)}")
        if self.format not in _VALID_FORMATS:
            raise ValueError(f"format must be one of {sorted(_VALID_FORMATS)}")
        if self.file_size is not None and self.file_size <= 0:
            raise ValueError("file_size must be positive")
        for lang in self.language:
            if lang not in _VALID_LANGUAGES:
                raise ValueError(f"language {lang!r} is invalid; must be one of {sorted(_VALID_LANGUAGES)}")
        object.__setattr__(self, "language", tuple(self.language))
        object.__setattr__(self, "trackers", tuple(self.trackers))
        if not isinstance(self.preview, bool):
            raise ValueError("preview must be boolean")
        for field_name in ("nyaa", "nyaa_complete", "nyaa_remake"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be boolean")
        if self.nyaa:
            if not self.nyaa_category.strip():
                raise ValueError("nyaa_category is required when nyaa is enabled")
            if not any("nyaa.tracker.wf:7777/announce" in tracker for tracker in self.trackers):
                raise ValueError("nyaa publishing requires the Nyaa tracker in trackers")
        if len(self.nyaa_information) > 500:
            raise ValueError("nyaa_information must be at most 500 characters")
        if self.use_torrent_file is not True:
            raise ValueError("anibt anime releases require use_torrent_file=true")

    def api_fields(self) -> dict[str, Any]:
        return {
            "anime_id_type": self.anime_id_type,
            "anime_id": self.anime_id,
            "title": self.title,
            "bgm_id": self.bgm_id,
            "episode_key": self.episode_key,
            "resolution": self.resolution,
            "language": self.language,
            "subtitle": self.subtitle,
            "format": self.format,
            "version": self.version,
            "file_size": self.file_size,
            "trackers": self.trackers,
            "notes": self.notes,
            "preview": self.preview,
            "nyaa": self.nyaa,
            "nyaa_category": self.nyaa_category,
            "nyaa_complete": self.nyaa_complete,
            "nyaa_remake": self.nyaa_remake,
            "nyaa_description": self.nyaa_description,
            "nyaa_information": self.nyaa_information,
        }

    def receipt_summary(self) -> dict[str, Any]:
        return {
            "version": ANIBT_PUBLISH_PROFILE_VERSION,
            "anime_id_type": self.anime_id_type,
            "anime_id": self.anime_id,
            "title": self.title,
            "episode_key": self.episode_key,
            "resolution": self.resolution,
            "language": list(self.language),
            "subtitle": self.subtitle,
            "format": self.format,
            "version_tag": self.version,
            "preview": self.preview,
            "nyaa": self.nyaa,
        }

    def normalized(self) -> dict[str, Any]:
        return {
            "version": ANIBT_PUBLISH_PROFILE_VERSION,
            "anime_id_type": self.anime_id_type,
            "anime_id": self.anime_id,
            "title": self.title,
            "bgm_id": self.bgm_id,
            "episode_key": self.episode_key,
            "resolution": self.resolution,
            "language": list(self.language),
            "subtitle": self.subtitle,
            "format": self.format,
            "version_tag": self.version,
            "file_size": self.file_size,
            "trackers": list(self.trackers),
            "notes": self.notes,
            "preview": self.preview,
            "nyaa": self.nyaa,
            "nyaa_category": self.nyaa_category,
            "nyaa_complete": self.nyaa_complete,
            "nyaa_remake": self.nyaa_remake,
            "nyaa_description": self.nyaa_description,
            "nyaa_information": self.nyaa_information,
            "use_torrent_file": self.use_torrent_file,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AnibtPublishProfile":
        data = _mapping(value, {
            "anime_id_type", "anime_id", "title", "bgm_id", "episode_key",
            "resolution", "language", "subtitle", "format", "version",
            "file_size", "trackers", "notes", "preview",
            "nyaa", "nyaa_category", "nyaa_complete", "nyaa_remake",
            "nyaa_description", "nyaa_information", "use_torrent_file",
        }, "anibt publish profile")
        if "language" in data:
            if not isinstance(data["language"], (list, tuple)):
                raise ValueError("language must be an array")
            data["language"] = tuple(str(item) for item in data["language"])
        if "trackers" in data:
            if not isinstance(data["trackers"], (list, tuple)):
                raise ValueError("trackers must be an array")
            data["trackers"] = tuple(str(item) for item in data["trackers"])
        return cls(**data)
