"""
进度与时间轴模块 — 进度条、网速显示、阶段计时

用法:
    from bmlsub.progress import ProgressBar, SpeedMeter, StageTimer, PipelineTimer

    # ── 文件上传进度条（带网速） ──
    bar = ProgressBar.file_upload("01_HEVC10bit.mkv", 500_000_000)  # 500 MB
    bar.update(100_000_000)  # 已上传 100 MB
    bar.close()

    # ── 上下文管理器 ──
    with ProgressBar.file_upload("01.mkv", total_size) as bar:
        for chunk in upload_chunks():
            bar.update(len(chunk))

    # ── 网速计 ──
    meter = SpeedMeter()
    meter.add_bytes(1_048_576)   # 累计 1MB
    print(meter.speed_str)        # "1.05 MB/s"

    # ── 阶段计时 ──
    timer = PipelineTimer()
    with timer.stage("HEVC 编码"):
        encode_video(...)
    with timer.stage("R2 上传"):
        upload_to_r2(...)
    timer.summary()  # 打印时间轴总览

依赖: tqdm（已在 transcribe.py 中使用）
"""

import time
from collections import deque
from contextlib import contextmanager
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 格式化工具
# ═══════════════════════════════════════════════════════════════

def _fmt_size(n_bytes: int | float) -> str:
    """字节数 → 人类可读 (B / KB / MB / GB)"""
    if n_bytes < 1024:
        return f"{n_bytes:.0f} B"
    elif n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f} KB"
    elif n_bytes < 1024 * 1024 * 1024:
        return f"{n_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{n_bytes / (1024 * 1024 * 1024):.2f} GB"


def _fmt_time(seconds: float) -> str:
    """秒数 → 人类可读 (1h23m45s / 3m12s / 45.3s)"""
    if seconds < 0:
        return "--"
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}h{m:02d}m{s:02d}s"
    elif seconds >= 60:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s:02d}s"
    else:
        return f"{seconds:.1f}s"


# ═══════════════════════════════════════════════════════════════
# SpeedMeter — 网速 / IO 速度计
# ═══════════════════════════════════════════════════════════════

class SpeedMeter:
    """滑动窗口速度计

    追踪字节传输速率，支持实时网速显示。

    Parameters
    ----------
    window_sec : 速度计算的滑动窗口（秒），默认 5 秒
    """

    def __init__(self, window_sec: float = 5.0):
        self._window = window_sec
        self._samples: deque[tuple[float, int]] = deque()
        self._total_bytes: int = 0
        self._start_time: float = time.time()

    def add_bytes(self, n: int) -> None:
        """记录新增字节数"""
        now = time.time()
        self._samples.append((now, n))
        self._total_bytes += n
        # 清理过期样本
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    @property
    def speed(self) -> float:
        """当前速度 (bytes/sec)"""
        if len(self._samples) < 2:
            return 0.0
        first_time = self._samples[0][0]
        last_time = self._samples[-1][0]
        elapsed = last_time - first_time
        if elapsed <= 0:
            return 0.0
        bytes_in_window = sum(b for _, b in self._samples)
        return bytes_in_window / elapsed

    @property
    def avg_speed(self) -> float:
        """全程平均速度 (bytes/sec)"""
        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0
        return self._total_bytes / elapsed

    @property
    def speed_str(self) -> str:
        """当前速度 → 人类可读字符串，如 '12.5 MB/s'"""
        return f"{_fmt_size(self.speed)}/s"

    @property
    def avg_speed_str(self) -> str:
        """平均速度 → 人类可读字符串"""
        return f"{_fmt_size(self.avg_speed)}/s"

    @property
    def total_str(self) -> str:
        """已传输总量 → 人类可读"""
        return _fmt_size(self._total_bytes)

    @property
    def elapsed(self) -> float:
        """已耗时（秒）"""
        return time.time() - self._start_time

    @property
    def elapsed_str(self) -> str:
        return _fmt_time(self.elapsed)

    def reset(self) -> None:
        """重置计数器"""
        self._samples.clear()
        self._total_bytes = 0
        self._start_time = time.time()


# ═══════════════════════════════════════════════════════════════
# ProgressBar — 通用进度条
# ═══════════════════════════════════════════════════════════════

