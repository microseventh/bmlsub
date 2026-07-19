"""Series-level metadata creation, discovery, and strict validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping
import json
import os
import re
import tempfile

from ..hanvert import ConverterProvider, convert_plain_text
from ..interactive import default_prompt, optional_prompt, ui_text

from ..ass_analysis.profiles import AssAnalysisProfile
from ..production.profiles import normalize_h264_parameters
from ..release.profiles import TorrentProfile


SERIES_SCHEMA_VERSION = "bmlsub-series-v1"
_SECRET_MARKERS = (
    "password", "passwd", "secret", "token", "access_key", "private_key",
    "authorization", "cookie", "api_key", "apikey",
)
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9._@-]+$")
_PRODUCTION_KEYS = (
    "hardsub_parameters", "hevc_parameters", "ass_profile", "torrent_profile",
)
_SERIES_QUESTIONS = (
    {"key": "parent_dir", "zh": "番组文件夹的上级目录", "en": "Parent directory for the series folder", "required": False, "default": "~/Downloads"},
    {"key": "series_folder_name", "zh": "番组文件夹名", "en": "Series folder name", "required": True},
    {"key": "title_chs", "zh": "简体中文番名", "en": "Simplified Chinese series title", "required": True},
    {"key": "romanized_title", "zh": "罗马音番名", "en": "Romanized series title", "required": True},
    {"key": "group_chs", "zh": "简体制作组名", "en": "Simplified Chinese release group name", "required": True},
    {"key": "bgm_id", "zh": "Bangumi ID", "en": "Bangumi ID", "required": False},
    {"key": "anime_id", "zh": "Anime ID", "en": "Anime ID", "required": False},
    {"key": "production", "zh": "Production JSON", "en": "Production JSON", "required": False, "default": "{}"},
)


def _strict_mapping(value: Any, *, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    result = dict(value)
    unknown = set(result) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    return result


def _reject_secret_fields(value: Any, prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in _SECRET_MARKERS):
                location = f"{prefix}.{key}" if prefix else str(key)
                raise ValueError(f"series metadata must not contain secret field: {location}")
            _reject_secret_fields(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, f"{prefix}[{index}]")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized or any(character in normalized for character in ("/", "\\", "\x00")):
        raise ValueError(f"{label} is invalid")
    return normalized


def _series_folder_name(value: Any) -> str:
    normalized = _text(value, "series_folder_name")
    if normalized in {".", ".."}:
        raise ValueError("series_folder_name is invalid")
    return normalized


def _normalize_production(value: Any) -> dict[str, Any]:
    production = _strict_mapping(
        value if value is not None else {}, allowed=set(_PRODUCTION_KEYS), label="production",
    )
    hardsub = normalize_h264_parameters(production.get("hardsub_parameters", {}))
    hardsub.pop("profile_version", None)

    hevc = _strict_mapping(
        production.get("hevc_parameters", {}),
        allowed={
            "video_codec", "pixel_format", "quality", "audio_codec", "audio_bitrate",
            "include_audio", "strip_metadata", "profile_version",
        }, label="production.hevc_parameters",
    )
    from ..production.models import ProductionOperation
    from ..production.profiles import normalize_profile
    normalized_hevc = normalize_profile(ProductionOperation.ENCODE, "hevc-10bit", hevc).normalized()
    normalized_hevc.pop("profile_version", None)

    ass = _strict_mapping(
        production.get("ass_profile", {}), allowed=set(production.get("ass_profile", {})),
        label="production.ass_profile",
    )
    AssAnalysisProfile.from_value(ass)

    torrent = _strict_mapping(
        production.get("torrent_profile", {"format": "v1"}),
        allowed=set(production.get("torrent_profile", {"format": "v1"})),
        label="production.torrent_profile",
    )
    TorrentProfile.from_mapping(torrent)
    return {
        "hardsub_parameters": hardsub,
        "hevc_parameters": normalized_hevc,
        "ass_profile": ass,
        "torrent_profile": torrent,
    }


def _normalize_publish(value: Any) -> dict[str, Any]:
    publish = _strict_mapping(
        value if value is not None else {},
        allowed={
            "r2_bucket", "r2_access", "r2_public_base_url", "rclone_remote",
            "ssh_alias", "remote_root", "qb_port", "qb_save_path",
            "qb_webui_origin", "notes",
            "credential_aliases",
        }, label="publish",
    )
    aliases = _strict_mapping(
        publish.get("credential_aliases", {}),
        allowed={"r2", "ssh", "qbittorrent", "anibt"}, label="publish.credential_aliases",
    )
    for key, item in aliases.items():
        if not isinstance(item, str) or not _SAFE_ALIAS.fullmatch(item):
            raise ValueError(f"credential alias {key} is invalid")
    publish["credential_aliases"] = aliases
    remote_root = publish.get("remote_root")
    if remote_root is not None:
        if not isinstance(remote_root, str):
            raise ValueError("publish.remote_root must be a string")
        remote = PurePosixPath(remote_root)
        if not remote.is_absolute() or any(part in {"", ".", ".."} for part in remote.parts[1:]):
            raise ValueError("publish.remote_root must be a normalized absolute POSIX path")
    qb_port = publish.get("qb_port", 8080)
    if isinstance(qb_port, bool) or not isinstance(qb_port, int) or not 1 <= qb_port <= 65535:
        raise ValueError("publish.qb_port is invalid")
    qb_save_path = publish.get("qb_save_path", "/downloads")
    if not isinstance(qb_save_path, str):
        raise ValueError("publish.qb_save_path must be a string")
    qb_path = PurePosixPath(qb_save_path)
    if not qb_path.is_absolute() or any(part in {"", ".", ".."} for part in qb_path.parts[1:]):
        raise ValueError("publish.qb_save_path must be a normalized absolute POSIX path")
    publish["qb_save_path"] = str(qb_path)
    return publish


def _normalize_payload(value: Any) -> dict[str, Any]:
    root = _strict_mapping(
        value, allowed={"schema_version", "series", "groups", "production", "publish"},
        label="series metadata",
    )
    _reject_secret_fields(root)
    if root.get("schema_version") != SERIES_SCHEMA_VERSION:
        raise ValueError("unsupported series metadata schema_version")
    series = _strict_mapping(
        root.get("series"),
        allowed={"title_chs", "title_cht", "romanized_title", "bgm_id", "anime_id", "traditionalization"},
        label="series",
    )
    groups = _strict_mapping(root.get("groups"), allowed={"chs", "cht"}, label="groups")
    bgm_id = series.get("bgm_id")
    if bgm_id is not None and (
        isinstance(bgm_id, bool) or not isinstance(bgm_id, int) or bgm_id <= 0
    ):
        raise ValueError("series.bgm_id must be positive")
    anime_id = series.get("anime_id")
    if anime_id is not None and (not isinstance(anime_id, str) or not anime_id.strip()):
        raise ValueError("series.anime_id must be a non-empty string")
    return {
        "schema_version": SERIES_SCHEMA_VERSION,
        "series": {
            "title_chs": _text(series.get("title_chs"), "series.title_chs"),
            "title_cht": _optional_text(series.get("title_cht"), "series.title_cht"),
            "romanized_title": _text(series.get("romanized_title"), "series.romanized_title"),
            "bgm_id": bgm_id,
            "anime_id": anime_id.strip() if isinstance(anime_id, str) else None,
            "traditionalization": _normalize_traditionalization(
                series.get("traditionalization"),
                title_chs=_text(series.get("title_chs"), "series.title_chs"),
                group_chs=_text(groups.get("chs"), "groups.chs"),
                title_cht=_optional_text(series.get("title_cht"), "series.title_cht"),
                group_cht=_optional_text(groups.get("cht"), "groups.cht"),
            ),
        },
        "groups": {
            "chs": _text(groups.get("chs"), "groups.chs"),
            "cht": _optional_text(groups.get("cht"), "groups.cht"),
        },
        "production": _normalize_production(root.get("production", {})),
        "publish": _normalize_publish(root.get("publish", {})),
    }


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    normalized = value.strip()
    if any(character in normalized for character in ("/", "\\", "\x00")):
        raise ValueError(f"{label} is invalid")
    return normalized or None


def _normalize_traditionalization(value: Any, *, title_chs: str, group_chs: str,
                                  title_cht: str | None, group_cht: str | None) -> dict[str, Any]:
    if value is None:
        return {
            "status": "resolved" if title_cht and group_cht else "pending",
            "converter": "Taiwan",
            "api_url": "https://api.zhconvert.org/convert",
            "attempts": {},
        }
    result = _strict_mapping(
        value, allowed={"status", "converter", "api_url", "attempts"},
        label="series.traditionalization",
    )
    attempts = _strict_mapping(result.get("attempts", {}), allowed={"title_cht", "group_cht"},
                               label="series.traditionalization.attempts")
    result["attempts"] = attempts
    result.setdefault("converter", "Taiwan")
    result.setdefault("api_url", "https://api.zhconvert.org/convert")
    result.setdefault("status", "resolved" if title_cht and group_cht else "pending")
    if result["status"] not in {"pending", "resolved"}:
        raise ValueError("series.traditionalization.status is invalid")
    return result


def ensure_traditional_series_names(
    path: Path | str, *, provider: ConverterProvider | None = None,
    converter: str = "Taiwan", api_url: str = "https://api.zhconvert.org/convert",
    timeout: int = 60, force: bool = False,
) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    raw = json.loads(target.read_text(encoding="utf-8"))
    normalized = _normalize_payload(raw)
    series = normalized["series"]
    groups = normalized["groups"]
    traditional = dict(series["traditionalization"])
    attempts = dict(traditional.get("attempts", {}))
    errors = {}
    for key, source in (("title_cht", series["title_chs"]), ("group_cht", groups["chs"])):
        if not force and (series.get(key) if key == "title_cht" else groups.get("cht")):
            continue
        previous = dict(attempts.get(key, {}))
        try:
            converted = convert_plain_text(
                source, converter=converter, api_url=api_url, timeout=timeout, provider=provider,
            )
            if key == "title_cht":
                series[key] = converted
            else:
                groups["cht"] = converted
            attempts[key] = {"source": source, "status": "succeeded", "value": converted}
        except Exception as exc:
            error = {"code": "traditionalization_failed", "message": str(exc), "retryable": True}
            attempts[key] = {**previous, "source": source, "status": "failed", "error": error}
            errors[key] = error
    traditional.update({"status": "resolved" if series.get("title_cht") and groups.get("cht") else "pending",
                        "converter": converter, "api_url": api_url, "attempts": attempts})
    series["traditionalization"] = traditional
    normalized["series"] = series
    normalized["groups"] = groups
    _atomic_write_series(target, _serialized_payload(normalized), replace=True)
    return {
        "status": traditional["status"], "metadata_path": str(target),
        "title_cht": series.get("title_cht"), "group_cht": groups.get("cht"),
        "traditionalization": traditional, "errors": errors,
    }
def _serialized_payload(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write_series(path: Path, data: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise FileExistsError(f"series metadata already exists: {path}") from exc
            temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True)
class SeriesMetadata:
    path: Path
    content_hash: str
    title_chs: str
    title_cht: str | None
    romanized_title: str
    group_chs: str
    group_cht: str | None
    bgm_id: int | None
    anime_id: str | None
    production: Mapping[str, Any]
    publish: Mapping[str, Any]
    traditionalization: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", self.path.expanduser().resolve())
        object.__setattr__(self, "production", MappingProxyType(dict(self.production)))
        object.__setattr__(self, "publish", MappingProxyType(dict(self.publish)))
        object.__setattr__(self, "traditionalization", MappingProxyType(dict(self.traditionalization)))

    @classmethod
    def load(cls, path: Path | str) -> "SeriesMetadata":
        source = Path(path).expanduser().resolve()
        raw = source.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("series metadata is unreadable or invalid JSON") from exc
        normalized = _normalize_payload(payload)
        series = normalized["series"]
        groups = normalized["groups"]
        return cls(
            path=source, content_hash=sha256(raw).hexdigest(),
            title_chs=series["title_chs"], title_cht=series["title_cht"],
            romanized_title=series["romanized_title"],
            group_chs=groups["chs"], group_cht=groups["cht"],
            bgm_id=series["bgm_id"], anime_id=series["anime_id"],
            production=normalized["production"], publish=normalized["publish"],
            traditionalization=normalized["series"]["traditionalization"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SERIES_SCHEMA_VERSION,
            "metadata_path": str(self.path), "metadata_hash": self.content_hash,
            "series": {
                "title_chs": self.title_chs, "title_cht": self.title_cht,
                "romanized_title": self.romanized_title,
                "bgm_id": self.bgm_id, "anime_id": self.anime_id,
            "traditionalization": dict(self.traditionalization),
            },
            "groups": {"chs": self.group_chs, "cht": self.group_cht},
            "production": dict(self.production), "publish": dict(self.publish),
        }


def series_metadata_template() -> dict[str, Any]:
    """Return a non-secret template that must be completed before use."""
    return {
        "schema_version": SERIES_SCHEMA_VERSION,
        "series": {
            "title_chs": "请填写简体中文番名",
            "title_cht": None,
            "romanized_title": "PleaseFillRomanizedTitle",
            "bgm_id": None,
            "anime_id": None,
            "traditionalization": {
                "status": "pending", "converter": "Taiwan",
                "api_url": "https://api.zhconvert.org/convert", "attempts": {},
            },
        },
        "groups": {
            "chs": "请填写简体制作组名",
            "cht": None,
        },
        "production": {
            "hardsub_parameters": {},
            "hevc_parameters": {},
            "ass_profile": {},
            "torrent_profile": {"format": "v1"},
        },
        "publish": {
            "r2_bucket": "bml",
            "r2_access": "private",
            "rclone_remote": "r2",
            "qb_port": 8080,
            "qb_save_path": "/downloads",
            "credential_aliases": {},
        },
    }


def write_series_metadata_template(series_root: Path | str, *, replace: bool = False) -> Path:
    """Write ``bgminfo/series.template.json`` without treating it as live config."""
    root = Path(series_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("series root directory does not exist")
    target = root / "bgminfo" / "series.template.json"
    _atomic_write_series(
        target, _serialized_payload(series_metadata_template()), replace=replace,
    )
    return target


def create_series_metadata(
    series_folder_name: str, *, parent_dir: Path | str | None = None,
    title_chs: str, title_cht: str | None = None, romanized_title: str = "",
    group_chs: str = "", group_cht: str | None = None, bgm_id: int | None = None,
    anime_id: str | None = None, production: Mapping[str, Any] | None = None,
    publish: Mapping[str, Any] | None = None, replace: bool = False,
) -> SeriesMetadata:
    """Create one strictly validated series.json and return its loaded metadata."""
    folder_name = _series_folder_name(series_folder_name)
    payload = _normalize_payload({
        "schema_version": SERIES_SCHEMA_VERSION,
        "series": {
            "title_chs": title_chs, "title_cht": title_cht,
            "romanized_title": romanized_title, "bgm_id": bgm_id,
            "anime_id": anime_id,
            "traditionalization": {
                "status": "resolved" if title_cht and group_cht else "pending",
                "converter": "Taiwan",
                "api_url": "https://api.zhconvert.org/convert",
                "attempts": {},
            },
        },
        "groups": {"chs": group_chs, "cht": group_cht},
        "production": dict(production or {}), "publish": dict(publish or {}),
    })
    parent = (Path.home() / "Downloads" if parent_dir is None
              else Path(parent_dir).expanduser()).resolve()
    target = parent / folder_name / "bgminfo" / "series.json"
    _atomic_write_series(target, _serialized_payload(payload), replace=replace)
    return SeriesMetadata.load(target)


def update_series_publish_config(
    path: Path | str, values: Mapping[str, Any], *,
    credential_aliases: Mapping[str, str] | None = None,
) -> SeriesMetadata:
    """Atomically merge non-secret publication settings into an existing series file."""
    target = Path(path).expanduser().resolve()
    raw = json.loads(target.read_text(encoding="utf-8"))
    normalized = _normalize_payload(raw)
    publish = dict(normalized["publish"])
    publish.update(dict(values))
    if credential_aliases is not None:
        aliases = dict(publish.get("credential_aliases", {}))
        aliases.update(dict(credential_aliases))
        publish["credential_aliases"] = aliases
    normalized["publish"] = _normalize_publish(publish)
    _reject_secret_fields(normalized)
    _atomic_write_series(target, _serialized_payload(normalized), replace=True)
    return SeriesMetadata.load(target)


def series_metadata_questions() -> tuple[dict[str, Any], ...]:
    """Return the ordered interactive question contract without prompting."""
    return tuple(dict(item) for item in _SERIES_QUESTIONS)


def _normalize_notes(value: str | None) -> str | None:
    if value is None:
        return None
    if "\x00" in value:
        raise ValueError("publish notes must not contain NUL")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized or None


def prompt_series_metadata(
    *, input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], Any] = print,
    notes_fn: Callable[[], str | None] | None = None,
    parent_dir: Path | str | None = None, replace: bool = False,
) -> SeriesMetadata:
    """Ask the series questions, then delegate creation to create_series_metadata()."""
    output_fn(ui_text(
        "将创建 <上级目录>/<番组文件夹>/bgminfo/series.json。",
        "This will create <parent directory>/<series folder>/bgminfo/series.json.",
    ))
    answers: dict[str, Any] = {}
    for question in _SERIES_QUESTIONS:
        key = str(question["key"])
        if key == "parent_dir" and parent_dir is not None:
            continue
        label = ui_text(str(question["zh"]), str(question["en"]))
        prompt = (default_prompt(label, str(question["default"]))
                  if "default" in question else
                  (f"{label}: " if question["required"] else optional_prompt(label)))
        while True:
            value = input_fn(prompt).strip()
            if not value and "default" in question:
                value = str(question["default"])
            if not question["required"] or value:
                break
            output_fn(ui_text(
                f"{label} 不能为空，请重新输入。",
                f"{label} cannot be empty. Please try again.",
            ))
        answers[key] = value
    resolved_parent = parent_dir if parent_dir is not None else (answers.get("parent_dir") or None)
    bgm_value = answers.get("bgm_id")
    try:
        bgm_id = int(bgm_value) if bgm_value else None
    except ValueError as exc:
        raise ValueError("bgm_id must be an integer") from exc

    def json_answer(key: str) -> dict[str, Any] | None:
        value = answers.get(key)
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{key} must contain valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{key} must contain a JSON object")
        return parsed

    notes = _normalize_notes(notes_fn() if notes_fn is not None else None)
    publish = {"notes": notes} if notes else None
    return create_series_metadata(
        answers["series_folder_name"], parent_dir=resolved_parent,
        title_chs=answers["title_chs"],
        romanized_title=answers["romanized_title"],
        group_chs=answers["group_chs"],
        bgm_id=bgm_id, anime_id=answers.get("anime_id") or None,
        production=json_answer("production"), publish=publish,
        replace=replace,
    )


@dataclass(frozen=True)
class SeriesContext:
    series_root: Path
    episode_dir: Path
    episode_id: str
    metadata: SeriesMetadata

    @property
    def series_folder_name(self) -> str:
        return self.series_root.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.series_root), "folder_name": self.series_folder_name,
            "metadata_path": str(self.metadata.path), "metadata_hash": self.metadata.content_hash,
            "episode_dir": str(self.episode_dir), "episode_id": self.episode_id,
        }


def discover_series_context(episode_dir: Path | str) -> SeriesContext:
    episode = Path(episode_dir).expanduser().resolve()
    if not episode.is_dir():
        raise ValueError("episode directory does not exist")
    if not episode.name.isdigit():
        raise ValueError("episode directory name must contain digits only")
    series_root = episode.parent
    metadata_path = series_root / "bgminfo" / "series.json"
    if not metadata_path.is_file():
        raise ValueError("episode direct parent has no bgminfo/series.json")
    folder_name = series_root.name
    if not folder_name or any(character in folder_name for character in ("/", "\\", "\x00")):
        raise ValueError("series folder name is invalid")
    return SeriesContext(series_root, episode, episode.name, SeriesMetadata.load(metadata_path))


def try_discover_series_context(episode_dir: Path | str) -> SeriesContext | None:
    try:
        return discover_series_context(episode_dir)
    except ValueError:
        return None
