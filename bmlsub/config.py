"""
BML 流水线统一配置
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EncodePreset:
    """压制预设"""
    codec: str = "hevc_videotoolbox"  # 'hevc_videotoolbox' | 'libx264'
    preset: str = "slow"              # x264 preset: 'slow', 'medium', 'veryslow'
    crf: int | None = None            # None → VideoToolbox 用 -q:v；libx264 默认 22
    quality: int = 60                 # VideoToolbox 质量 (0-100)
    pixel_fmt: str = "p010le"         # 'p010le' (VT 10bit) | 'yuv420p' (x264)
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    extra_params: list[str] = field(default_factory=list)

    def to_ffmpeg_video_params(self) -> list[str]:
        """生成 ffmpeg 视频编码参数列表"""
        if self.codec == "hevc_videotoolbox":
            params = [
                "-c:v", "hevc_videotoolbox",
                "-allow_sw", "1",
                "-profile:v", "main10",
                "-pix_fmt", self.pixel_fmt,
                "-q:v", str(self.quality),
            ]
            if self.extra_params:
                params += self.extra_params
            return params
        else:
            params = [
                "-c:v", self.codec,
                "-preset", self.preset,
                "-pix_fmt", self.pixel_fmt,
            ]
            if self.crf is not None:
                params += ["-crf", str(self.crf)]
            if self.extra_params:
                params += self.extra_params
            return params

    def to_ffmpeg_audio_params(self) -> list[str]:
        """生成 ffmpeg 音频编码参数列表"""
        return ["-c:a", self.audio_codec, "-b:a", self.audio_bitrate]


@dataclass
class SubtitleStandard:
    """字幕规范"""
    play_res_x: int = 1920
    play_res_y: int = 1080
    color_matrix: str = "TV.709"
    script_type: str = "v4.00+"
    wrap_style: int = 0
    scaled_border_and_shadow: str = "yes"

    @property
    def expected_header(self) -> dict[str, str]:
        """返回标准 ASS 头部的键值对"""
        return {
            "PlayResX": str(self.play_res_x),
            "PlayResY": str(self.play_res_y),
            "YCbCr Matrix": self.color_matrix,
            "ScriptType": self.script_type,
            "WrapStyle": str(self.wrap_style),
            "ScaledBorderAndShadow": self.scaled_border_and_shadow,
        }


@dataclass
class PipelineConfig:
    """流水线总配置"""
    work_dir: Path = field(default_factory=lambda: Path(".").resolve())

    # Whisper 模型
    whisper_fast_model: str = "mlx-community/whisper-large-v3-turbo"
    whisper_detailed_model: str = "mlx-community/whisper-medium-mlx"
    language: str = "ja"

    # 编码预设
    hevc_preset: EncodePreset = field(default_factory=EncodePreset)
    x264_preset: EncodePreset = field(default_factory=lambda: EncodePreset(
        codec="libx264",
        preset="slow",
        crf=22,
        pixel_fmt="yuv420p",
        extra_params=[
            "-tune", "film",
            "-refs", "6", "-bf", "6",
            "-qcomp", "0.7", "-rc-lookahead", "70",
            "-aq-mode", "3", "-aq-strength", "0.8",
            "-x264-params",
            "bframes=8:ref=6:aq-mode=3:aq-strength=0.8:"
            "deblock=-1,-1:merange=57:no-mbtree=1",
        ]
    ))

    # 字幕规范
    sub_standard: SubtitleStandard = field(default_factory=SubtitleStandard)

    # 音频切片
    chunk_sec: int = 240
    overlap_sec: int = 5

    # 输出目录
    output_transcripts_dir: Path = field(default_factory=lambda: Path("./output_transcripts"))


# 常用预设
PRESET_HEVC_VT_DEFAULT = EncodePreset()
PRESET_X264_SLOW = EncodePreset(
    codec="libx264", preset="slow", crf=22, pixel_fmt="yuv420p",
    extra_params=["-tune", "film", "-refs", "6", "-bf", "6",
                  "-x264-params", "bframes=8:ref=6:aq-mode=3:aq-strength=0.8:deblock=-1,-1:no-mbtree=1"]
)
PRESET_X264_VERYSLOW = EncodePreset(
    codec="libx264", preset="veryslow", crf=22, pixel_fmt="yuv420p",
    extra_params=["-tune", "film"]
)
SUB_STANDARD_HD = SubtitleStandard()
