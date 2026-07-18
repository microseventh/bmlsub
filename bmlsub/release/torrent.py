"""BitTorrent creation, loading, and validation backed by libtorrent."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..execution.errors import BmlsubError, ErrorCode
from .profiles import TorrentProfile


TORRENT_BACKEND_VERSION = "libtorrent-adapter-v2"
TORRENT_CREATOR_VERSION = "libtorrent-creator-v1"
TORRENT_READER_VERSION = "libtorrent-reader-v1"
TORRENT_VALIDATOR_VERSION = "libtorrent-validator-v2"
PIECE_SIZE_POLICY_VERSION = "torrent-piece-size-v1"
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class TorrentMetadata:
    name: str
    length: int
    piece_length: int
    piece_count: int
    format: str
    info_hash_v1: str
    info_hash_v2: str | None
    torrent_id: str
    preferred_hash_algorithm: str
    trackers: tuple[str, ...]
    tracker_tiers: tuple[int, ...]
    private: bool
    comment: str
    created_by: str
    magnet_uri: str

    def bounded(self) -> dict[str, str | int | bool | None]:
        return {
            "name": self.name,
            "length": self.length,
            "piece_length": self.piece_length,
            "piece_count": self.piece_count,
            "format": self.format,
            "info_hash_v1": self.info_hash_v1,
            "info_hash_v2": self.info_hash_v2,
            "torrent_id": self.torrent_id,
            "preferred_hash_algorithm": self.preferred_hash_algorithm,
            "tracker_count": len(self.trackers),
            "private": self.private,
            "comment": self.comment,
            "created_by": self.created_by,
        }


def libtorrent_version() -> str:
    lt = _load_libtorrent()
    value = getattr(lt, "__version__", None) or getattr(lt, "version", None)
    if callable(value):
        value = value()
    if not value:
        raise BmlsubError(
            "libtorrent binding does not expose its version",
            code=ErrorCode.DEPENDENCY_MISSING,
        )
    return str(value)


def choose_piece_length(total_bytes: int) -> int:
    if total_bytes <= 0:
        raise ValueError("torrent source must be non-empty")
    mib = total_bytes / (1024 * 1024)
    if mib < 64:
        return 64 * 1024
    if mib < 512:
        return 256 * 1024
    if mib < 2048:
        return 1024 * 1024
    if mib < 8192:
        return 4 * 1024 * 1024
    return 8 * 1024 * 1024


def create_torrent(
    source: Path | str,
    target: Path | str,
    *,
    trackers: tuple[str, ...],
    profile: TorrentProfile,
    expected_sha256: str | None = None,
) -> TorrentMetadata:
    path = Path(source).expanduser().resolve()
    output = Path(target).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("torrent source must be a non-empty regular file")
    if not trackers:
        raise ValueError("torrent requires at least one tracker")

    before = path.stat()
    source_sha256 = _sha256_file(path)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError("torrent source content no longer matches its Artifact")
    piece_length = profile.piece_length or choose_piece_length(before.st_size)
    payload = _generate_torrent_bytes(path, trackers, profile, piece_length)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("torrent source changed while libtorrent was hashing it")
    output.write_bytes(payload)
    return validate_torrent(
        output,
        source=path,
        expected_trackers=trackers,
        profile=profile,
        expected_sha256=source_sha256,
    )


def read_torrent_metadata(path: Path | str) -> TorrentMetadata:
    lt = _load_libtorrent()
    torrent_path = Path(path).expanduser().resolve()
    if not torrent_path.is_file():
        raise FileNotFoundError(f"torrent file does not exist: {torrent_path}")
    try:
        info = lt.torrent_info(str(torrent_path))
    except Exception as exc:
        raise ValueError(f"libtorrent could not load torrent metadata: {exc}") from exc
    return _metadata_from_info(info)


def validate_torrent(
    path: Path | str,
    *,
    source: Path | str,
    expected_trackers: tuple[str, ...],
    profile: TorrentProfile,
    expected_sha256: str | None = None,
) -> TorrentMetadata:
    torrent_path = Path(path).expanduser().resolve()
    source_path = Path(source).expanduser().resolve()
    metadata = read_torrent_metadata(torrent_path)
    expected_tiers = tuple(range(len(expected_trackers)))
    if metadata.trackers != expected_trackers or metadata.tracker_tiers != expected_tiers:
        raise ValueError("torrent tracker tiers do not match the resolved list")
    if metadata.name != source_path.name or metadata.length != source_path.stat().st_size:
        raise ValueError("torrent file mapping does not match the source Artifact")

    expected_piece_length = profile.piece_length or choose_piece_length(metadata.length)
    if metadata.piece_length != expected_piece_length:
        raise ValueError("torrent piece length does not match the Profile")
    expected_piece_count = (metadata.length + expected_piece_length - 1) // expected_piece_length
    if metadata.piece_count != expected_piece_count:
        raise ValueError("torrent piece count is invalid")
    if metadata.format != profile.format:
        raise ValueError("torrent format does not match the Profile")
    if metadata.private != profile.private or metadata.comment != profile.comment:
        raise ValueError("torrent private/comment fields do not match the Profile")
    if metadata.created_by != profile.created_by:
        raise ValueError("torrent creator does not match the Profile")

    before = source_path.stat()
    source_sha256 = _sha256_file(source_path)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError("source SHA-256 changed during torrent validation")
    rebuilt = _metadata_from_bytes(
        _generate_torrent_bytes(
            source_path,
            expected_trackers,
            profile,
            expected_piece_length,
        )
    )
    after = source_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("torrent source changed during libtorrent validation")
    if metadata.info_hash_v1 != rebuilt.info_hash_v1:
        raise ValueError("torrent v1 hash does not match the source Artifact")
    if metadata.info_hash_v2 != rebuilt.info_hash_v2:
        raise ValueError("torrent v2 hash does not match the source Artifact")
    return metadata


def build_magnet_uri(metadata: TorrentMetadata, *, prefer: str = "v1") -> str:
    if prefer != "v1":
        raise ValueError("only v1 is supported as the preferred torrent identity")
    params: list[tuple[str, str]] = [("xt", f"urn:btih:{metadata.info_hash_v1}")]
    if metadata.info_hash_v2:
        params.append(("xt", f"urn:btmh:1220{metadata.info_hash_v2}"))
    params.append(("dn", metadata.name))
    params.extend(("tr", tracker) for tracker in metadata.trackers)
    return f"magnet:?{urlencode(params)}"


def _generate_torrent_bytes(
    source: Path,
    trackers: tuple[str, ...],
    profile: TorrentProfile,
    piece_length: int,
) -> bytes:
    lt = _load_libtorrent()
    storage = lt.file_storage()
    lt.add_files(storage, str(source))
    flags = lt.create_torrent.v1_only if profile.format == "v1" else 0
    creator = lt.create_torrent(storage, piece_size=piece_length, flags=flags)
    for tier, tracker in enumerate(trackers):
        creator.add_tracker(tracker, tier=tier)
    creator.set_creator(profile.created_by)
    creator.set_priv(profile.private)
    if profile.comment:
        creator.set_comment(profile.comment)
    lt.set_piece_hashes(creator, str(source.parent))
    return bytes(lt.bencode(creator.generate()))


def _metadata_from_bytes(payload: bytes) -> TorrentMetadata:
    lt = _load_libtorrent()
    try:
        info = lt.torrent_info(payload)
    except Exception as exc:
        raise ValueError(f"libtorrent could not load generated torrent metadata: {exc}") from exc
    return _metadata_from_info(info)


def _metadata_from_info(info: Any) -> TorrentMetadata:
    hashes = info.info_hashes()
    has_v1 = bool(hashes.has_v1())
    has_v2 = bool(hashes.has_v2())
    if not has_v1:
        raise ValueError("torrent does not contain the required v1 info hash")
    info_hash_v1 = str(_binding_value(hashes, "v1")).lower()
    info_hash_v2 = str(_binding_value(hashes, "v2")).lower() if has_v2 else None

    files = info.files()
    if files.num_files() != 1:
        raise ValueError("torrent must contain exactly one file")
    name = str(files.file_path(0))
    if Path(name).name != name:
        raise ValueError("torrent file mapping must be a single root file")

    tracker_entries = tuple(info.trackers())
    trackers = tuple(str(item.url) for item in tracker_entries)
    tiers = tuple(int(item.tier) for item in tracker_entries)
    if not trackers:
        raise ValueError("torrent announce list is missing")
    if len(set(trackers)) != len(trackers):
        raise ValueError("torrent tracker tiers contain duplicates")

    format_name = "hybrid" if has_v2 else "v1"
    provisional = TorrentMetadata(
        name=name,
        length=int(files.file_size(0)),
        piece_length=int(info.piece_length()),
        piece_count=int(info.num_pieces()),
        format=format_name,
        info_hash_v1=info_hash_v1,
        info_hash_v2=info_hash_v2,
        torrent_id=info_hash_v1,
        preferred_hash_algorithm="v1",
        trackers=trackers,
        tracker_tiers=tiers,
        private=bool(info.priv()),
        comment=str(info.comment()),
        created_by=str(info.creator()),
        magnet_uri="",
    )
    return TorrentMetadata(**{**provisional.__dict__, "magnet_uri": build_magnet_uri(provisional)})


def _binding_value(value: Any, name: str) -> Any:
    member = getattr(value, name)
    return member() if callable(member) else member


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _load_libtorrent() -> Any:
    try:
        import libtorrent as lt
    except (ImportError, OSError) as exc:
        raise BmlsubError(
            "libtorrent>=2.0 is required for torrent operations",
            code=ErrorCode.DEPENDENCY_MISSING,
            details={"dependency": "libtorrent"},
        ) from exc
    if not hasattr(lt, "create_torrent") or not hasattr(lt, "torrent_info"):
        raise BmlsubError(
            "installed libtorrent binding is incompatible",
            code=ErrorCode.DEPENDENCY_MISSING,
            details={"dependency": "libtorrent"},
        )
    return lt
