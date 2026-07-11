"""
字幕校验与标准化

修复点:
- 校验 ASS 头部是否符合 HD 规范 (PlayRes 1920x1080, TV.709)
- 检查 {episode_id}.chs&jpn.ass / .cht&jpn.ass 是否存在
- 标准化提取出的原始 ASS 字幕头部
"""

import re
from pathlib import Path

from ._backup import backup_if_exists
from .config import SubtitleStandard, SUB_STANDARD_HD


class SubtitleValidator:
    """字幕文件校验与标准化"""

    def __init__(self, standard: SubtitleStandard | None = None):
        self.standard = standard or SUB_STANDARD_HD

    # ── 文件存在性检查 ───────────────────────────

    def check_subtitle_exists(self, episode_dir: Path | str,
                               episode_id: str,
                               sub_type: str) -> Path | None:
        """
        检查字幕文件是否存在

        Parameters
        ----------
        episode_dir : 集数目录
        episode_id : 集数编号，如 "01"
        sub_type : 'chs' 或 'cht'

        Returns
        -------
        如果存在返回 Path，否则返回 None
        """
        episode_dir = Path(episode_dir)
        sub_path = episode_dir / f"{episode_id}.{sub_type}&jpn.ass"
        if sub_path.exists():
            return sub_path
        return None

    def validate_for_episode(self, episode_dir: Path | str,
                              episode_id: str) -> dict:
        """
        完整校验单集字幕状态

        Returns
        -------
        {
            'chs': {'exists': bool, 'path': Path|None, 'header_ok': bool, 'issues': list},
            'cht': {'exists': bool, 'path': Path|None, 'header_ok': bool, 'issues': list},
            'all_ok': bool
        }
        """
        episode_dir = Path(episode_dir)
        result = {"chs": {}, "cht": {}, "all_ok": True}

        for sub_type in ("chs", "cht"):
            sub_path = self.check_subtitle_exists(episode_dir, episode_id, sub_type)
            info = {"exists": sub_path is not None, "path": sub_path,
                    "header_ok": True, "issues": []}

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

    # ── ASS 头部校验 ─────────────────────────────

    def validate_ass_header(self, ass_path: Path) -> dict[str, str]:
        """
        检查 ASS 头部是否符合规范

        Returns
        -------
        dict: 不合规的字段 → 当前值。空 dict 表示完全合规
        """
        header = self._parse_ass_header(ass_path)
        expected = self.standard.expected_header
        violations: dict[str, str] = {}

        for key, expected_val in expected.items():
            actual = header.get(key)
            if actual != expected_val:
                violations[key] = actual or "(缺失)"

        return violations

    # ── ASS 头部标准化 ───────────────────────────

    def standardize_ass(self, ass_path: Path,
                         output_path: Path | None = None) -> Path:
        """
        修正 ASS 头部，使其符合 HD 规范。不修改 [V4+ Styles] 和 [Events] 段

        Parameters
        ----------
        ass_path : 待修正的 ASS 文件
        output_path : 输出路径，默认覆盖原文件（会先备份为 .bak）

        Returns
        -------
        修正后的文件路径
        """
        ass_path = Path(ass_path)
        content = ass_path.read_text(encoding="utf-8")

        if output_path is None:
            output_path = ass_path
            # 原地覆盖前先备份到 _backup/
            bak = backup_if_exists(ass_path)
            if bak:
                print(f"📦 已备份旧字幕 → {bak.name}")
        else:
            # 显式指定输出路径也备份
            out = Path(output_path)
            if out.exists():
                bak = backup_if_exists(out)
                if bak:
                    print(f"📦 已备份旧字幕 → {bak.name}")
        expected = self.standard.expected_header

        # 对每个期望的字段，替换或插入
        for key, value in expected.items():
            pattern = rf'^{re.escape(key)}:\s*.*$'
            replacement = f"{key}: {value}"

            if re.search(pattern, content, flags=re.MULTILINE):
                content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
            else:
                # 字段不存在 → 插入到 [Script Info] 段末尾，[V4 之前
                insert_pos = content.find("[V4")
                if insert_pos == -1:
                    insert_pos = content.find("[Events]")
                if insert_pos == -1:
                    # 追加到末尾
                    content = content.rstrip() + f"\n{replacement}\n"
                else:
                    content = (content[:insert_pos].rstrip()
                               + f"\n{replacement}\n\n"
                               + content[insert_pos:])

        output_path = Path(output_path)
        output_path.write_text(content, encoding="utf-8")
        print(f"📝 ASS 头部已标准化: {output_path.name}")
        return output_path

    def standardize_extracted_subs(self, episode_dir: Path,
                                    episode_id: str) -> list[Path]:
        """
        对提取出的 _raw_sub_*.ass 文件批量标准化
        """
        episode_dir = Path(episode_dir)
        raw_subs = sorted(episode_dir.glob(f"{episode_id}_sub_*.ass"))
        results: list[Path] = []
        for sub in raw_subs:
            violations = self.validate_ass_header(sub)
            if violations:
                results.append(self.standardize_ass(sub))
            else:
                print(f"  ✅ 已合规: {sub.name}")
        return results

    # ── 内部辅助 ─────────────────────────────────

    def _parse_ass_header(self, ass_path: Path) -> dict[str, str]:
        """解析 ASS 文件 [Script Info] 段"""
        header: dict[str, str] = {}
        content = ass_path.read_text(encoding="utf-8")

        # 仅解析 [Script Info] 到下一个 [...] 段之间的内容
        # 用 \n\[ 而非 \[ 确保不会匹配到字段值中的 [ （如 Title: [Seigyoku]）
        match = re.search(r'\[Script Info\](.*?)\n\[', content, re.DOTALL)
        if not match:
            return header

        section = match.group(1)
        for line in section.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith(";"):
                key, _, val = line.partition(":")
                header[key.strip()] = val.strip()

        return header
