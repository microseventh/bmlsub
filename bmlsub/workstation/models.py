"""Configuration models for the three workstation phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .naming import ProductKind, ReleaseNames, intermediate_path, product_path, product_torrent_path
from .series import SeriesContext


@dataclass(frozen=True)
class TrackSelection:
    stream_index: int | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        if self.stream_index is not None and self.stream_index < 0:
            raise ValueError("track stream index must be non-negative")
        if self.language is not None:
            object.__setattr__(self, "language", self.language.strip().lower() or None)

    def to_dict(self) -> dict[str, Any]:
        return {"stream_index": self.stream_index, "language": self.language}


@dataclass(frozen=True)
class TranscriptionJob:
    name: str
    mode: str = "direct"
    model: str = "mlx-community/whisper-large-v3-turbo"
    model_revision: str = "main"
    language: str = "ja"
    chunk_seconds: float = 240.0
    overlap_seconds: float = 5.0
    manual_cuts: tuple[float, ...] = ()
    throttle_seconds: float = 0.0
    decoding: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name or not all(character.isalnum() or character in "._-" for character in name):
            raise ValueError("transcription job name is invalid")
        if self.mode not in {"direct", "chunked", "both"}:
            raise ValueError("transcription mode must be direct, chunked, or both")
        if self.chunk_seconds <= 0 or self.overlap_seconds < 0 or self.overlap_seconds >= self.chunk_seconds:
            raise ValueError("transcription chunk timing is invalid")
        if self.throttle_seconds < 0:
            raise ValueError("transcription throttle must be non-negative")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "manual_cuts", tuple(self.manual_cuts))
        object.__setattr__(self, "decoding", dict(self.decoding))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "mode": self.mode, "model": self.model,
            "model_revision": self.model_revision, "language": self.language,
            "chunk_seconds": self.chunk_seconds, "overlap_seconds": self.overlap_seconds,
            "manual_cuts": list(self.manual_cuts), "throttle_seconds": self.throttle_seconds,
            "decoding": dict(self.decoding),
        }


@dataclass(frozen=True)
class PreprocessConfig:
    source_video: Path | str | None = None
    reference_track: TrackSelection = field(default_factory=lambda: TrackSelection(language="eng"))
    audio_track: TrackSelection = field(default_factory=lambda: TrackSelection(language="jpn"))
    whisper_jobs: Sequence[TranscriptionJob] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_video": str(self.source_video) if self.source_video else None,
            "reference_track": self.reference_track.to_dict(),
            "audio_track": self.audio_track.to_dict(),
            "whisper_jobs": [item.to_dict() for item in self.whisper_jobs],
        }


@dataclass(frozen=True)
class DeliveryConfig:
    names: ReleaseNames
    production_subtitle: Path | str | None = None
    hardsub_parameters: Mapping[str, Any] = field(default_factory=dict)
    hevc_parameters: Mapping[str, Any] = field(default_factory=dict)
    ass_profile: Mapping[str, Any] = field(default_factory=dict)
    torrent_profile: Mapping[str, Any] = field(default_factory=lambda: {"format": "v1"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "hardsub_parameters", dict(self.hardsub_parameters))
        object.__setattr__(self, "hevc_parameters", dict(self.hevc_parameters))
        object.__setattr__(self, "ass_profile", dict(self.ass_profile))
        object.__setattr__(self, "torrent_profile", dict(self.torrent_profile))

    def to_dict(self) -> dict[str, Any]:
        return {
            "names": self.names.to_dict(),
            "production_subtitle": str(self.production_subtitle) if self.production_subtitle else None,
            "hardsub_parameters": dict(self.hardsub_parameters),
            "hevc_parameters": dict(self.hevc_parameters),
            "ass_profile": dict(self.ass_profile),
            "torrent_profile": dict(self.torrent_profile),
        }


@dataclass(frozen=True)
class PublishConfig:
    r2_folder: str | None = None
    r2_bucket: str = "bml"
    r2_access: str = "private"
    r2_public_base_url: str | None = None
    rclone_remote: str = "r2"
    ssh_alias: str | None = None
    remote_dir: str | None = None
    qb_port: int = 8080
    qb_webui_origin: str | None = None
    bgm_id: int | None = None
    anime_id: str | None = None
    notes: str = ""
    credential_manifest: Path | str | None = None
    r2_credential_profile: str | None = None
    ssh_profile: str | None = None
    qb_credential_profile: str | None = None
    anibt_credential_profile: str | None = None

    def __post_init__(self) -> None:
        folder = self.r2_folder.strip().strip("/") if self.r2_folder else None
        if folder and any(part in {"", ".", ".."} for part in PurePosixPath(folder).parts):
            raise ValueError("r2_folder contains an unsafe path segment")
        if self.remote_dir:
            remote = PurePosixPath(self.remote_dir)
            if not remote.is_absolute() or any(part in {"", ".", ".."} for part in remote.parts[1:]):
                raise ValueError("remote_dir must be a normalized absolute POSIX path")
        if not 1 <= self.qb_port <= 65535:
            raise ValueError("qb_port is invalid")
        if self.bgm_id is not None and self.bgm_id <= 0:
            raise ValueError("bgm_id must be positive")
        object.__setattr__(self, "r2_folder", folder)
        if self.credential_manifest is not None:
            object.__setattr__(self, "credential_manifest", Path(self.credential_manifest).expanduser())

    def object_key(self, episode_id: str, path: Path | str,
                   *, series_folder_name: str | None = None) -> str:
        prefix = series_folder_name or self.r2_folder
        parts = [item for item in (prefix, episode_id, Path(path).name) if item]
        return "/".join(parts)

    def remote_target(self, path: Path | str, *, series_folder_name: str | None = None,
                      episode_id: str | None = None) -> str:
        if not self.remote_dir:
            raise ValueError("remote_dir is not configured")
        base = PurePosixPath(self.remote_dir)
        if series_folder_name and episode_id:
            base = base / series_folder_name / episode_id
        return str(base / Path(path).name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "r2_folder": self.r2_folder, "r2_bucket": self.r2_bucket,
            "r2_access": self.r2_access, "r2_public_base_url": self.r2_public_base_url,
            "rclone_remote": self.rclone_remote, "ssh_alias": self.ssh_alias,
            "remote_dir": self.remote_dir, "qb_port": self.qb_port,
            "qb_webui_origin": self.qb_webui_origin, "bgm_id": self.bgm_id,
            "anime_id": self.anime_id, "notes": self.notes,
            "credential_manifest": str(self.credential_manifest) if self.credential_manifest else None,
            "r2_credential_profile": self.r2_credential_profile,
            "ssh_profile": self.ssh_profile, "qb_credential_profile": self.qb_credential_profile,
            "anibt_credential_profile": self.anibt_credential_profile,
        }


@dataclass(frozen=True)
class WorkstationConfig:
    workspace: Path | str
    episode_id: str
    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    delivery: DeliveryConfig | None = None
    publish: PublishConfig = field(default_factory=PublishConfig)
    workflow_id: str | None = None
    series_context: SeriesContext | None = None

    def __post_init__(self) -> None:
        root = Path(self.workspace).expanduser().resolve()
        episode = self.episode_id.strip()
        if not episode or any(character in episode for character in ("/", "\\", "\x00")):
            raise ValueError("episode_id is invalid")
        object.__setattr__(self, "workspace", root)
        object.__setattr__(self, "episode_id", episode)
        object.__setattr__(self, "workflow_id", self.workflow_id or f"episode-{episode}")
        if self.series_context is not None:
            if self.series_context.episode_dir != root or self.series_context.episode_id != episode:
                raise ValueError("series context does not match workstation episode")

    @classmethod
    def from_series_context(
        cls, context: SeriesContext, *, preprocess: PreprocessConfig | None = None,
        delivery: DeliveryConfig | None = None, publish: PublishConfig | None = None,
    ) -> "WorkstationConfig":
        metadata = context.metadata
        names = ReleaseNames(
            metadata.group_chs, metadata.group_cht, metadata.title_chs,
            metadata.title_cht, metadata.romanized_title,
        )
        production = dict(metadata.production)
        delivery_config = delivery or DeliveryConfig(
            names=names,
            hardsub_parameters=production.get("hardsub_parameters", {}),
            hevc_parameters=production.get("hevc_parameters", {}),
            ass_profile=production.get("ass_profile", {}),
            torrent_profile=production.get("torrent_profile", {"format": "v1"}),
        )
        publication = dict(metadata.publish)
        aliases = dict(publication.get("credential_aliases", {}))
        publish_config = publish or PublishConfig(
            r2_bucket=str(publication.get("r2_bucket", "bml")),
            r2_access=str(publication.get("r2_access", "private")),
            r2_public_base_url=publication.get("r2_public_base_url"),
            rclone_remote=str(publication.get("rclone_remote", "r2")),
            ssh_alias=publication.get("ssh_alias"),
            remote_dir=publication.get("remote_root"),
            qb_port=int(publication.get("qb_port", 8080)),
            qb_webui_origin=publication.get("qb_webui_origin"),
            bgm_id=metadata.bgm_id, anime_id=metadata.anime_id,
            notes=str(publication.get("notes", "")),
            r2_credential_profile=aliases.get("r2"), ssh_profile=aliases.get("ssh"),
            qb_credential_profile=aliases.get("qbittorrent"),
            anibt_credential_profile=aliases.get("anibt"),
        )
        return cls(
            context.episode_dir, context.episode_id,
            preprocess=preprocess or PreprocessConfig(), delivery=delivery_config,
            publish=publish_config, series_context=context,
        )

    @property
    def state_dir(self) -> Path:
        return self.workspace / "workstation" / "state"

    def product_paths(self) -> dict[str, Path]:
        if self.delivery is None:
            return {}
        return {
            ProductKind.MP4_CHS.value: product_path(self.workspace, self.episode_id, ProductKind.MP4_CHS, self.delivery.names),
            ProductKind.MP4_CHT.value: product_path(self.workspace, self.episode_id, ProductKind.MP4_CHT, self.delivery.names),
            ProductKind.MKV_HEVC.value: product_path(self.workspace, self.episode_id, ProductKind.MKV_HEVC, self.delivery.names),
        }

    def intermediate_path(self) -> Path:
        return intermediate_path(self.workspace, self.episode_id)

    def torrent_paths(self) -> dict[str, Path]:
        return {key: product_torrent_path(value, self.workspace) for key, value in self.product_paths().items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "workstation-config-v1", "workflow_id": self.workflow_id,
            "workspace": str(self.workspace), "episode_id": self.episode_id,
            "state_dir": str(self.state_dir), "preprocess": self.preprocess.to_dict(),
            "delivery": self.delivery.to_dict() if self.delivery else None,
            "publish": self.publish.to_dict(),
            "series": self.series_context.to_dict() if self.series_context else None,
            "products": {key: str(value) for key, value in self.product_paths().items()},
            "torrents": {key: str(value) for key, value in self.torrent_paths().items()},
        }
