"""
素材提取 — 从 MKV 提取音轨和字幕轨
修复: 每条轨道使用唯一文件名，不再互相覆盖
新增: 智能字幕筛选（中/英/日优先）
"""

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ._backup import backup_if_exists


@dataclass
class SubtitleInfo:
    """字幕轨道信息（提取前）"""
    index: int
    language: str       # 'chi', 'eng', 'jpn'...
    title: str          # 轨道标题 (如 'Simplified', 'English')
    codec_name: str     # 'subrip', 'ass'...


@dataclass
class ExtractedTrack:
    """提取后的轨道信息"""
    index: int          # 原始流索引
    codec_type: str     # 'audio' | 'subtitle'
    language: str       # 语言代码: 'jpn', 'eng', 'chi'...
    title: str          # 轨道标题
    codec_name: str     # 编解码器名
    output_path: Path   # 提取后的文件路径


@dataclass
class PreferredSubs:
    """智能筛选后的字幕提取结果"""
    chi: list[ExtractedTrack] = field(default_factory=list)   # 中文字幕
    eng: list[ExtractedTrack] = field(default_factory=list)   # 英文字幕
    jpn: list[ExtractedTrack] = field(default_factory=list)   # 日文字幕
    other: list[ExtractedTrack] = field(default_factory=list) # 其他语言

    @property
    def total_count(self) -> int:
        return len(self.chi) + len(self.eng) + len(self.jpn) + len(self.other)

    @property
    def has_any(self) -> bool:
        return self.total_count > 0

    def all_tracks(self) -> list[ExtractedTrack]:
        return self.chi + self.eng + self.jpn + self.other

    def summary(self) -> str:
        parts = []
        if self.chi: parts.append(f"中文 {len(self.chi)} 条")
        if self.eng: parts.append(f"英文 {len(self.eng)} 条")
        if self.jpn: parts.append(f"日文 {len(self.jpn)} 条")
        if self.other: parts.append(f"其他 {len(self.other)} 条 ({', '.join(t.language for t in self.other)})")
        return "; ".join(parts) if parts else "无字幕"


