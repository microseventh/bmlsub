"""Public local and external release APIs."""

from ..credentials import SecretStore
from .anibt import ANIBT_ADAPTER_VERSION, ANIBT_RECEIPT_SCHEMA, AnibtClient, RequestsAnibtClient
from .anibt_execution import ANIBT_PUBLISH_ARTIFACT_TYPE, ANIBT_PUBLISH_STAGE, run_anibt_publish
from .credentials import (
    AnibtCredentials, QBittorrentCredentials, R2Credentials,
    resolve_anibt_credentials, resolve_qbittorrent_credentials,
    resolve_r2_credentials,
)
from .execution import TORRENT_ARTIFACT_TYPE, TORRENT_STAGE, run_torrent_creation
from .external_profiles import ANIBT_PUBLISH_PROFILE_VERSION, AnibtPublishProfile, QBittorrentSeedProfile, R2UploadProfile, RemotePullProfile
from .profiles import TorrentProfile, normalize_torrent_profile
from .qbittorrent import QBittorrentClient, SSHQBittorrentClient, SeedIdentity
from .r2 import Boto3R2Client, R2Client, R2ObjectIdentity
from .r2_execution import R2_RECEIPT_ARTIFACT_TYPE, R2_UPLOAD_STAGE, run_r2_upload
from .remote import RemoteFileIdentity, RemotePullClient, SSHRclonePullClient
from .remote_execution import REMOTE_FILE_ARTIFACT_TYPE, REMOTE_PULL_STAGE, run_remote_pull
from .seeding_execution import QB_SEED_ARTIFACT_TYPE, QB_SEED_STAGE, run_qbittorrent_seed
from .torrent import (
    TorrentMetadata,
    build_magnet_uri,
    libtorrent_version,
    read_torrent_metadata,
    validate_torrent,
)
from .trackers import LEGACY_TRACKERS, TrackerListClient, resolve_trackers

__all__ = [
    "ANIBT_ADAPTER_VERSION", "ANIBT_PUBLISH_ARTIFACT_TYPE",
    "ANIBT_PUBLISH_PROFILE_VERSION", "ANIBT_PUBLISH_STAGE",
    "ANIBT_RECEIPT_SCHEMA", "AnibtClient", "AnibtCredentials",
    "AnibtPublishProfile",
    "Boto3R2Client", "LEGACY_TRACKERS",
    "QB_SEED_ARTIFACT_TYPE", "QB_SEED_STAGE",
    "QBittorrentClient", "QBittorrentCredentials", "QBittorrentSeedProfile",
    "R2Client", "R2Credentials", "R2ObjectIdentity", "R2UploadProfile",
    "R2_RECEIPT_ARTIFACT_TYPE", "R2_UPLOAD_STAGE", "REMOTE_FILE_ARTIFACT_TYPE",
    "REMOTE_PULL_STAGE", "RemoteFileIdentity", "RemotePullClient", "RemotePullProfile",
    "RequestsAnibtClient",
    "SSHQBittorrentClient", "SSHRclonePullClient", "SecretStore",
    "SeedIdentity",
    "TORRENT_ARTIFACT_TYPE", "TORRENT_STAGE", "TorrentMetadata", "TorrentProfile",
    "TrackerListClient", "build_magnet_uri", "libtorrent_version",
    "normalize_torrent_profile", "read_torrent_metadata", "resolve_qbittorrent_credentials",
    "resolve_anibt_credentials", "resolve_r2_credentials", "resolve_trackers",
    "run_anibt_publish", "run_qbittorrent_seed", "run_r2_upload",
    "run_remote_pull", "run_torrent_creation", "validate_torrent",
]
