"""
封装 — mkvmerge 封裝 + ffmpeg 硬压字幕

将 HEVC 视频 + 简繁 ASS 字幕 + 字体附件 → 最终发布 MKV
将纯数字 MKV + ASS 字幕 → 烧录硬字幕 MP4
"""

import subprocess
from pathlib import Path

from ._backup import backup_if_exists
from .config import PipelineConfig
from .subtitle import SubtitleValidator


class PackagingError(Exception):
    """封装异常"""
    pass


class Packager:
    """视频字幕封装器"""

    def __init__(self, episode_dir: Path | str, episode_id: str,
                 config: PipelineConfig | None = None):
        self.episode_dir = Path(episode_dir).expanduser().resolve()
        self.episode_id = episode_id
        self.config = config or PipelineConfig()
        self.validator = SubtitleValidator(self.config.sub_standard)

    # ── 文件扫描 ─────────────────────────────────

    # 字幕匹配模式（按优先级排序，* 会替换为 episode_id）
    SUB_PATTERNS = [
        # 双语字幕（简日 / 繁日）
        "{stem}.chs&jpn.ass",
        "{stem}.cht&jpn.ass",
        # 单语字幕
        "{stem}.chs.ass",
        "{stem}.cht.ass",
        "{stem}.chs&ja.ass",
        "{stem}.cht&ja.ass",
        # 提取的原始字幕（按语言优先级）
        "{stem}_sub_chi_*.ass",
        "{stem}_sub_eng_*.ass",
        "{stem}_sub_jpn_*.ass",
    ]

    def get_available_files(self) -> dict:
        """
        自动扫描目录，匹配所有可能的字幕文件

        匹配模式（按优先级）:
          {stem}.chs&jpn.ass  → chs_sub
          {stem}.cht&jpn.ass  → cht_sub
          {stem}.chs.ass      → chs_sub (fallback)
          {stem}.cht.ass      → cht_sub (fallback)
          {stem}_sub_chi_*.ass → chs_sub (extracted)
          {stem}_sub_eng_*.ass → eng_sub (extracted)
          {stem}_sub_jpn_*.ass → jpn_sub (extracted)

        Returns
        -------
        {
            'pure_mkv': Path | None,
            'hevc_mkv': Path | None,
            'chs_sub': Path | None,    # 简体中文字幕
            'cht_sub': Path | None,    # 繁体中文字幕
            'eng_sub': Path | None,    # 英文字幕
            'jpn_sub': Path | None,    # 日文字幕
            'all_subs': list[Path],    # 所有匹配到的字幕
            'fonts': list[Path],
        }
        """
        d = self.episode_dir
        eid = self.episode_id
        stem = eid  # 如 "01"

        result = {
            "pure_mkv": self._maybe(d / f"{eid}.mkv"),
            "hevc_mkv": self._maybe(d / f"{eid}_HEVC10bit.mkv"),
            "chs_sub": None,
            "cht_sub": None,
            "eng_sub": None,
            "jpn_sub": None,
            "all_subs": [],
            "fonts": self._find_fonts(d),
        }

        # 自动匹配字幕
        for pattern in self.SUB_PATTERNS:
            glob_pattern = pattern.replace("{stem}", stem)
            matches = sorted(d.glob(glob_pattern))
            for m in matches:
                if m not in result["all_subs"]:
                    result["all_subs"].append(m)

        # 分类填充
        for sub_path in result["all_subs"]:
            name = sub_path.name.lower()
            # 简体中文
            if "chs" in name and result["chs_sub"] is None:
                result["chs_sub"] = sub_path
            # 繁体中文
            if "cht" in name and result["cht_sub"] is None:
                result["cht_sub"] = sub_path
            # 英文
            if ("eng" in name or "_sub_eng_" in name) and result["eng_sub"] is None:
                result["eng_sub"] = sub_path
            # 日文
            if ("jpn" in name or "&jpn" in name or "_sub_jpn_" in name) and result["jpn_sub"] is None:
                result["jpn_sub"] = sub_path

        # 打印扫描结果
        sub_names = [p.name for p in result["all_subs"]]
        print(f"📋 字幕扫描 ({stem}): {sub_names if sub_names else '无匹配字幕'}")
        if not result["all_subs"]:
            print(f"⚠️  未找到任何匹配 {stem}.*.ass 的字幕文件！")

        return result

    # ── mkvmerge 封装 ────────────────────────────

    def mkvmerge_package(self, output_template: str) -> Path | None:
        """
        封装 HEVC 视频 + 字幕 + 字体 → 最终 MKV

        Parameters
        ----------
        output_template : 输出文件名模板，"&&" 替换为集数
                          e.g. "[BML] Series [&&][HEVC-10bit][CHS&CHT&JP].mkv"
        """
        files = self.get_available_files()

        hevc_video = files["hevc_mkv"]
        if not hevc_video:
            raise PackagingError("找不到 HEVC10bit 影片，无法封装")

        # 验证字幕存在
        valid_subs: list[Path] = []
        for key in ("chs_sub", "cht_sub"):
            sub = files[key]
            if sub:
                valid_subs.append(sub)
            else:
                sub_type = "chs" if "chs" in key else "cht"
                print(f"⚠️ 缺少 {key.split('_')[0]} 字幕: {self.episode_id}.{sub_type}&jpn.ass")

        if not valid_subs:
            raise PackagingError("无可用字幕文件")

        # 输出路径
        output_name = output_template.replace("&&", self.episode_id)
        output_path = self.episode_dir / output_name

        # 备份旧封装文件
        if output_path.exists():
            bak = backup_if_exists(output_path)
            if bak:
                print(f"📦 已备份旧 MKV → {bak.name}")

        cmd = ["mkvmerge", "-o", str(output_path), str(hevc_video)]

        # 添加字幕轨道
        for sub in valid_subs:
            meta = self._detect_subtitle_meta(sub)
            cmd += [
                "--language", f"0:{meta['lang']}",
                "--track-name", f"0:{meta['track_name']}",
                "--default-track", f"0:{meta['default']}",
                str(sub),
            ]

        # 添加字体附件
        for font in files["fonts"]:
            mime = self._detect_font_mime(font)
            cmd += ["--attachment-mime-type", mime, "--attach-file", str(font)]

        print(f"\n{'='*50}")
        print(f"📦 mkvmerge 封装 (EP{self.episode_id})")
        print(f"   视频: {hevc_video.name}")
        print(f"   字幕: {[s.name for s in valid_subs]}")
        print(f"   字体: {len(files['fonts'])} 个")
        print(f"{'='*50}")

        subprocess.run(cmd, check=True, timeout=600)
        print(f"✅ MKV 封装完成: {output_name}")
        return output_path

    # ── ffmpeg x264 硬压字幕 ────────────────────

    def ffmpeg_hardsub_encode(self, chs_template: str,
                               cht_template: str) -> list[Path]:
        """
        对纯数字 MKV 烧录 ASS 硬字幕，输出简/繁两个 MP4
        """
        files = self.get_available_files()
        pure_mkv = files["pure_mkv"]
        if not pure_mkv:
            raise PackagingError("找不到纯数字 MKV，无法压制")

        # 构建输出映射
        tasks: list[tuple[str, Path, str]] = []
        if files["chs_sub"]:
            tasks.append(("chs_sub", files["chs_sub"], chs_template))
        if files["cht_sub"]:
            tasks.append(("cht_sub", files["cht_sub"], cht_template))

        if not tasks:
            raise PackagingError("无可用字幕进行硬压")

        results: list[Path] = []
        for sub_key, sub_path, template in tasks:
            out_name = template.replace("&&", self.episode_id)
            out_path = self.episode_dir / out_name

            if out_path.exists():
                bak = backup_if_exists(out_path)
                if bak:
                    print(f"📦 已备份旧 MP4 → {bak.name}")

            # ASS 路径转绝对路径 + 正斜线（兼容 ffmpeg ass 滤镜）
            sub_abs = str(sub_path.absolute()).replace("\\", "/")

            cmd = [
                "ffmpeg", "-y", "-i", str(pure_mkv),
                "-vf", f"ass='{sub_abs}'",
            ] + self.config.x264_preset.to_ffmpeg_video_params() \
              + self.config.x264_preset.to_ffmpeg_audio_params() \
              + ["-map_metadata", "-1", "-fflags", "+bitexact",
                 "-flags:v", "+bitexact",
                 str(out_path)]

            print(f"🎬 硬压 {out_name} ...")
            subprocess.run(cmd, check=True, timeout=3600)
            print(f"✅ {out_name} 压制完成")
            results.append(out_path)

        return results

    # ── 一键封装 ─────────────────────────────────

    def package_all(self, mkv_tmpl: str, chs_tmpl: str, cht_tmpl: str) -> list[Path]:
        """
        一键执行 mkvmerge + ffmpeg 硬压（简繁）
        先校验字幕存在性
        """
        # 校验字幕
        status = self.validator.validate_for_episode(self.episode_dir, self.episode_id)
        if not status["all_ok"]:
            missing = []
            for st in ("chs", "cht"):
                if not status[st]["exists"]:
                    missing.append(f"{self.episode_id}.{st}&jpn.ass")
            if missing:
                print(f"⚠️ 字幕缺失: {', '.join(missing)}，将跳过对应步骤")

        results: list[Path] = []

        # mkvmerge
        try:
            mkv = self.mkvmerge_package(mkv_tmpl)
            if mkv:
                results.append(mkv)
        except (PackagingError, subprocess.CalledProcessError) as e:
            print(f"❌ mkvmerge 失败: {e}")

        # ffmpeg 硬压
        try:
            mp4s = self.ffmpeg_hardsub_encode(chs_tmpl, cht_tmpl)
            results.extend(mp4s)
        except (PackagingError, subprocess.CalledProcessError) as e:
            print(f"❌ ffmpeg 硬压失败: {e}")

        print(f"\n🎉 EP{self.episode_id} 封装完成，生成 {len(results)} 个文件")
        return results

    # ── 辅助 ─────────────────────────────────────

    @staticmethod
    def _maybe(path: Path) -> Path | None:
        return path if path.exists() else None

    @staticmethod
    def _find_fonts(directory: Path) -> list[Path]:
        fonts: list[Path] = []
        for ext in ("*.ttf", "*.otf", "*.ttc"):
            fonts.extend(directory.glob(ext))
        return sorted(fonts)

    @staticmethod
    def _detect_subtitle_meta(sub_path: Path) -> dict:
        """根据文件名模式检测字幕语言元数据"""
        name = sub_path.name.lower()
        # 从文件名中提取语言类型（如 01.chs&jpn.ass → chs）
        stem_parts = sub_path.stem.lower().split(".")
        if "chs" in stem_parts or (len(stem_parts) > 0 and "chs" in stem_parts[-1]):
            return {"lang": "chi", "track_name": "简体中文+日语", "default": "yes"}
        elif "cht" in stem_parts or (len(stem_parts) > 0 and "cht" in stem_parts[-1]):
            return {"lang": "chi", "track_name": "繁體中文+日语", "default": "no"}
        raise ValueError(f"无法识别字幕类型: {sub_path.name}")

    @staticmethod
    def _detect_font_mime(font_path: Path) -> str:
        ext = font_path.suffix.lower()
        if ext == ".ttf":
            return "application/x-truetype-font"
        elif ext in (".otf", ".ttc"):
            return "application/vnd.ms-opentype"
        return "application/octet-stream"
