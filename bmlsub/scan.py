"""
产物命名与检测
"""

from pathlib import Path

from .config import PRODUCT_FORMATS, PipelineConfig


def product_path(ep_dir: Path | str, ep_id: str, product_key: str,
                 prefix_chs: str, prefix_cht: str | None = None,
                 config: PipelineConfig | None = None) -> Path:
    config = config or PipelineConfig()
    prefix_cht = prefix_cht or prefix_chs
    fmt = config.naming.formats[product_key]
    prefix = prefix_cht if product_key in config.naming.cht_keys else prefix_chs
    return Path(ep_dir) / fmt.format(prefix=prefix, ep_id=ep_id)


def product_torrent_path(video_path: Path | None) -> Path | None:
    if video_path is None:
        return None
    return video_path.with_suffix(video_path.suffix + '.torrent')


def check_products(ep_dir: Path | str, ep_id: str,
                   prefix_chs: str, prefix_cht: str | None = None,
                   config: PipelineConfig | None = None) -> dict:
    config = config or PipelineConfig()
    result: dict = {}
    for key in config.naming.formats:
        p = product_path(ep_dir, ep_id, key, prefix_chs, prefix_cht, config=config)
        result[key] = p if p.exists() else None
    result['all'] = [p for p in (result['mp4_chs'], result['mp4_cht'], result['mkv_hevc']) if p is not None]
    return result


def scan_products(ep_dir: Path | str, ep_id: str,
                  prefix_chs: str, prefix_cht: str | None = None,
                  config: PipelineConfig | None = None) -> dict:
    products = check_products(ep_dir, ep_id, prefix_chs, prefix_cht, config=config)
    mp4_chs_t = product_torrent_path(products['mp4_chs'])
    mp4_cht_t = product_torrent_path(products['mp4_cht'])
    mkv_hevc_t = product_torrent_path(products['mkv_hevc'])

    def _exists(p: Path | None) -> bool:
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
