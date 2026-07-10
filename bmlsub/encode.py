"""
编码器 — VideoToolbox HEVC 硬压 + x264 软编码 + 元数据清理

核心修复:
- 编码时用 -map_metadata -1 -fflags +bitexact 最小化元数据
- 编码后用 mkvpropedit --delete-track-statistics-tags 彻底清理
"""

import shutil
import subprocess
import time
from pathlib import Path

from ._backup import backup_if_exists
from .config import EncodePreset, PRESET_HEVC_VT_DEFAULT, PRESET_X264_SLOW


class Encoder:
    """视频编码器"""

    def __init__(self, hevc_preset: EncodePreset | None = None,
                 x264_preset: EncodePreset | None = None):
        self.hevc_preset = hevc_preset or PRESET_HEVC_VT_DEFAULT
        self.x264_preset = x264_preset or PRESET_X264_SLOW

    # ── HEVC VideoToolbox 硬压 ───────────────────

    def encode_hevc_vt(self, src: Path, dst: Path | None = None,
                        audio_streams: list[int] | None = None,
                        strip_metadata: bool = True) -> Path:
        """
        Mac VideoToolbox HEVC 10bit 硬件编码

        Parameters
        ----------
        src : 源视频路径
        dst : 输出路径，默认 {src.stem}_HEVC10bit.mkv
        audio_streams : 要包含的音轨流索引，None=全部
        strip_metadata : 是否在编码后清理元数据
        """
        src = Path(src)
        if dst is None:
            dst = src.parent / f"{src.stem}_HEVC10bit.mkv"
        dst = Path(dst)

        if dst.exists():
            bak = backup_if_exists(dst)
            if bak:
                print(f"📦 已备份旧文件 → {bak.name}")

        print(f"🚀 VideoToolbox HEVC 压制: {src.name}")

        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-map", "0:v:0",
        ]

        # 音频映射
        if audio_streams:
            for a in audio_streams:
                cmd += ["-map", f"0:{a}"]
        else:
            cmd += ["-map", "0:a?"]

        # 视频编码参数
        cmd += self.hevc_preset.to_ffmpeg_video_params()

        # 音频编码
        cmd += self.hevc_preset.to_ffmpeg_audio_params()

        # 元数据最小化
        if strip_metadata:
            cmd += ["-map_metadata", "-1",
                    "-fflags", "+bitexact",
                    "-flags:v", "+bitexact"]

        cmd.append(str(dst))

        start = time.time()
        subprocess.run(cmd, check=True, timeout=7200)
        elapsed = (time.time() - start) / 60
        print(f"✅ 压制完成 ({elapsed:.1f} min) → {dst.name}")

        # 编码后深度清理
        if strip_metadata:
            self.strip_metadata(dst)

        return dst

    # ── x264 软编码 + ASS 硬字幕 ──────────────────

    def encode_x264(self, src: Path, dst: Path,
                     ass_subtitle: Path | None = None,
                     preset: EncodePreset | None = None) -> Path:
        """
        x264 软件编码，可选烧录 ASS 字幕

        Parameters
        ----------
        src : 源视频
        dst : 输出 .mp4 路径
        ass_subtitle : ASS 字幕文件路径，None = 不烧录
        preset : 编码预设
        """
        src, dst = Path(src), Path(dst)
        p = preset or self.x264_preset

        if dst.exists():
            bak = backup_if_exists(dst)
            if bak:
                print(f"📦 已备份旧文件 → {bak.name}")

        print(f"🎬 x264 压制: {src.name} → {dst.name}")

        cmd = ["ffmpeg", "-y", "-i", str(src)]

        # 字幕烧录
        if ass_subtitle and ass_subtitle.exists():
            sub_path = str(ass_subtitle.absolute()).replace("\\", "/")
            cmd += ["-vf", f"ass='{sub_path}'"]

        cmd += p.to_ffmpeg_video_params()
        cmd += p.to_ffmpeg_audio_params()
        cmd += ["-map_metadata", "-1", "-fflags", "+bitexact",
                "-flags:v", "+bitexact"]
        cmd.append(str(dst))

        start = time.time()
        subprocess.run(cmd, check=True, timeout=7200)
        print(f"✅ x264 压制完成 ({(time.time()-start)/60:.1f} min)")

        return dst

    # ── 元数据清理 ────────────────────────────────

    def strip_metadata(self, video_path: Path) -> Path:
        """
        使用 mkvpropedit 深度清理元数据标签:
        - 删除所有 _STATISTICS_* 标签
        - 清空全局标签

        如果 mkvpropedit 不可用，回退到 ffmpeg 流拷贝方式
        """
        video_path = Path(video_path)
        if not video_path.exists():
            print(f"⚠️ 文件不存在，跳过清理: {video_path}")
            return video_path

        if shutil.which("mkvpropedit"):
            try:
                # 注意: 不在此时备份 — encode_hevc_vt / encode_x264 已在编码前完成备份
                # mkvpropedit 原地修改元数据（不改视频流），无需重复备份
                # 先删除 track statistics tags
                subprocess.run([
                    "mkvpropedit", str(video_path),
                    "--delete-track-statistics-tags",
                ], check=True, capture_output=True, timeout=120)

                # 再清空全局标签
                subprocess.run([
                    "mkvpropedit", str(video_path),
                    "--tags", "all:",
                ], check=True, capture_output=True, timeout=120)

                print(f"🧹 元数据已清理: {video_path.name}")
                return video_path
            except subprocess.CalledProcessError as e:
                print(f"⚠️ mkvpropedit 清理失败: {e.stderr.decode() if e.stderr else e}")

        # 回退：ffmpeg 流拷贝
        tmp = video_path.with_suffix(".clean.mkv")
        try:
            subprocess.run([
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-map_metadata", "-1",
                "-fflags", "+bitexact",
                "-c", "copy",
                str(tmp),
            ], check=True, capture_output=True, timeout=600)
            try:
                tmp.replace(video_path)
            except OSError as e:
                print(f"⚠️ 替换原文件失败: {e}")
                if tmp.exists():
                    print(f"   清理文件保存在: {tmp}")
                raise
            print(f"🧹 元数据已清理 (ffmpeg): {video_path.name}")
        except subprocess.CalledProcessError as e:
            print(f"⚠️ ffmpeg 清理失败: {e.stderr.decode() if e.stderr else e}")
            if tmp.exists():
                tmp.unlink()

        return video_path

    def verify_metadata_clean(self, video_path: Path) -> dict:
        """
        检查视频元数据是否干净，返回残留的源标签报告

        Returns
        -------
        dict: 空 = 完全干净，不含任何源泄漏标签
        """
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        import json
        data = json.loads(result.stdout)

        issues: dict[str, list[str]] = {}

        # 仅检查源泄漏标签（不报告 ffmpeg/Lavf 自身的 encoder 等无害标签）
        SOURCE_LEAK_KEYS = (
            "_STATISTICS_WRITING_APP", "_STATISTICS_TAGS",
            "BPS", "DURATION", "NUMBER_OF_FRAMES", "NUMBER_OF_BYTES",
            "title", "HANDLER_NAME", "VENDOR_ID",
        )

        # 检查每个流的标签
        for s in data.get("streams", []):
            tags = s.get("tags", {})
            dirty = [k for k in tags if k.startswith("_STATISTICS")
                     or k in SOURCE_LEAK_KEYS]
            if dirty:
                issues[f"stream_{s.get('index')}_{s.get('codec_type','?')}"] = dirty

        # 检查全局标签中是否有源泄漏
        fmt_tags = data.get("format", {}).get("tags", {})
        fmt_dirty = [k for k in fmt_tags if k in SOURCE_LEAK_KEYS
                     or k.startswith("_STATISTICS")]
        if fmt_dirty:
            issues["format_tags"] = fmt_dirty

        return issues
