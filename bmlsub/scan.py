"""
产物命名与检测 — 根据命名模板生成阶段 5/6 产物路径，检测前置文件

设计原则：
- 命名模板（前缀）在 notebook cell-1 中定义
- 库函数只负责拼接路径和检查文件存在性
- 换项目时只需改 cell-1 中的前缀常量
"""

from pathlib import Path

# ── 产物文件名模板（不含前缀） ──────────────────
# key 对应 product_key 参数，{prefix} {ep_id} 由调用方填入

PRODUCT_FORMATS = {
    'mp4_chs':  "{prefix} [{ep_id}][1080P][简日内嵌].mp4",
    'mp4_cht':  "{prefix} [{ep_id}][1080P][繁日內嵌].mp4",
    'mkv_hevc': "{prefix} [{ep_id}][1080P][HEVC-10bit][简繁日内封].mkv",
}

# 哪些 key 使用繁体前缀（繁日版标题中「继」→「繼」等）
_CHT_KEYS = {'mp4_cht'}


def product_path(ep_dir: Path | str, ep_id: str, product_key: str,
                 prefix_chs: str, prefix_cht: str | None = None) -> Path:
    """
    根据命名模板生成产物完整路径。

    Parameters
    ----------
    ep_dir : 集数目录
    ep_id : 集数编号，如 "01"
    product_key : 'mp4_chs' | 'mp4_cht' | 'mkv_hevc'
    prefix_chs : 简体中文前缀，如 "[Billion Meta Lab] 作品名"
    prefix_cht : 繁体中文前缀，默认同 prefix_chs

    Returns
    -------
    Path — 无论文件是否存在都会返回路径

    Examples
    --------
    >>> product_path('.', '01', 'mp4_chs', PREFIX_CHS, PREFIX_CHT)
    PosixPath('01/[Billion Meta Lab] ... [01][1080P][简日内嵌].mp4')
    """
    if prefix_cht is None:
        prefix_cht = prefix_chs

    fmt = PRODUCT_FORMATS[product_key]
    prefix = prefix_cht if product_key in _CHT_KEYS else prefix_chs
    return Path(ep_dir) / fmt.format(prefix=prefix, ep_id=ep_id)


def product_torrent_path(video_path: Path | None) -> Path | None:
    """
    返回视频文件对应的 .torrent 种子路径（纯构造，不检查存在性）。

    Parameters
    ----------
    video_path : 视频文件路径或 None

    Returns
    -------
    Path | None — None 入参返回 None
    """
    if video_path is None:
        return None
    return video_path.with_suffix(video_path.suffix + '.torrent')


def check_products(ep_dir: Path | str, ep_id: str,
                   prefix_chs: str, prefix_cht: str | None = None) -> dict:
    """
    检查阶段 5/6 产物是否存在（只看文件在不在，不扫描目录）。

    Parameters
    ----------
    ep_dir, ep_id, prefix_chs, prefix_cht : 同 product_path()

    Returns
    -------
    dict:
        mp4_chs   — Path | None
        mp4_cht   — Path | None
        mkv_hevc  — Path | None
        all       — list[Path]  仅存在的视频文件
    """
    result: dict = {}
    for key in PRODUCT_FORMATS:
        p = product_path(ep_dir, ep_id, key, prefix_chs, prefix_cht)
        result[key] = p if p.exists() else None

    result['all'] = [p for p in (result['mp4_chs'], result['mp4_cht'], result['mkv_hevc'])
                     if p is not None]
    return result


def scan_products(ep_dir: Path | str, ep_id: str,
                  prefix_chs: str, prefix_cht: str | None = None) -> dict:
    """
    扫描阶段 5/6 产物 + 对应种子，返回完整状态。

    比 check_products 多出 torrent 和汇总字段，适合需要一次性
    获取全部信息的场景（如阶段 7 种子生成、阶段 12 API 发布）。

    Returns
    -------
    dict:
        mp4_chs, mp4_cht, mkv_hevc          — Path | None
        mp4_chs_torrent, mp4_cht_torrent,
        mkv_hevc_torrent                     — Path | None
        all_videos, all_torrents             — list[Path]
    """
    products = check_products(ep_dir, ep_id, prefix_chs, prefix_cht)

    mp4_chs_t = product_torrent_path(products['mp4_chs'])
    mp4_cht_t = product_torrent_path(products['mp4_cht'])
    mkv_hevc_t = product_torrent_path(products['mkv_hevc'])

    # 过滤：只保留实际存在的种子
    def _exists(p):
        return p is not None and p.exists()

    return {
        'mp4_chs': products['mp4_chs'],
        'mp4_cht': products['mp4_cht'],
        'mkv_hevc': products['mkv_hevc'],
        'mp4_chs_torrent': mp4_chs_t if _exists(mp4_chs_t) else None,
        'mp4_cht_torrent': mp4_cht_t if _exists(mp4_cht_t) else None,
        'mkv_hevc_torrent': mkv_hevc_t if _exists(mkv_hevc_t) else None,
        'all_videos': products['all'],
        'all_torrents': [t for t in (mp4_chs_t, mp4_cht_t, mkv_hevc_t) if _exists(t)],
    }
