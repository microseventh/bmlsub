"""
字幕校验、标准化与简繁转换
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ._backup import backup_if_exists
from .hanvert import HanvertConversionError, _read_ass, convert_ass_with_fanhuaji
from .config import (
    SubtitleStandard,
    SubtitleConversionConfig,
    SUB_STANDARD_HD,
    PipelineConfig,
)
from .episode import EpisodeFiles


class SubtitleConversionError(Exception):
    """字幕转换异常"""
    pass


class SubtitleValidator:
    """字幕文件校验、标准化与简繁转换"""

    def __init__(self, standard: SubtitleStandard | None = None, config: PipelineConfig | None = None):
        self.standard = standard or SUB_STANDARD_HD
        self.config = config or PipelineConfig()

    @property
    def conversion_config(self) -> SubtitleConversionConfig:
        return self.config.subtitle_conversion

    def check_subtitle_exists(self, episode_dir: Path | str,
                              episode_id: str,
                              sub_type: str,
                              chs_subtitle: Path | str | None = None,
                              cht_subtitle: Path | str | None = None) -> Path | None:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        return ctx.subtitle_for(sub_type)

    def validate_for_episode(self, episode_dir: Path | str,
                             episode_id: str,
                             chs_subtitle: Path | str | None = None,
                             cht_subtitle: Path | str | None = None) -> dict:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        result = {"chs": {}, "cht": {}, "all_ok": True, "all_subs": [p.name for p in ctx.all_subs]}
        for sub_type in ("chs", "cht"):
            sub_path = ctx.subtitle_for(sub_type)
            info = {"exists": sub_path is not None, "path": sub_path, "header_ok": True, "issues": []}
            if sub_path:
                header_issues = self.validate_ass_header(sub_path)
                if header_issues:
                    info["header_ok"] = False
                    info["issues"] = list(header_issues.keys())
                    result["all_ok"] = False
            else:
                result["all_ok"] = False
            result[sub_type] = info
        return result

    def validate_ass_header(self, ass_path: Path) -> dict[str, str]:
        header = self._parse_ass_header(ass_path)
        expected = self.standard.expected_header
        violations: dict[str, str] = {}
        for key, expected_val in expected.items():
            actual = header.get(key)
            if actual != expected_val:
                violations[key] = actual or "(缺失)"
        return violations

    def standardize_ass(self, ass_path: Path,
                        output_path: Path | None = None) -> Path:
        ass_path = Path(ass_path)
        content = ass_path.read_text(encoding="utf-8")
        if output_path is None:
            output_path = ass_path
            bak = backup_if_exists(ass_path)
            if bak:
                print(f"📦 已备份旧字幕 → {bak.name}")
        else:
            out = Path(output_path)
            if out.exists():
                bak = backup_if_exists(out)
                if bak:
                    print(f"📦 已备份旧字幕 → {bak.name}")

        expected = self.standard.expected_header
        for key, value in expected.items():
            pattern = rf"^{re.escape(key)}:\s*.*$"
            replacement = f"{key}: {value}"
            if re.search(pattern, content, flags=re.MULTILINE):
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            else:
                insert_pos = content.find("[V4")
                if insert_pos == -1:
                    insert_pos = content.find("[Events]")
                if insert_pos == -1:
                    content = content.rstrip() + f"\n{replacement}\n"
                else:
                    content = content[:insert_pos].rstrip() + f"\n{replacement}\n\n" + content[insert_pos:]

        output_path = Path(output_path)
        output_path.write_text(content, encoding="utf-8")
        print(f"📝 ASS 头部已标准化: {output_path.name}")
        return output_path

    def standardize_extracted_subs(self, episode_dir: Path, episode_id: str,
                                   source_video: Path | str | None = None,
                                   chs_subtitle: Path | str | None = None,
                                   cht_subtitle: Path | str | None = None) -> list[Path]:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        results: list[Path] = []
        for sub in ctx.extracted_subtitles:
            violations = self.validate_ass_header(sub)
            if violations:
                results.append(self.standardize_ass(sub))
            else:
                print(f"  ✅ 已合规: {sub.name}")
        return results

    def convert_chs_to_cht(self,
                           chs_path: Path | str,
                           output_path: Path | str | None = None,
                           *,
                           converter: str | None = None,
                           api_url: str | None = None,
                           timeout: int | None = None,
                           backup_existing: bool = True,
                           full_file: bool = False,
                           fallback_to_full_file: bool = True) -> Path:
        chs_path = Path(chs_path)
        if not chs_path.exists():
            raise FileNotFoundError(f"简体字幕不存在: {chs_path}")

        output = Path(output_path) if output_path else self.derive_cht_path(chs_path)
        cfg = self.conversion_config
        api = api_url or cfg.api_url
        mode = converter or cfg.converter
        req_timeout = timeout if timeout is not None else cfg.timeout

        print(f"🔄 繁化姬转换: {chs_path.name} -> {output.name} ({mode})")
        chs_content, _ = _read_ass(chs_path)

        try:
            converted_content, stats = convert_ass_with_fanhuaji(
                chs_content,
                converter=mode,
                api_url=api,
                timeout=req_timeout,
                full_file=full_file,
                fallback_to_full_file=fallback_to_full_file,
            )
        except HanvertConversionError as exc:
            raise SubtitleConversionError(str(exc)) from exc

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output.parent,
                prefix=f".{output.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(converted_content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            if backup_existing and output.exists():
                bak = backup_if_exists(output)
                if bak:
                    print(f"📦 已备份旧繁体字幕 → {bak.name}")
            temporary_path.replace(output)
            temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        mode_label = "全文件" if stats["conversion_mode"] == "full_file" else "ASS 感知"
        print(f"✅ 繁体字幕已生成: {output.name} ({mode_label}模式)")
        if stats["fallback_reason"] and stats["fallback_reason"] != "requested":
            print(f"⚠️  ASS 感知无法可靠转换，已自动改用全文件繁化: {stats['fallback_reason']}")
        if stats["length_changed_events"]:
            print(f"⚠️  {stats['length_changed_events']} 条繁化结果长度发生变化，请抽查标签位置")
        if stats["skipped_mixed_groups"]:
            print(f"⚠️  跳过 {stats['skipped_mixed_groups']} 个无法可靠拆分的中日混合文本段")
        return output

    def ensure_episode_subtitles(self,
                                 episode_dir: Path | str,
                                 episode_id: str,
                                 source_video: Path | str | None = None,
                                 chs_subtitle: Path | str | None = None,
                                 cht_subtitle: Path | str | None = None,
                                 converter: str | None = None,
                                 api_url: str | None = None,
                                 timeout: int | None = None,
                                 regenerate_cht: bool | None = None,
                                 full_file: bool = False,
                                 fallback_to_full_file: bool = True,
                                 standardize: bool = True) -> dict:
        ctx = EpisodeFiles.discover(
            episode_dir,
            episode_id,
            config=self.config,
            source_video=source_video,
            chs_subtitle=chs_subtitle,
            cht_subtitle=cht_subtitle,
        )
        cfg = self.conversion_config
        regenerate = cfg.regenerate_existing_cht if regenerate_cht is None else regenerate_cht
        chs_file = ctx.subtitle_for("chs") or ctx.subtitle_for("chi")
        cht_file = ctx.subtitle_for("cht")

        result = {
            "chs": chs_file,
            "cht": cht_file,
            "generated_cht": None,
            "backed_up": [],
            "validated": [],
            "standardized": [],
            "missing": [],
            "all_ok": True,
        }

        print(f"找到字幕文件: 简体={chs_file.name if chs_file else '❌ 未找到'} / 繁体={cht_file.name if cht_file else '❌ 未找到'}")

        if chs_file and cht_file and regenerate:
            print("📌 已同时找到简繁字幕：先生成并验证新繁体，成功后再备份旧文件")
            cht_file = self.convert_chs_to_cht(
                chs_file,
                output_path=cht_file,
                converter=converter,
                api_url=api_url,
                timeout=timeout,
                full_file=full_file,
                fallback_to_full_file=fallback_to_full_file,
            )
            result["generated_cht"] = cht_file
        elif chs_file and not cht_file:
            print("📌 仅找到简体字幕：将使用繁化姬生成繁体字幕")
            cht_file = self.convert_chs_to_cht(
                chs_file,
                output_path=self.derive_cht_path(chs_file),
                converter=converter,
                api_url=api_url,
                timeout=timeout,
                full_file=full_file,
                fallback_to_full_file=fallback_to_full_file,
            )
            result["generated_cht"] = cht_file
        elif cht_file and not chs_file:
            print("⚠️ 仅找到繁体字幕，缺少简体基准文件；跳过繁化姬转换，仅校验现有繁体字幕")
        else:
            result["all_ok"] = False
            result["missing"] = [f"{episode_id}.chs&jpn.ass", f"{episode_id}.cht&jpn.ass"]
            print("⚠️ 未找到制作组字幕文件，跳过处理")
            return result

        result["cht"] = cht_file
        active_files = [path for path in (chs_file, cht_file) if path and path.exists()]
        for sub_path in active_files:
            result["validated"].append(sub_path)
            violations = self.validate_ass_header(sub_path)
            if violations and standardize:
                print(f"📝 {sub_path.name}: 修正 {list(violations.keys())}")
                self.standardize_ass(sub_path)
                result["standardized"].append(sub_path)
            elif violations:
                result["all_ok"] = False
                print(f"⚠️ {sub_path.name}: 发现头部问题 {list(violations.keys())}")
            else:
                print(f"✅ {sub_path.name}: 已合规")

        if chs_file is None:
            result["all_ok"] = False
            result["missing"].append(f"{episode_id}.chs&jpn.ass")
        if cht_file is None:
            result["all_ok"] = False
            result["missing"].append(f"{episode_id}.cht&jpn.ass")
        return result

    def derive_cht_path(self, chs_path: Path | str) -> Path:
        chs_path = Path(chs_path)
        name = chs_path.name
        if ".chs&jpn.ass" in name:
            return chs_path.with_name(name.replace(".chs&jpn.ass", ".cht&jpn.ass"))
        if ".chs.ass" in name:
            return chs_path.with_name(name.replace(".chs.ass", ".cht.ass"))
        return chs_path.with_name(f"{chs_path.stem}.cht.ass")

    def move_to_backup(self, path: Path | str, backup_dir: Path | str | None = None) -> Path:
        src = Path(path)
        target_dir = Path(backup_dir) if backup_dir else src.parent / self.conversion_config.backup_dir_name
        target_dir.mkdir(exist_ok=True)
        target = target_dir / src.name
        if target.exists():
            bak = backup_if_exists(target)
            if bak:
                target = target_dir / src.name
        src.rename(target)
        print(f"📦 已移入备份目录: {src.name} -> {target_dir.name}/{target.name}")
        return target

    def _parse_ass_header(self, ass_path: Path) -> dict[str, str]:
        header: dict[str, str] = {}
        content = ass_path.read_text(encoding="utf-8")
        match = re.search(r"\[Script Info\](.*?)\n\[", content, re.DOTALL)
        if not match:
            return header
        section = match.group(1)
        for line in section.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith(";"):
                key, _, val = line.partition(":")
                header[key.strip()] = val.strip()
        return header
