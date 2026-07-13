"""
封装 — mkvmerge 内封 + ffmpeg 硬压
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ._backup import backup_if_exists
from .config import PipelineConfig, ProjectNaming
from .episode import EpisodeFiles
from .subtitle import SubtitleValidator


class PackagingError(Exception):
    """封装异常"""
    pass


@dataclass
class PackagingPlan:
    """单集封装计划。

    每一步都可独立执行，但会明确列出当前阶段依赖的输入与缺失项。
    """

    episode_id: str
    pure_mkv: Path | None
    hevc_mkv: Path | None
    chs_sub: Path | None
    cht_sub: Path | None
    fonts: list[Path] = field(default_factory=list)
    mkv_output: Path | None = None
    mp4_chs_output: Path | None = None
    mp4_cht_output: Path | None = None
    missing_for_mkv: list[str] = field(default_factory=list)
    missing_for_mp4: list[str] = field(default_factory=list)

    @property
    def has_mkv_inputs(self) -> bool:
        return not self.missing_for_mkv

    @property
    def has_mp4_inputs(self) -> bool:
        return not self.missing_for_mp4

    def summary(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "pure_mkv": str(self.pure_mkv) if self.pure_mkv else None,
            "hevc_mkv": str(self.hevc_mkv) if self.hevc_mkv else None,
            "chs_sub": str(self.chs_sub) if self.chs_sub else None,
            "cht_sub": str(self.cht_sub) if self.cht_sub else None,
            "fonts": [str(font) for font in self.fonts],
            "mkv_output": str(self.mkv_output) if self.mkv_output else None,
            "mp4_chs_output": str(self.mp4_chs_output) if self.mp4_chs_output else None,
            "mp4_cht_output": str(self.mp4_cht_output) if self.mp4_cht_output else None,
            "missing_for_mkv": list(self.missing_for_mkv),
            "missing_for_mp4": list(self.missing_for_mp4),
        }


class Packager:
    """视频字幕封装器。"""

    def __init__(self, episode_dir: Path | str, episode_id: str,
                 config: PipelineConfig | None = None,
                 source_video: Path | str | None = None,
                 chs_subtitle: Path | str | None = None,
                 cht_subtitle: Path | str | None = None):
        self.episode_dir = Path(episode_dir).expanduser().resolve()
        self.episode_id = episode_id
        self.config = config or PipelineConfig(work_dir=self.episode_dir)
        self.source_video = source_video
        self.chs_subtitle = chs_subtitle
        self.cht_subtitle = cht_subtitle
        self.validator = SubtitleValidator(self.config.sub_standard, config=self.config)

    def context(self) -> EpisodeFiles:
        return EpisodeFiles.discover(
            self.episode_dir,
            self.episode_id,
            config=self.config,
            source_video=self.source_video,
            chs_subtitle=self.chs_subtitle,
            cht_subtitle=self.cht_subtitle,
        )

    def get_available_files(self) -> dict:
        ctx = self.context()
        result = {
            "pure_mkv": ctx.pure_mkv,
            "hevc_mkv": ctx.hevc_mkv,
            "chs_sub": ctx.subtitle_for("chs") or ctx.subtitle_for("chi"),
            "cht_sub": ctx.subtitle_for("cht"),
            "eng_sub": ctx.subtitle_for("eng"),
            "jpn_sub": ctx.subtitle_for("jpn"),
            "all_subs": ctx.all_subs,
            "fonts": ctx.fonts,
            "context": ctx,
        }
        sub_names = [p.name for p in result["all_subs"]]
        print(f"📋 字幕扫描 ({self.episode_id}): {sub_names if sub_names else '无匹配字幕'}")
        if not result["all_subs"]:
            print(f"⚠️  未找到任何匹配 {self.episode_id}.*.ass 的字幕文件！")
        return result

    def build_plan(self,
                   prefix_chs: str | None = None,
                   prefix_cht: str | None = None,
                   project: ProjectNaming | None = None) -> PackagingPlan:
        ctx = EpisodeFiles.discover(
            self.episode_dir,
            self.episode_id,
            prefix_chs=prefix_chs,
            prefix_cht=prefix_cht,
            config=self.config,
            project=project,
            source_video=self.source_video,
            chs_subtitle=self.chs_subtitle,
            cht_subtitle=self.cht_subtitle,
        )
        chs_sub = ctx.subtitle_for("chs") or ctx.subtitle_for("chi")
        cht_sub = ctx.subtitle_for("cht")

        mkv_missing: list[str] = []
        if not ctx.hevc_mkv:
            mkv_missing.append(f"{self.episode_id}_HEVC10bit.mkv")
        if not chs_sub:
            mkv_missing.append(f"{self.episode_id}.chs&jpn.ass")
        if not cht_sub:
            mkv_missing.append(f"{self.episode_id}.cht&jpn.ass")
        if not ctx.fonts:
            mkv_missing.append("字体文件 (.ttf/.otf/.ttc)")

        mp4_missing: list[str] = []
        if not ctx.pure_mkv:
            mp4_missing.append(str(ctx.source_video_path or (ctx.episode_dir / f"{self.episode_id}.mkv")))
        if not chs_sub and not cht_sub:
            mp4_missing.append(f"{self.episode_id}.chs&jpn.ass / {self.episode_id}.cht&jpn.ass")

        return PackagingPlan(
            episode_id=self.episode_id,
            pure_mkv=ctx.pure_mkv,
            hevc_mkv=ctx.hevc_mkv,
            chs_sub=chs_sub,
            cht_sub=cht_sub,
            fonts=ctx.fonts,
            mkv_output=ctx.expected_products.get("mkv_hevc"),
            mp4_chs_output=ctx.expected_products.get("mp4_chs"),
            mp4_cht_output=ctx.expected_products.get("mp4_cht"),
            missing_for_mkv=mkv_missing,
            missing_for_mp4=mp4_missing,
        )

    def mkvmerge_package(self,
                         output_template: str | None = None,
                         output_path: Path | str | None = None) -> Path:
        files = self.get_available_files()
        hevc_video = files["hevc_mkv"]
        if not hevc_video:
            raise PackagingError("找不到 HEVC10bit 影片，无法封装")

        chs_sub = files["chs_sub"]
        cht_sub = files["cht_sub"]
        if not chs_sub or not cht_sub:
            raise PackagingError("内封 MKV 需要同时存在简繁字幕")
        if not files["fonts"]:
            raise PackagingError("内封 MKV 需要字体文件")

        output = self._resolve_output_path(output_template=output_template, output_path=output_path)
        self._backup_output(output, "MKV")

        valid_subs = [("chs", chs_sub), ("cht", cht_sub)]
        cmd = ["mkvmerge", "-o", str(output), str(hevc_video)]
        for sub_type, sub in valid_subs:
            meta = self._detect_subtitle_meta(sub_type)
            cmd += [
                "--language", f"0:{meta['lang']}",
                "--track-name", f"0:{meta['track_name']}",
                "--default-track", f"0:{meta['default']}",
                str(sub),
            ]

        for font in files["fonts"]:
            mime = self._detect_font_mime(font)
            cmd += ["--attachment-mime-type", mime, "--attach-file", str(font)]

        print(f"\n{'=' * 50}")
        print(f"📦 mkvmerge 封装 (EP{self.episode_id})")
        print(f"   视频: {hevc_video.name}")
        print(f"   字幕: {[s.name for _, s in valid_subs]}")
        print(f"   字体: {len(files['fonts'])} 个")
        print(f"   输出: {output.name}")
        print(f"{'=' * 50}")

        subprocess.run(cmd, check=True, timeout=600)
        print(f"✅ MKV 封装完成: {output.name}")
        return output

    def ffmpeg_hardsub_encode(self,
                              chs_template: str | None = None,
                              cht_template: str | None = None,
                              chs_output: Path | str | None = None,
                              cht_output: Path | str | None = None) -> list[Path]:
        files = self.get_available_files()
        pure_mkv = files["pure_mkv"]
        if not pure_mkv:
            raise PackagingError("找不到纯数字 MKV，无法压制")

        tasks: list[tuple[Path, Path, str]] = []
        if files["chs_sub"]:
            output = self._resolve_output_path(output_template=chs_template, output_path=chs_output)
            tasks.append((files["chs_sub"], output, "简体中文"))
        if files["cht_sub"]:
            output = self._resolve_output_path(output_template=cht_template, output_path=cht_output)
            tasks.append((files["cht_sub"], output, "繁體中文"))
        if not tasks:
            raise PackagingError("无可用字幕进行硬压")

        results: list[Path] = []
        for sub_path, out_path, label in tasks:
            self._backup_output(out_path, "MP4")
            sub_abs = str(sub_path.absolute()).replace("\\", "/")
            cmd = [
                "ffmpeg", "-y", "-i", str(pure_mkv),
                "-vf", f"ass='{sub_abs}'",
            ]
            cmd += self.config.x264_preset.to_ffmpeg_video_params()
            cmd += self.config.x264_preset.to_ffmpeg_audio_params()
            cmd += ["-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", str(out_path)]

            print(f"🎬 硬压 ({label}): {out_path.name}")
            subprocess.run(cmd, check=True, timeout=3600)
            print(f"✅ {out_path.name} 压制完成")
            results.append(out_path)

        return results

    def package_expected(self,
                         prefix_chs: str | None = None,
                         prefix_cht: str | None = None,
                         project: ProjectNaming | None = None) -> list[Path]:
        plan = self.build_plan(prefix_chs=prefix_chs, prefix_cht=prefix_cht, project=project)
        results: list[Path] = []

        if not plan.has_mp4_inputs:
            print(f"⚠️ 跳过 MP4 硬压，缺少: {', '.join(plan.missing_for_mp4)}")
        else:
            results.extend(self.ffmpeg_hardsub_encode(
                chs_output=plan.mp4_chs_output if plan.chs_sub else None,
                cht_output=plan.mp4_cht_output if plan.cht_sub else None,
            ))

        if not plan.has_mkv_inputs:
            print(f"⚠️ 跳过 MKV 内封，缺少: {', '.join(plan.missing_for_mkv)}")
        else:
            results.append(self.mkvmerge_package(output_path=plan.mkv_output))

        print(f"\n🎉 EP{self.episode_id} 封装完成，生成 {len(results)} 个文件")
        return results

    def package_all(self, mkv_tmpl: str, chs_tmpl: str, cht_tmpl: str) -> list[Path]:
        status = self.validator.validate_for_episode(
            self.episode_dir,
            self.episode_id,
            chs_subtitle=self.chs_subtitle,
            cht_subtitle=self.cht_subtitle,
        )
        if not status["all_ok"]:
            missing = []
            for st in ("chs", "cht"):
                if status[st]["exists"]:
                    continue
                override_path = self.chs_subtitle if st == "chs" else self.cht_subtitle
                missing.append(str(Path(override_path)) if override_path else f"{self.episode_id}.{st}&jpn.ass")
            if missing:
                print(f"⚠️ 字幕缺失: {', '.join(missing)}，将跳过对应步骤")

        results: list[Path] = []
        try:
            results.extend(self.ffmpeg_hardsub_encode(chs_template=chs_tmpl, cht_template=cht_tmpl))
        except (PackagingError, subprocess.CalledProcessError) as e:
            print(f"❌ ffmpeg 硬压失败: {e}")

        try:
            results.append(self.mkvmerge_package(output_template=mkv_tmpl))
        except (PackagingError, subprocess.CalledProcessError) as e:
            print(f"❌ mkvmerge 失败: {e}")

        print(f"\n🎉 EP{self.episode_id} 封装完成，生成 {len(results)} 个文件")
        return results

    def _resolve_output_path(self,
                             output_template: str | None = None,
                             output_path: Path | str | None = None) -> Path:
        if output_path is not None:
            return Path(output_path)
        if not output_template:
            raise PackagingError("缺少输出路径或输出模板")
        output_name = output_template.replace("&&", self.episode_id)
        return self.episode_dir / output_name

    def _backup_output(self, output_path: Path, label: str) -> None:
        if output_path.exists():
            bak = backup_if_exists(output_path)
            if bak:
                print(f"📦 已备份旧 {label} → {bak.name}")

    def _detect_subtitle_meta(self, sub_type: str) -> dict:
        return {
            "lang": self.config.track_meta.languages[sub_type],
            "track_name": self.config.track_meta.names[sub_type],
            "default": self.config.track_meta.defaults[sub_type],
        }

    @staticmethod
    def _detect_font_mime(font_path: Path) -> str:
        ext = font_path.suffix.lower()
        if ext == ".ttf":
            return "application/x-truetype-font"
        if ext in (".otf", ".ttc"):
            return "application/vnd.ms-opentype"
        return "application/octet-stream"