class ProgressBar:
    """带速度和 ETA 的进度条

    封装 tqdm，自动显示：
    - 百分比进度
    - 当前速度 / 平均速度 (SpeedMeter)
    - 已用时间 / 预计剩余时间 (ETA)

    Usage::

        # 方式1: 手动
        bar = ProgressBar.file_upload("01.mkv", 500_000_000)
        bar.update(200_000_000)   # 已上传 200MB
        bar.close()

        # 方式2: 上下文管理器（推荐）
        with ProgressBar("Encoding", total=100, unit="frames") as bar:
            for i in range(100):
                process_frame(i)
                bar.update(1)
    """

    def __init__(self, label: str, total: int,
                 unit: str = "B",
                 show_speed: bool = True,
                 show_eta: bool = True,
                 bar_format: str | None = None,
                 **kwargs):
        """
        Parameters
        ----------
        label : 进度条标签（如 "📤 上传 01.mkv"）
        total : 总量（字节数 / 帧数 / 百分比）
        unit : 单位 ('B' = 自动换算, 'frames', 'items', '%')
        show_speed : 是否显示速度
        show_eta : 是否显示 ETA
        bar_format : 自定义 tqdm bar_format，None = 自动生成
        """
        from tqdm import tqdm

        self._label = label
        self._total = total
        self._unit = unit
        self._show_speed = show_speed
        self._meter = SpeedMeter() if show_speed else None
        self._start = time.time()

        if bar_format is None:
            bar_format = self._build_bar_format(show_speed, show_eta)

        self._bar: tqdm = tqdm(
            total=total,
            desc=label,
            unit=unit,
            unit_scale=(unit == "B"),
            unit_divisor=1024,
            bar_format=bar_format,
            **kwargs,
        )

    @classmethod
    def file_upload(cls, filename: str, total_bytes: int,
                    unit: str = "B") -> "ProgressBar":
        """创建文件上传进度条（默认显示速度和 ETA）"""
        return cls(f"📤 {filename}", total=total_bytes, unit=unit)

    @classmethod
    def file_download(cls, filename: str, total_bytes: int,
                      unit: str = "B") -> "ProgressBar":
        """创建文件下载进度条"""
        return cls(f"📥 {filename}", total=total_bytes, unit=unit)

    @classmethod
    def encode(cls, filename: str, duration_sec: float,
               unit: str = "frames") -> "ProgressBar":
        """创建编码进度条"""
        return cls(f"🎬 {filename}", total=int(duration_sec), unit="s")

    def update(self, n: int = 1) -> None:
        """增加进度"""
        self._bar.update(n)
        if self._meter:
            self._meter.add_bytes(n)

    def set_postfix(self, **kwargs) -> None:
        """设置额外显示信息"""
        self._bar.set_postfix(**kwargs, refresh=False)

    @property
    def speed_str(self) -> str:
        """当前速度"""
        if self._meter:
            return self._meter.speed_str
        return "--"

    @property
    def elapsed_str(self) -> str:
        """已用时间"""
        return _fmt_time(time.time() - self._start)

    def close(self) -> None:
        """关闭进度条"""
        self._bar.close()

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def _build_bar_format(self, show_speed: bool,
                          show_eta: bool) -> str:
        """自动构造 tqdm bar_format"""
        parts = ["{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"]
        if show_speed:
            parts.append("[{rate_fmt}]")
        if show_eta:
            parts.append("[{elapsed}<{remaining}]")
        return " ".join(parts)


# ═══════════════════════════════════════════════════════════════
# StageTimer — 单阶段计时器
# ═══════════════════════════════════════════════════════════════

class StageTimer:
    """单个阶段的计时器

    记录阶段名称、开始时间、结束时间、耗时
    """

    def __init__(self, name: str):
        self.name = name
        self.start_time: float | None = None
        self.end_time: float | None = None
        self._bytes_processed: int = 0

    @property
    def elapsed(self) -> float:
        if self.start_time is None:
            return 0.0
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def elapsed_str(self) -> str:
        return _fmt_time(self.elapsed)

    @property
    def is_done(self) -> bool:
        return self.end_time is not None

    def start(self) -> "StageTimer":
        self.start_time = time.time()
        return self

    def stop(self) -> "StageTimer":
        self.end_time = time.time()
        return self

    def add_bytes(self, n: int) -> None:
        self._bytes_processed += n

    @property
    def bytes_str(self) -> str:
        return _fmt_size(self._bytes_processed)


# ═══════════════════════════════════════════════════════════════
# PipelineTimer — 流水线时间轴
# ═══════════════════════════════════════════════════════════════

class PipelineTimer:
    """流水线阶段时间轴

    追踪每个阶段的耗时，最后打印时间轴总览。

    Usage::

        timer = PipelineTimer()

        with timer.stage("提取音轨"):
            extract_audio()

        with timer.stage("HEVC 编码"):
            encode_hevc()

        timer.summary()
        # ─────── 流水线时间轴 ───────
        #  1. 提取音轨       3.2s   [██░░░░]   5%
        #  2. HEVC 编码      58.4s  [██████]  92%
        #  3. R2 上传        1.8s   [█░░░░░]   3%
        # ────────────────────────────
        #      总计: 63.4s
    """

    def __init__(self, label: str = ""):
        self.label = label
        self.stages: list[StageTimer] = []
        self._start_time: float = time.time()
        self._current: StageTimer | None = None

    @contextmanager
    def stage(self, name: str):
        """上下文管理器：开始并自动结束一个阶段"""
        st = StageTimer(name)
        self.stages.append(st)
        st.start()
        self._current = st
        try:
            yield st
        finally:
            st.stop()
            self._current = None

    def stage_start(self, name: str) -> StageTimer:
        """手动开始阶段（需手动调用 .stop()）"""
        st = StageTimer(name)
        self.stages.append(st)
        st.start()
        self._current = st
        return st

    @property
    def total_elapsed(self) -> float:
        return time.time() - self._start_time

    def summary(self, width: int = 50) -> None:
        """打印时间轴总览"""
        if not self.stages:
            print("(无阶段记录)")
            return

        total = sum(s.elapsed for s in self.stages)
        if total == 0:
            return

        header = f"─── 流水线时间轴"
        if self.label:
            header += f" [{self.label}]"
        header += f" ───"

        print(f"\n{header}")
        max_name = max(len(s.name) for s in self.stages)

        for i, st in enumerate(self.stages, 1):
            pct = st.elapsed / total * 100
            bar_len = max(1, int(pct / 100 * (width - max_name - 20)))
            bar = "█" * bar_len
            status = "✅" if st.is_done else "⏳"
            print(f"  {i:2d}. {status} {st.name:<{max_name}s}  "
                  f"{st.elapsed_str:>8s}  [{bar:.<{bar_len}s}]  {pct:4.1f}%")

        print(f"  {'─' * (max_name + width - 5)}")
        print(f"      总计: {_fmt_time(total)}")
        print()
