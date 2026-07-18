"""Formal paths and release naming for workstation workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ProductKind(str, Enum):
    MP4_CHS = "mp4_chs"
    MP4_CHT = "mp4_cht"
    MKV_HEVC = "mkv_hevc"


@dataclass(frozen=True)
class ReleaseNames:
    group_chs: str
    group_cht: str
    title_chs: str
    title_cht: str
    romanized_title: str

    def __post_init__(self) -> None:
        for name, value in (
            ("group_chs", self.group_chs),
            ("group_cht", self.group_cht),
            ("title_chs", self.title_chs),
            ("title_cht", self.title_cht),
            ("romanized_title", self.romanized_title),
        ):
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{name} must not be empty")
            if any(character in normalized for character in ("/", "\\", "\x00")):
                raise ValueError(f"{name} contains a path separator")
            object.__setattr__(self, name, normalized)

    def to_dict(self) -> dict[str, str]:
        return {
            "group_chs": self.group_chs,
            "group_cht": self.group_cht,
            "title_chs": self.title_chs,
            "title_cht": self.title_cht,
            "romanized_title": self.romanized_title,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, str]) -> "ReleaseNames":
        return cls(**value)


def product_filename(episode_id: str, kind: ProductKind | str, names: ReleaseNames) -> str:
    episode = episode_id.strip()
    if not episode or any(character in episode for character in ("/", "\\", "\x00")):
        raise ValueError("episode_id is invalid")
    selected = ProductKind(kind)
    if selected is ProductKind.MP4_CHT:
        prefix = f"[{names.group_cht}] {names.title_cht} {names.romanized_title}"
        suffix = "[1080P][繁日內嵌].mp4"
    elif selected is ProductKind.MP4_CHS:
        prefix = f"[{names.group_chs}] {names.title_chs} {names.romanized_title}"
        suffix = "[1080P][简日内嵌].mp4"
    else:
        prefix = f"[{names.group_chs}] {names.title_chs} {names.romanized_title}"
        suffix = "[1080P][HEVC-10bit][简繁日内封].mkv"
    return f"{prefix} [{episode}]{suffix}"


def workstation_root(workspace: Path | str) -> Path:
    return Path(workspace).expanduser().resolve() / "workstation"


def intermediate_path(workspace: Path | str, episode_id: str) -> Path:
    return workstation_root(workspace) / "delivery" / "intermediate" / f"{episode_id}_HEVC10bit.mkv"


def product_path(workspace: Path | str, episode_id: str,
                 kind: ProductKind | str, names: ReleaseNames) -> Path:
    return workstation_root(workspace) / "delivery" / "products" / product_filename(
        episode_id, kind, names
    )


def product_torrent_path(path: Path | str, workspace: Path | str | None = None) -> Path:
    source = Path(path).expanduser().resolve()
    directory = (workstation_root(workspace) / "delivery" / "torrents"
                 if workspace is not None else source.parent)
    return directory / f"{source.name}.torrent"
