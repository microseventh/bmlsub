"""
备份工具 — 模块生成文件前自动备份已存在的文件

用法:
    from bmlsub._backup import backup_if_exists

    output = Path("01_HEVC10bit.mkv")
    backup_if_exists(output)  # 如果已存在 → 移到 _backup/ 目录
    # 然后安全生成新文件...

备份目录结构:
    项目目录/
    ├── 01.mkv
    ├── 01_HEVC10bit.mkv
    └── _backup/
        └── 01_HEVC10bit_20260710_143000.mkv   ← 带时间戳
"""

import shutil
from datetime import datetime
from pathlib import Path


def _make_backup_dir(parent_dir: Path) -> Path:
    """确保 _backup/ 目录存在，返回其 Path"""
    backup_dir = parent_dir / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_if_exists(file_path: Path, suffix: str | None = None) -> Path | None:
    """
    如果 file_path 存在，将其移到 _backup/ 目录（带时间戳）

    Parameters
    ----------
    file_path : 要备份的文件路径
    suffix : 自定义时间戳后缀，None = 自动生成 (YYYYMMDD_HHMMSS)

    Returns
    -------
    备份后的路径，如果源文件不存在则返回 None
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return None

    ts = suffix or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = _make_backup_dir(file_path.parent)
    backup_name = f"{file_path.stem}_{ts}{file_path.suffix}"
    backup_path = backup_dir / backup_name

    shutil.move(str(file_path), str(backup_path))
    return backup_path


def backup_path_if_exists(file_path: Path) -> Path | None:
    """
    检查文件是否存在，如果存在则备份。返回备份路径以便打印日志。
    等同于 backup_if_exists，但总是传递 None suffix。
    """
    return backup_if_exists(file_path)
