"""
单集资源发现与统一上下文
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import PipelineConfig, ProjectNaming
from .scan import product_path, product_torrent_path


@dataclass
class EpisodeFiles:
    episode_dir: Path
    episode_id: str
    config: PipelineConfig
    pure_mkv: Path | None = None
    hevc_mkv: Path | None = None
    subtitles: dict[str, list[Path]] = field(default_factory=dict)
    fonts: list[Path] = field(default_factory=list)
    extracted_audio: list[Path] = field(default_factory=list)
    extracted_subtitles: list[Path] = field(default_factory=list)
    expected_products: dict[str, Path] = field(default_factory=dict)
    existing_products: dict[str, Path | None] = field(default_factory=dict)
    torrent_products: dict[str, Path | None] = field(default_factory=dict)
    source_video_path: Path | None = None
    override_subtitles: dict[str, Path] = field(default_factory=dict)

    @classmethod
    def discover(cls, episode_dir: Path | str,
                 episode_id: str | None = None,
                 prefix_chs: str | None = None,
                 prefix_cht: str | None = None,
                 config: PipelineConfig | None = None,
                 project: ProjectNaming | None = None,
                 source_video: Path | str | None = None,
                 chs_subtitle: Path | str | None = None,
                 cht_subtitle: Path | str | None = None) -> 'EpisodeFiles':
        config = config or PipelineConfig()
        prefix_chs, prefix_cht = cls._resolve_prefixes(config, prefix_chs, prefix_cht, project)
        d = Path(episode_dir).expanduser().resolve()
        episode_id = episode_id or cls._infer_episode_id(d)
        if not episode_id:
            raise ValueError('无法推断 episode_id，请显式指定')

        source_video_path = cls._resolve_optional_path(d, source_video)
        override_subtitles = cls._resolve_override_subtitles(d, chs_subtitle, cht_subtitle)
        subtitles = cls._discover_subtitles(d, episode_id, config, override_subtitles=override_subtitles)
        expected_products: dict[str, Path] = {}
        existing_products: dict[str, Path | None] = {}
        torrent_products: dict[str, Path | None] = {}
        for key in config.naming.formats:
            p = product_path(d, episode_id, key, prefix_chs, prefix_cht, config=config)
            expected_products[key] = p
            existing_products[key] = p if p.exists() else None
            t = product_torrent_path(existing_products[key])
            torrent_products[key] = t if t and t.exists() else None

        default_source = d / f'{episode_id}.mkv'
        pure_mkv = source_video_path or (default_source if default_source.exists() else None)

        return cls(
            episode_dir=d,
            episode_id=episode_id,
            config=config,
            pure_mkv=pure_mkv,
            hevc_mkv=(d / f'{episode_id}_HEVC10bit.mkv') if (d / f'{episode_id}_HEVC10bit.mkv').exists() else None,
            subtitles=subtitles,
            fonts=cls._discover_fonts(d),
            extracted_audio=sorted(d.glob(f'{episode_id}_audio_*.aac')),
            extracted_subtitles=sorted(d.glob(f'{episode_id}_sub_*.ass')),
            expected_products=expected_products,
            existing_products=existing_products,
            torrent_products=torrent_products,
            source_video_path=source_video_path,
            override_subtitles=override_subtitles,
        )

    @staticmethod
    def _resolve_prefixes(config: PipelineConfig,
                          prefix_chs: str | None,
                          prefix_cht: str | None,
                          project: ProjectNaming | None = None) -> tuple[str, str]:
        naming = project or config.project
        resolved_chs = prefix_chs or naming.prefix_chs
        resolved_cht = prefix_cht or naming.prefix_cht
        return resolved_chs, resolved_cht

    @staticmethod
    def _infer_episode_id(directory: Path) -> str | None:
        mkvs = sorted(directory.glob('*.mkv'))
        digits = [m.stem for m in mkvs if re.match(r'^\d+$', m.stem) and '_HEVC10bit' not in m.stem]
        return digits[0] if digits else None

    @staticmethod
    def _resolve_optional_path(directory: Path, value: Path | str | None) -> Path | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = directory / path
        return path.resolve()

    @classmethod
    def _resolve_override_subtitles(cls,
                                    directory: Path,
                                    chs_subtitle: Path | str | None,
                                    cht_subtitle: Path | str | None) -> dict[str, Path]:
        result: dict[str, Path] = {}
        chs_path = cls._resolve_optional_path(directory, chs_subtitle)
        cht_path = cls._resolve_optional_path(directory, cht_subtitle)
        if chs_path is not None:
            result['chs'] = chs_path
        if cht_path is not None:
            result['cht'] = cht_path
        return result

    @staticmethod
    def _discover_fonts(directory: Path) -> list[Path]:
        fonts: list[Path] = []
        for ext in ('*.ttf', '*.otf', '*.ttc'):
            fonts.extend(directory.glob(ext))
        return sorted(fonts)

    @staticmethod
    def _discover_subtitles(directory: Path, episode_id: str,
                            config: PipelineConfig,
                            override_subtitles: dict[str, Path] | None = None) -> dict[str, list[Path]]:
        patterns = {
            'chs': [f'{episode_id}.chs&jpn.ass', f'{episode_id}.chs.ass', f'{episode_id}.chs&ja.ass'],
            'cht': [f'{episode_id}.cht&jpn.ass', f'{episode_id}.cht.ass', f'{episode_id}.cht&ja.ass'],
            'eng': [f'{episode_id}_sub_eng_*.ass'],
            'jpn': [f'{episode_id}_sub_jpn_*.ass'],
            'chi': [f'{episode_id}_sub_chi_*.ass'],
            'other': [f'{episode_id}_sub_*.ass'],
        }
        result = {key: [] for key in patterns}
        override_subtitles = override_subtitles or {}

        for key in ('chs', 'cht'):
            path = override_subtitles.get(key)
            if path is not None:
                result[key].append(path)

        for key, globs in patterns.items():
            seen: set[Path] = set(result[key])
            for pattern in globs:
                for path in sorted(directory.glob(pattern)):
                    if path not in seen:
                        seen.add(path)
                        result[key].append(path)
        return result

    @property
    def all_subs(self) -> list[Path]:
        ordered: list[Path] = []
        seen: set[Path] = set()
        for key in ('chs', 'cht', 'eng', 'jpn', 'chi', 'other'):
            for path in self.subtitles.get(key, []):
                if path not in seen:
                    seen.add(path)
                    ordered.append(path)
        return ordered

    def subtitle_for(self, sub_type: str) -> Path | None:
        candidates = self.subtitles.get(sub_type, [])
        return candidates[0] if candidates else None

    def summary(self) -> dict:
        return {
            'episode_id': self.episode_id,
            'pure_mkv': str(self.pure_mkv) if self.pure_mkv else None,
            'source_video_path': str(self.source_video_path) if self.source_video_path else None,
            'override_subtitles': {k: str(v) for k, v in self.override_subtitles.items()},
            'hevc_mkv': str(self.hevc_mkv) if self.hevc_mkv else None,
            'subtitles': {k: [str(p) for p in v] for k, v in self.subtitles.items() if v},
            'fonts': [str(p) for p in self.fonts],
            'expected_products': {k: str(v) for k, v in self.expected_products.items()},
            'existing_products': {k: str(v) if v else None for k, v in self.existing_products.items()},
            'torrent_products': {k: str(v) if v else None for k, v in self.torrent_products.items()},
        }