class MediaExtractor:
    """从 MKV 中提取音频和字幕轨道"""

    # 字幕语言优先级：中文 > 英文 > 日文
    CHI_LANGS = {"chi", "zh", "zho", "chs", "cht", "zh-cn", "zh-tw", "zh-hans", "zh-hant"}
    ENG_LANGS = {"eng", "en", "en-us", "en-gb"}
    JPN_LANGS = {"jpn", "ja", "jp"}

    def __init__(self, work_dir: Path | str = "."):
        self.work_dir = Path(work_dir).expanduser().resolve()

    # ── 查找文件 ─────────────────────────────────

    def find_digit_mkvs(self) -> list[Path]:
        """找到当前目录下纯数字命名的 .mkv 文件（如 01.mkv），排除 HEVC 压制版"""
        all_mkv = list(self.work_dir.glob("*.mkv"))
        target = [p for p in all_mkv if re.match(r'^\d+$', p.stem)
                  and "_HEVC10bit" not in p.stem]
        print(f"🔍 找到 {len(target)} 个纯数字 MKV: {[p.name for p in target]}")
        return sorted(target)

    def find_all_mkvs(self) -> list[Path]:
        """找到当前目录下所有 .mkv 文件"""
        return sorted(self.work_dir.glob("*.mkv"))

    # ── 流信息探测 ──────────────────────────────

    def probe_streams(self, video_path: Path) -> list[dict]:
        """用 ffprobe 获取视频流信息"""
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout).get("streams", [])

    def list_subtitle_streams(self, video_path: Path) -> list[SubtitleInfo]:
        """列出所有字幕轨道（不提取），供筛选决策用"""
        streams = self.probe_streams(video_path)
        subs: list[SubtitleInfo] = []
        for s in streams:
            if s.get("codec_type") != "subtitle":
                continue
            tags = s.get("tags", {})
            subs.append(SubtitleInfo(
                index=s.get("index", 0),
                language=tags.get("language", "und"),
                title=tags.get("title", ""),
                codec_name=s.get("codec_name", "unknown"),
            ))
        return subs

    # ── 音轨提取 ─────────────────────────────────

    def extract_audio_tracks(self, video_path: Path, progress=None,
                             output_stem: str | None = None) -> list[ExtractedTrack]:
        """
        提取所有音轨，每个轨道使用唯一文件名:
        {stem}_audio_{lang}_{index}.aac

        Parameters
        ----------
        progress : 可选，tqdm 兼容对象（有 .update(n) 方法），用于显示进度
        output_stem : 输出文件名前缀；不传时默认使用 video_path.stem
        """
        video_path = Path(video_path)
        streams = self._get_non_attachment_streams(video_path)
        extracted: list[ExtractedTrack] = []

        for s in streams:
            if s.get("codec_type") != "audio":
                continue

            track = self._stream_to_track(video_path, s, "audio", output_stem=output_stem)
            if track.output_path.exists():
                print(f"  ⏭️  跳过已存在: {track.output_path.name}")
                extracted.append(track)
                if progress:
                    progress.update(1)
                continue

            print(f"  >> 提取音轨 {track.index} ({track.language}) → {track.output_path.name}")
            subprocess.run([
                "ffmpeg", "-y", "-i", str(video_path),
                "-map", f"0:{track.index}",
                "-c:a", "aac", "-b:a", "192k",
                str(track.output_path)
            ], check=True, capture_output=True, timeout=600)
            extracted.append(track)
            if progress:
                progress.update(1)

        return extracted

    # ── 字幕提取（全量） ─────────────────────────

    def extract_subtitle_tracks(self, video_path: Path,
                                output_stem: str | None = None) -> list[ExtractedTrack]:
        """
        提取所有字幕轨，转为 ASS 格式:
        {stem}_sub_{lang}_{index}.ass
        """
        video_path = Path(video_path)
        streams = self._get_non_attachment_streams(video_path)
        extracted: list[ExtractedTrack] = []

        for s in streams:
            if s.get("codec_type") != "subtitle":
                continue

            track = self._stream_to_track(video_path, s, "subtitle", output_stem=output_stem)
            if track.output_path.exists():
                print(f"  ⏭️  跳过已存在: {track.output_path.name}")
                extracted.append(track)
                continue

            print(f"  >> 提取字幕 {track.index} ({track.language}) → {track.output_path.name}")
            subprocess.run([
                "ffmpeg", "-y", "-i", str(video_path),
                "-map", f"0:{track.index}",
                "-c:s", "ass",
                str(track.output_path)
            ], check=True, capture_output=True, timeout=300)
            extracted.append(track)

        return extracted

    # ── 智能字幕提取（中/英/日优先） ─────────────

    def extract_preferred_subtitles(self, video_path: Path,
                                     langs: list[str] | None = None,
                                     output_stem: str | None = None
                                     ) -> PreferredSubs | None:
        """
        智能字幕筛选提取：
        1. 先列出所有字幕流
        2. 按优先级提取：中文 > 英文 > 日文 > 其他
        3. 如果只有一种语言，只提取该语言
        4. 如果没有字幕，返回 None

        Parameters
        ----------
        video_path : 视频文件路径
        langs : 自定义语言优先级列表，默认 ["chi", "eng", "jpn"]
        output_stem : 输出文件名前缀；不传时默认使用 video_path.stem

        Returns
        -------
        PreferredSubs | None — None 表示无字幕
        """
        if langs is None:
            langs = ["chi", "eng", "jpn"]

        video_path = Path(video_path)
        all_sub_info = self.list_subtitle_streams(video_path)

        if not all_sub_info:
            print(f"⚠️ [{video_path.stem}] 没有任何字幕轨道")
            return None

        print(f"📋 [{video_path.stem}] 检测到 {len(all_sub_info)} 条字幕轨道:")
        for si in all_sub_info:
            print(f"    [{si.index}] {si.language} {si.title} ({si.codec_name})")

        # 按语言分组
        buckets: dict[str, list[SubtitleInfo]] = {"chi": [], "eng": [], "jpn": [], "other": []}
        for si in all_sub_info:
            cat = self._classify_lang(si.language)
            buckets[cat].append(si)

        # 统计有内容的语言种类
        active = {k: v for k, v in buckets.items() if v}
        print(f"  分类: {', '.join(f'{k}({len(v)}条)' for k, v in active.items())}")

        # 如果只有一种语言分类，提取全部
        non_empty = [k for k in langs if buckets.get(k)]
        if len(non_empty) <= 1 and not buckets["other"]:
            # 只有一种语言 or 完全没有优先级语言
            print(f"  仅一种语言，提取全部 {len(all_sub_info)} 条字幕")
            extracted = self.extract_subtitle_tracks(video_path, output_stem=output_stem)
            return self._bundle_preferred(extracted, all_sub_info)

        # 多种语言：按优先级提取
        result = PreferredSubs()
        streams = self._get_streams_dict(video_path)
        for si in all_sub_info:
            cat = self._classify_lang(si.language)
            track = self._extract_single_sub(video_path, streams[si.index], output_stem=output_stem)
            if cat == "chi":
                result.chi.append(track)
            elif cat == "eng":
                result.eng.append(track)
            elif cat == "jpn":
                result.jpn.append(track)
            else:
                result.other.append(track)

        print(f"  ✅ 提取结果: {result.summary()}")
        return result

    # ── 一键提取 ─────────────────────────────────

    def extract_all(self, video_path: Path,
                    output_stem: str | None = None) -> tuple[list[ExtractedTrack], list[ExtractedTrack]]:
        """返回 (音频列表, 字幕列表)"""
        audio = self.extract_audio_tracks(video_path, output_stem=output_stem)
        subs = self.extract_subtitle_tracks(video_path, output_stem=output_stem)
        return audio, subs

    def extract_smart(self, video_path: Path,
                      output_stem: str | None = None) -> tuple[list[ExtractedTrack], PreferredSubs | None]:
        """返回 (音频列表, 智能筛选字幕结果)"""
        audio = self.extract_audio_tracks(video_path, output_stem=output_stem)
        subs = self.extract_preferred_subtitles(video_path, output_stem=output_stem)
        return audio, subs

    # ── 公共辅助 ─────────────────────────────────

    def get_audio_track(self, video_path: Path, index: int = 0,
                        output_stem: str | None = None) -> Path | None:
        """获取视频的第 N 条音轨路径（提取后），方便快速引用"""
        tracks = self.extract_audio_tracks(video_path, output_stem=output_stem)
        if index < len(tracks):
            return tracks[index].output_path
        return None

    @classmethod
    def is_chi(cls, lang: str) -> bool:
        return lang.lower() in cls.CHI_LANGS

    @classmethod
    def is_eng(cls, lang: str) -> bool:
        return lang.lower() in cls.ENG_LANGS

    @classmethod
    def is_jpn(cls, lang: str) -> bool:
        return lang.lower() in cls.JPN_LANGS

    # ── 内部辅助 ─────────────────────────────────

    def _get_non_attachment_streams(self, video_path: Path) -> list[dict]:
        return [s for s in self.probe_streams(video_path)
                if s.get("codec_type") in ("audio", "subtitle")]

    def _get_streams_dict(self, video_path: Path) -> dict[int, dict]:
        return {s["index"]: s for s in self.probe_streams(video_path)}

    def _classify_lang(self, lang: str) -> str:
        """分类语言代码 → 'chi' | 'eng' | 'jpn' | 'other'"""
        l = lang.lower()
        if l in self.CHI_LANGS:
            return "chi"
        if l in self.ENG_LANGS:
            return "eng"
        if l in self.JPN_LANGS:
            return "jpn"
        return "other"

    def _extract_single_sub(self, video_path: Path, stream: dict,
                            output_stem: str | None = None) -> ExtractedTrack:
        """提取单条字幕轨"""
        track = self._stream_to_track(video_path, stream, "subtitle", output_stem=output_stem)
        if track.output_path.exists():
            return track

        print(f"  >> 提取字幕 {track.index} ({track.language}) → {track.output_path.name}")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-map", f"0:{track.index}",
            "-c:s", "ass",
            str(track.output_path)
        ], check=True, capture_output=True, timeout=300)
        return track

    def _stream_to_track(self, video_path: Path, stream: dict, track_type: str,
                         output_stem: str | None = None) -> ExtractedTrack:
        tags = stream.get("tags", {})
        lang = tags.get("language", "und")
        title = tags.get("title", "")
        codec = stream.get("codec_name", "unknown")
        idx = stream.get("index", 0)

        if track_type == "audio":
            ext = ".aac"
            prefix = "audio"
        else:
            ext = ".ass"
            prefix = "sub"

        stem = output_stem or video_path.stem
        filename = f"{stem}_{prefix}_{lang}_{idx}{ext}"
        output_path = self.work_dir / filename

        return ExtractedTrack(
            index=idx,
            codec_type=track_type,
            language=lang,
            title=title,
            codec_name=codec,
            output_path=output_path,
        )

    @staticmethod
    def _bundle_preferred(extracted: list[ExtractedTrack],
                           info_list: list[SubtitleInfo]) -> PreferredSubs:
        """将已提取的轨道按语言归类"""
        result = PreferredSubs()
        info_map = {si.index: si for si in info_list}

        for t in extracted:
            si = info_map.get(t.index)
            lang = si.language if si else t.language
            cat = MediaExtractor._classify_lang_raw(lang)
            if cat == "chi":
                result.chi.append(t)
            elif cat == "eng":
                result.eng.append(t)
            elif cat == "jpn":
                result.jpn.append(t)
            else:
                result.other.append(t)
        return result

    @staticmethod
    def _classify_lang_raw(lang: str) -> str:
        l = lang.lower()
        if l in MediaExtractor.CHI_LANGS: return "chi"
        if l in MediaExtractor.ENG_LANGS: return "eng"
        if l in MediaExtractor.JPN_LANGS: return "jpn"
        return "other"
