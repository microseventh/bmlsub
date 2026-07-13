"""
BML v2 流水线统一配置
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EncodePreset:
    """压制预设"""

    codec: str = "hevc_videotoolbox"
    preset: str = "slow"
    crf: int | None = None
    quality: int = 60
    pixel_fmt: str = "p010le"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    extra_params: list[str] = field(default_factory=list)

    def to_ffmpeg_video_params(self) -> list[str]:
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
        return {
            "PlayResX": str(self.play_res_x),
            "PlayResY": str(self.play_res_y),
            "YCbCr Matrix": self.color_matrix,
            "ScriptType": self.script_type,
            "WrapStyle": str(self.wrap_style),
            "ScaledBorderAndShadow": self.scaled_border_and_shadow,
        }


@dataclass
class SubtitleConversionConfig:
    """简繁字幕转换配置。"""

    api_url: str = "https://api.zhconvert.org/convert"
    converter: str = "Taiwan"
    timeout: int = 60
    backup_dir_name: str = "_backup"
    regenerate_existing_cht: bool = True


@dataclass
class ProductNaming:
    """最终产物命名模板。{prefix} 与 {ep_id} 由调用方填充。"""

    formats: dict[str, str] = field(default_factory=lambda: {
        "mp4_chs": "{prefix} [{ep_id}][1080P][简日内嵌].mp4",
        "mp4_cht": "{prefix} [{ep_id}][1080P][繁日內嵌].mp4",
        "mkv_hevc": "{prefix} [{ep_id}][1080P][HEVC-10bit][简繁日内封].mkv",
    })
    cht_keys: set[str] = field(default_factory=lambda: {"mp4_cht"})


@dataclass
class LanguageStrategy:
    """字幕语言优先级与别名。"""

    preferred: list[str] = field(default_factory=lambda: ["chi", "eng", "jpn"])
    aliases: dict[str, set[str]] = field(default_factory=lambda: {
        "chi": {"chi", "zh", "zho", "chs", "cht", "zh-cn", "zh-tw", "zh-hans", "zh-hant"},
        "eng": {"eng", "en", "en-us", "en-gb"},
        "jpn": {"jpn", "ja", "jp"},
    })

    def classify(self, lang: str) -> str:
        value = (lang or "und").lower()
        for canonical, aliases in self.aliases.items():
            if value in aliases:
                return canonical
        return "other"


@dataclass
class TrackMetaConfig:
    """封装字幕轨道元数据。"""

    names: dict[str, str] = field(default_factory=lambda: {
        "chs": "简体中文+日语",
        "cht": "繁體中文+日语",
    })
    defaults: dict[str, str] = field(default_factory=lambda: {
        "chs": "yes",
        "cht": "no",
    })
    languages: dict[str, str] = field(default_factory=lambda: {
        "chs": "chi",
        "cht": "chi",
    })


def _compose_prefix(group: str, title: str, romaji: str) -> str:
    parts = [f"[{group}]" if group else "", title.strip(), romaji.strip()]
    return " ".join(part for part in parts if part).strip()


@dataclass
class ProjectNaming:
    """项目命名配置。"""

    group: str = "Billion Meta Lab"
    name_chs: str = "作品名"
    name_cht: str = "作品名"
    romaji: str = "Romaji"

    @property
    def prefix_chs(self) -> str:
        return _compose_prefix(self.group, self.name_chs, self.romaji)

    @property
    def prefix_cht(self) -> str:
        return _compose_prefix(self.group, self.name_cht, self.romaji)

@dataclass
class WorkstationConfig:
    """合集模式 notebook/workstation 的项目级配置。"""

    root_dir: Path = field(default_factory=lambda: Path(".").resolve())
    episode_ids: list[str] | str = field(default_factory=list)
    group: str = "Billion Meta Lab"
    name_chs: str = "作品名"
    name_cht: str = "作品名"
    romaji: str = "Romaji"
    raw_dir_name: str = "RAW"
    sub_dir_name: str = "CHS&JPN"
    sub_tj_dir_name: str = "CHT&JPN"
    hevc_label: str = "[1080P][HEVC-10bit][简繁日外挂]"
    chs_label: str = "[1080P][简日内嵌]"
    cht_label: str = "[1080P][繁日內嵌]"
    hevc_subdir_name: str = "HEVC-10Bit"
    r2_prefix: str = ""
    bgm_id: int | None = None
    notes: str = ""
    naming: ProductNaming = field(default_factory=ProductNaming)

    def __post_init__(self) -> None:
        self.root_dir = Path(self.root_dir).expanduser().resolve()
        self.episode_ids = parse_episode_ids(self.episode_ids)

    @property
    def prefix_chs(self) -> str:
        return _compose_prefix(self.group, self.name_chs, self.romaji)

    @property
    def prefix_cht(self) -> str:
        return _compose_prefix(self.group, self.name_cht, self.romaji)

    @property
    def effective_episode_ids(self) -> list[str]:
        if self.episode_ids:
            return list(self.episode_ids)
        return self.infer_episode_ids()

    @property
    def ep_range(self) -> str:
        episode_ids = self.effective_episode_ids
        if not episode_ids:
            return ""
        if len(episode_ids) == 1:
            return episode_ids[0]
        return f"{episode_ids[0]}-{episode_ids[-1]}"

    @property
    def raw_dir(self) -> Path:
        return self.root_dir / self.raw_dir_name

    @property
    def sub_dir(self) -> Path:
        return self.root_dir / self.sub_dir_name

    @property
    def sub_tj_dir(self) -> Path:
        return self.root_dir / self.sub_tj_dir_name

    @property
    def hevc_pack_dir(self) -> Path:
        suffix = f" [{self.ep_range}]" if self.ep_range else ""
        return self.root_dir / f"{self.prefix_chs}{suffix}{self.hevc_label}"

    @property
    def hevc_sub_dir(self) -> Path:
        return self.hevc_pack_dir / self.hevc_subdir_name

    @property
    def chs_pack_dir(self) -> Path:
        suffix = f" [{self.ep_range}]" if self.ep_range else ""
        return self.root_dir / f"{self.prefix_chs}{suffix}{self.chs_label}"

    @property
    def cht_pack_dir(self) -> Path:
        suffix = f" [{self.ep_range}]" if self.ep_range else ""
        return self.root_dir / f"{self.prefix_cht}{suffix}{self.cht_label}"

    def infer_episode_ids(self) -> list[str]:
        if not self.raw_dir.exists():
            return []
        results: list[str] = []
        for path in sorted(self.raw_dir.glob("*.mkv")):
            stem = path.stem
            if stem.isdigit() and "_HEVC10bit" not in stem:
                results.append(stem)
        return results

    def source_video(self, ep_id: str) -> Path:
        return self.raw_dir / f"{ep_id}.mkv"

    def hevc_raw_video(self, ep_id: str) -> Path:
        return self.hevc_sub_dir / f"{self.prefix_chs} [{ep_id}][1080P][HEVC-10bit][RAW].mkv"

    def resolve_chs_sub(self, ep_id: str) -> Path | None:
        candidates = [
            self.sub_dir / f"{ep_id}.chs&jpn.ass",
            self.sub_dir / f"{ep_id}v2.chs&jpn.ass",
            self.sub_dir / f"{ep_id}.chs.ass",
            self.sub_dir / f"{ep_id}.chs&ja.ass",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def resolve_cht_sub(self, ep_id: str) -> Path | None:
        candidates = [
            self.sub_tj_dir / f"{ep_id}.cht&jpn.ass",
            self.sub_tj_dir / f"{ep_id}v2.cht&jpn.ass",
            self.sub_tj_dir / f"{ep_id}.cht.ass",
            self.sub_tj_dir / f"{ep_id}.cht&ja.ass",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def hevc_path(self, ep_id: str) -> Path:
        return self.hevc_raw_video(ep_id)

    def x264_path(self, ep_id: str, kind: str) -> Path:
        if kind not in {"chs", "cht"}:
            raise ValueError("kind 必须是 chs 或 cht")
        prefix = self.prefix_chs if kind == "chs" else self.prefix_cht
        template = self.naming.formats["mp4_chs" if kind == "chs" else "mp4_cht"]
        base_dir = self.chs_pack_dir if kind == "chs" else self.cht_pack_dir
        return base_dir / template.format(prefix=prefix, ep_id=ep_id)

    def release_pack_dir(self, kind: str) -> Path:
        mapping = {
            "hevc": self.hevc_pack_dir,
            "chs": self.chs_pack_dir,
            "cht": self.cht_pack_dir,
        }
        try:
            return mapping[kind]
        except KeyError as exc:
            raise ValueError("kind 必须是 hevc / chs / cht") from exc

    def release_torrent_path(self, kind: str) -> Path:
        pack_dir = self.release_pack_dir(kind)
        return self.root_dir / f"{pack_dir.name}.torrent"

    def stage0_checks(self) -> list[dict]:
        checks: list[dict] = []
        for ep_id in self.effective_episode_ids:
            source = self.source_video(ep_id)
            chs_sub = self.resolve_chs_sub(ep_id)
            cht_sub = self.resolve_cht_sub(ep_id)
            missing: list[str] = []
            if not source.exists():
                missing.append("source")
            if chs_sub is None:
                missing.append("chs_sub")
            if cht_sub is None:
                missing.append("cht_sub")
            checks.append({
                "episode_id": ep_id,
                "source": str(source),
                "source_exists": source.exists(),
                "chs_sub": str(chs_sub) if chs_sub else None,
                "chs_sub_exists": chs_sub is not None,
                "cht_sub": str(cht_sub) if cht_sub else None,
                "cht_sub_exists": cht_sub is not None,
                "missing": missing,
                "hevc_output": str(self.hevc_path(ep_id)),
                "mp4_chs_output": str(self.x264_path(ep_id, "chs")),
                "mp4_cht_output": str(self.x264_path(ep_id, "cht")),
            })
        return checks

    def missing_summary(self) -> dict[str, list[str]]:
        summary = {"source": [], "chs_sub": [], "cht_sub": []}
        for item in self.stage0_checks():
            for key in summary:
                if key in item["missing"]:
                    summary[key].append(item["episode_id"])
        return summary

    def sample_outputs(self) -> dict[str, str | None]:
        episode_ids = self.effective_episode_ids
        if not episode_ids:
            return {
                "sample_episode_id": None,
                "hevc": None,
                "mp4_chs": None,
                "mp4_cht": None,
            }
        sample_ep = episode_ids[0]
        return {
            "sample_episode_id": sample_ep,
            "hevc": str(self.hevc_path(sample_ep)),
            "mp4_chs": str(self.x264_path(sample_ep, "chs")),
            "mp4_cht": str(self.x264_path(sample_ep, "cht")),
        }

    def summary(self) -> dict:
        return {
            "root_dir": str(self.root_dir),
            "episode_ids": list(self.effective_episode_ids),
            "ep_range": self.ep_range,
            "group": self.group,
            "name_chs": self.name_chs,
            "name_cht": self.name_cht,
            "romaji": self.romaji,
            "prefix_chs": self.prefix_chs,
            "prefix_cht": self.prefix_cht,
            "raw_dir": str(self.raw_dir),
            "sub_dir": str(self.sub_dir),
            "sub_tj_dir": str(self.sub_tj_dir),
            "hevc_pack_dir": str(self.hevc_pack_dir),
            "hevc_sub_dir": str(self.hevc_sub_dir),
            "chs_pack_dir": str(self.chs_pack_dir),
            "cht_pack_dir": str(self.cht_pack_dir),
            "release_torrents": {
                "hevc": str(self.release_torrent_path("hevc")),
                "chs": str(self.release_torrent_path("chs")),
                "cht": str(self.release_torrent_path("cht")),
            },
            "r2_prefix": self.r2_prefix,
            "bgm_id": self.bgm_id,
            "notes": self.notes,
            "sample_outputs": self.sample_outputs(),
            "missing_summary": self.missing_summary(),
            "checks": self.stage0_checks(),
        }


@dataclass
class PipelineConfig:
    """流水线总配置"""

    work_dir: Path = field(default_factory=lambda: Path(".").resolve())
    whisper_fast_model: str = "mlx-community/whisper-large-v3-turbo"
    whisper_detailed_model: str = "mlx-community/whisper-medium-mlx"
    language: str = "ja"
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
            "bframes=8:ref=6:aq-mode=3:aq-strength=0.8:deblock=-1,-1:merange=57:no-mbtree=1",
        ],
    ))
    sub_standard: SubtitleStandard = field(default_factory=SubtitleStandard)
    subtitle_conversion: SubtitleConversionConfig = field(default_factory=SubtitleConversionConfig)
    chunk_sec: int = 240
    overlap_sec: int = 5
    output_transcripts_dir: Path = field(default_factory=lambda: Path("./output_transcripts"))
    naming: ProductNaming = field(default_factory=ProductNaming)
    subtitle_strategy: LanguageStrategy = field(default_factory=LanguageStrategy)
    track_meta: TrackMetaConfig = field(default_factory=TrackMetaConfig)
    project: ProjectNaming = field(default_factory=ProjectNaming)

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir).expanduser().resolve()
        self.output_transcripts_dir = Path(self.output_transcripts_dir).expanduser().resolve()


PRESET_HEVC_VT_DEFAULT = EncodePreset()
PRESET_X264_SLOW = EncodePreset(
    codec="libx264",
    preset="slow",
    crf=22,
    pixel_fmt="yuv420p",
    extra_params=[
        "-tune", "film", "-refs", "6", "-bf", "6",
        "-x264-params", "bframes=8:ref=6:aq-mode=3:aq-strength=0.8:deblock=-1,-1:no-mbtree=1",
    ],
)
PRESET_X264_VERYSLOW = EncodePreset(
    codec="libx264",
    preset="veryslow",
    crf=22,
    pixel_fmt="yuv420p",
    extra_params=["-tune", "film"],
)
SUB_STANDARD_HD = SubtitleStandard()
PRODUCT_FORMATS = ProductNaming().formats


def parse_episode_ids(value: str | list[str] | None) -> list[str]:
    """解析 notebook 风格的批量集数参数。"""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = value.strip()
    if not text:
        return []

    parts = [part.strip() for part in text.split(",") if part.strip()]
    result: list[str] = []
    for part in parts:
        if "-" in part and all(piece.strip().isdigit() for piece in part.split("-", 1)):
            start_text, end_text = [piece.strip() for piece in part.split("-", 1)]
            start = int(start_text)
            end = int(end_text)
            width = max(len(start_text), len(end_text))
            step = 1 if end >= start else -1
            for number in range(start, end + step, step):
                result.append(f"{number:0{width}d}")
        else:
            result.append(part)
    return result
