"""
种子生成 — 基于 libtorrent 生成 v1 或 v1+v2 种子
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ._backup import backup_if_exists


DEFAULT_TRACKERS: list[str] = [
    "http://nyaa.tracker.wf:7777/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "https://tracker.bangumi.zip/announce",
    "https://tr.bangumi.moe:9696/announce",
    "http://tr.bangumi.moe:6969/announce",
    "udp://tr.bangumi.moe:6969/announce",
    "http://open.acgtracker.com:1096/announce",
    "http://tracker.ktxp.com:6868/announce",
    "http://tracker.ktxp.com:7070/announce",
    "http://t2.popgo.org:7456/annonce",
    "http://bt.sc-ol.com:2710/announce",
    "http://share.camoe.cn:8080/announce",
    "http://bt.rghost.net:80/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.publicbt.com:80/announce",
    "http://tracker.prq.to/announce",
    "http://open.nyaatorrents.info:6544/announce",
    "http://opentracker.acgnx.se/announce",
    "http://tracker.acgnx.se/announce",
    "http://t.acg.rip:6699/announce",
    "https://tracker.gbitt.info:443/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://chihaya.toss.li:9696/announce",
    "udp://bt1.archive.org:6969/announce",
    "udp://bt2.archive.org:6969/announce",
    "udp://opentracker.i2p.rocks:6969/announce",
    "udp://retracker.lanta-net.ru:2710/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "http://tracker.corpscorp.online:80/announce",
    "http://tracker.dler.org:6969/announce",
    "http://pow7.com:80/announce",
    "udp://ipv6.tracker.harry.lu:80/announce",
    "http://ipv6.tracker.harry.lu:80/announce",
    "http://tracker.ipv6tracker.ru/announce",
]


@dataclass(frozen=True)
class TorrentMetadata:
    """本地 .torrent 的元数据与可用于远程添加的磁力链接。"""

    name: str
    info_hash_v1: str | None
    info_hash_v2: str | None
    trackers: tuple[str, ...]
    magnet_uri: str


@dataclass(frozen=True)
class TorrentPlan:
    """单个目标的种子生成计划。"""

    src: Path
    dst: Path
    piece_size: int
    v1_only: bool
    tracker_count: int

    def summary(self) -> dict:
        return {
            "src": str(self.src),
            "dst": str(self.dst),
            "piece_size": self.piece_size,
            "v1_only": self.v1_only,
            "tracker_count": self.tracker_count,
        }


@dataclass(frozen=True)
class TorrentBatchPlan:
    """多个目标的种子生成计划。"""

    plans: tuple[TorrentPlan, ...]

    def summary(self) -> list[dict]:
        return [plan.summary() for plan in self.plans]


def read_torrent_metadata(torrent_path: Path | str) -> TorrentMetadata:
    """读取本地 .torrent，并生成包含名称和 tracker 的标准磁力链接。"""
    import libtorrent as lt

    torrent_path = Path(torrent_path).expanduser().resolve()
    if not torrent_path.is_file():
        raise FileNotFoundError(f"种子文件不存在: {torrent_path}")

    info = lt.torrent_info(str(torrent_path))
    hashes = info.info_hashes()
    v1_hash = str(hashes.v1) if hashes.has_v1() else None
    v2_hash = str(hashes.v2) if hashes.has_v2() else None
    trackers = tuple(dict.fromkeys(tracker.url for tracker in info.trackers() if tracker.url))

    params: list[tuple[str, str]] = []
    if v1_hash:
        params.append(("xt", f"urn:btih:{v1_hash}"))
    if v2_hash:
        params.append(("xt", f"urn:btmh:1220{v2_hash}"))
    if not params:
        raise ValueError(f"种子不包含可用的 info hash: {torrent_path}")
    params.append(("dn", info.name()))
    params.extend(("tr", tracker) for tracker in trackers)

    return TorrentMetadata(
        name=info.name(),
        info_hash_v1=v1_hash,
        info_hash_v2=v2_hash,
        trackers=trackers,
        magnet_uri=f"magnet:?{urlencode(params)}",
    )


class TorrentCreator:
    """种子生成器。"""

    def __init__(
        self,
        trackers: list[str] | None = None,
        extra_trackers: list[str] | None = None,
        piece_length: int | None = None,
        comment: str = "",
        created_by: str = "BML",
    ):
        base = list(trackers) if trackers is not None else list(DEFAULT_TRACKERS)
        if extra_trackers:
            for url in extra_trackers:
                if url not in base:
                    base.append(url)
        self._trackers: list[str] = base
        self._piece_length: int | None = piece_length
        self._comment: str = comment
        self._created_by: str = created_by

    def build_plan(self,
                   src: Path | str,
                   dst: Path | str | None = None,
                   v1_only: bool = False) -> TorrentPlan:
        src = Path(src).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"源路径不存在: {src}")
        if dst is None:
            dst = src.parent / f"{src.name}.torrent"
        dst = Path(dst).expanduser().resolve()
        piece_size = self._piece_length or self._estimate_piece_size(src)
        return TorrentPlan(
            src=src,
            dst=dst,
            piece_size=piece_size,
            v1_only=v1_only,
            tracker_count=len(self._trackers),
        )

    def build_batch_plan(self,
                         sources: list[Path | str],
                         v1_only: bool = False) -> TorrentBatchPlan:
        plans = tuple(self.build_plan(src, v1_only=v1_only) for src in sources)
        return TorrentBatchPlan(plans=plans)

    def create(self, src: Path | str, dst: Path | str | None = None,
               v1_only: bool = False) -> Path:
        import libtorrent as lt

        plan = self.build_plan(src, dst=dst, v1_only=v1_only)
        plan.dst.parent.mkdir(parents=True, exist_ok=True)

        if plan.dst.exists():
            bak = backup_if_exists(plan.dst)
            if bak:
                print(f"📦 已备份旧种子 → {bak.name}")

        fs = lt.file_storage()
        lt.add_files(fs, str(plan.src))

        print(f"📦 分块大小: {plan.piece_size // 1024} KB  ({self._num_pieces_str(plan.src, plan.piece_size)})")

        flags = lt.create_torrent.v1_only if plan.v1_only else 0
        torrent = lt.create_torrent(fs, piece_size=plan.piece_size, flags=flags)
        for tier, url in enumerate(self._trackers):
            torrent.add_tracker(url, tier=tier)

        torrent.set_creator(self._created_by)
        if self._comment:
            torrent.set_comment(self._comment)

        print("🔍 正在计算分块哈希...")
        lt.set_piece_hashes(torrent, str(plan.src.parent))

        plan.dst.write_bytes(lt.bencode(torrent.generate()))

        fmt_label = "v1" if plan.v1_only else "v1+v2 hybrid"
        print(f"✅ 种子已生成: {plan.dst.name}")
        print(f"   Tracker: {plan.tracker_count} 个")
        print(f"   格式: {fmt_label}")
        return plan.dst

    def create_many(self, sources: list[Path | str], v1_only: bool = False) -> list[Path]:
        results: list[Path] = []
        for src in sources:
            results.append(self.create(src, v1_only=v1_only))
            print()
        return results

    @staticmethod
    def _estimate_piece_size(src: Path) -> int:
        total = TorrentCreator._total_size(src)
        return TorrentCreator._calc_piece_size(total)

    @staticmethod
    def _calc_piece_size(total_bytes: int) -> int:
        mb = total_bytes / (1024 * 1024)
        if mb < 64:
            return 64 * 1024
        if mb < 512:
            return 256 * 1024
        if mb < 2048:
            return 1 * 1024 * 1024
        if mb < 8192:
            return 4 * 1024 * 1024
        return 8 * 1024 * 1024

    @staticmethod
    def _num_pieces_str(src: Path, piece_size: int) -> str:
        total = TorrentCreator._total_size(src)
        count = max(1, (total + piece_size - 1) // piece_size)
        return f"约 {count} 块"

    @staticmethod
    def _total_size(src: Path) -> int:
        if src.is_file():
            return src.stat().st_size
        total = 0
        for path in src.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
        return total
