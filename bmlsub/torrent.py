"""
种子生成 — 基于 libtorrent 生成 v1+v2 hybrid 种子

自动计算分块大小，默认附带 40+ 动漫 tracker，
输出到源文件同目录。
"""

from pathlib import Path

from ._backup import backup_if_exists

# ═══════════════════════════════════════════════════════════════
# 默认 Tracker 列表
# ═══════════════════════════════════════════════════════════════

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
    "http://208.67.16.113:8000/announce",
    "udp://208.67.16.113:8000/announce",
    "http://tracker.ktxp.com:6868/announce",
    "http://tracker.ktxp.com:7070/announce",
    "http://t2.popgo.org:7456/annonce",
    "http://bt.sc-ol.com:2710/announce",
    "http://share.camoe.cn:8080/announce",
    "http://61.154.116.205:8000/announce",
    "http://bt.rghost.net:80/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.publicbt.com:80/announce",
    "http://tracker.prq.to/announce",
    "http://open.nyaatorrents.info:6544/announce",
    "http://opentracker.acgnx.se/announce",
    "http://tracker.acgnx.se/announce",
    "http://t.acg.rip:6699/announce",
    "https://tracker.gbitt.info:443/announce",
    "udp://91.216.110.52:451/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://chihaya.toss.li:9696/announce",
    "udp://bt1.archive.org:6969/announce",
    "udp://bt2.archive.org:6969/announce",
    "udp://52.58.128.163:6969/announce",
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


class TorrentCreator:
    """种子生成器 — 基于 libtorrent，输出 v1+v2 hybrid 种子

    Parameters
    ----------
    trackers : list[str] | None
        自定义 tracker 列表。None = 使用 DEFAULT_TRACKERS。
    extra_trackers : list[str] | None
        额外追加的 tracker，叠加在 trackers 之后（去重）。
    piece_length : int | None
        分块大小（字节）。None = 根据总数据量自动计算。
    comment : str
        种子注释。
    created_by : str
        创建者标识。
    """

    def __init__(
        self,
        trackers: list[str] | None = None,
        extra_trackers: list[str] | None = None,
        piece_length: int | None = None,
        comment: str = "",
        created_by: str = "BML",
    ):
        # 合并 tracker 列表
        base = list(trackers) if trackers is not None else list(DEFAULT_TRACKERS)
        if extra_trackers:
            for url in extra_trackers:
                if url not in base:
                    base.append(url)
        self._trackers: list[str] = base
        self._piece_length: int | None = piece_length
        self._comment: str = comment
        self._created_by: str = created_by

    # ── 公共 API ──────────────────────────────────────

    def create(self, src: Path | str, dst: Path | str | None = None,
               v1_only: bool = False) -> Path:
        """生成 .torrent 种子文件

        Parameters
        ----------
        src : Path | str
            源文件或目录路径。
        dst : Path | str | None
            输出 .torrent 路径。None = 自动放在 src 同目录下。
        v1_only : bool
            True = 仅生成 v1 种子；False = v1+v2 hybrid（默认）。

        Returns
        -------
        Path
            生成的 .torrent 文件路径
        """
        import libtorrent as lt

        src = Path(src).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"源路径不存在: {src}")

        # ── 输出路径 ──
        if dst is None:
            dst = src.parent / f"{src.name}.torrent"
        dst = Path(dst).expanduser().resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)

        # 备份旧种子
        if dst.exists():
            bak = backup_if_exists(dst)
            if bak:
                print(f"📦 已备份旧种子 → {bak.name}")

        # ── 文件存储 ──
        fs = lt.file_storage()
        lt.add_files(fs, str(src))

        # ── 分块大小 ──
        piece_size = self._piece_length or self._estimate_piece_size(src)
        print(f"📦 分块大小: {piece_size // 1024} KB  ({self._num_pieces_str(src, piece_size)})")

        # ── 创建 torrent ──
        flags = lt.create_torrent.v1_only if v1_only else 0
        t = lt.create_torrent(fs, piece_size=piece_size, flags=flags)

        # 添加 tracker（每个独立 tier）
        for tier, url in enumerate(self._trackers):
            t.add_tracker(url, tier=tier)

        t.set_creator(self._created_by)
        if self._comment:
            t.set_comment(self._comment)

        # ── 计算哈希 ──
        parent = str(src.parent) if src.is_file() else str(src.parent)
        print(f"🔍 正在计算分块哈希...")
        lt.set_piece_hashes(t, parent)

        # ── 写入文件 ──
        torrent_data = t.generate()
        dst.write_bytes(lt.bencode(torrent_data))

        # ── 汇总 ──
        trackers_count = len(self._trackers)
        fmt_label = "v1" if v1_only else "v1+v2 hybrid"
        print(f"✅ 种子已生成: {dst.name}")
        print(f"   Tracker: {trackers_count} 个")
        print(f"   格式: {fmt_label}")
        return dst

    # ── 分块大小自动计算 ──────────────────────────────

    @staticmethod
    def _estimate_piece_size(src: Path) -> int:
        """根据源数据总量自动选择合适的分块大小

        目标: 块数控制在 1000-2000 之间。
        """
        total = TorrentCreator._total_size(src)
        return TorrentCreator._calc_piece_size(total)

    @staticmethod
    def _calc_piece_size(total_bytes: int) -> int:
        """根据总字节数返回推荐的分块大小"""
        mb = total_bytes / (1024 * 1024)

        if mb < 64:
            return 64 * 1024        # 64 KB
        elif mb < 512:
            return 256 * 1024       # 256 KB
        elif mb < 2048:
            return 1 * 1024 * 1024  # 1 MB
        elif mb < 8192:
            return 4 * 1024 * 1024  # 4 MB
        else:
            return 8 * 1024 * 1024  # 8 MB

    @staticmethod
    def _num_pieces_str(src: Path, piece_size: int) -> str:
        total = TorrentCreator._total_size(src)
        n = max(1, (total + piece_size - 1) // piece_size)
        return f"约 {n} 块"

    @staticmethod
    def _total_size(src: Path) -> int:
        """递归计算源文件/目录的总大小"""
        if src.is_file():
            return src.stat().st_size
        total = 0
        for f in src.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
        return total
