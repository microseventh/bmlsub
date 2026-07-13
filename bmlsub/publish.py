"""
发布 — 本地 qBittorrent 做种辅助 + anibt.net API 发布
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


class PublishError(Exception):
    """发布异常"""
    pass


@dataclass(frozen=True)
class ReleasePlan:
    """单个发布任务计划。"""

    title: str
    episode_key: str
    torrent_path: Path | None
    resolution: str
    languages: tuple[str, ...]
    fmt: str
    subtitle: str
    mode: str

    def summary(self) -> dict:
        return {
            "title": self.title,
            "episode_key": self.episode_key,
            "torrent_path": str(self.torrent_path) if self.torrent_path else None,
            "resolution": self.resolution,
            "languages": list(self.languages),
            "format": self.fmt,
            "subtitle": self.subtitle,
            "mode": self.mode,
        }


class Publisher:
    """发布管理器。"""

    @staticmethod
    def seed_qbittorrent(host: str,
                         files: list[Path],
                         torrent_base_dir: Path | None = None,
                         download_base: str = "/downloads",
                         username: str = "admin",
                         password: str = "") -> dict[str, bool]:
        from qbittorrentapi import Client

        print(f"🔗 连接 qBittorrent: {host} ...")
        qb = Client(host=host, username=username, password=password)

        try:
            qb.auth_log_in()
            print("✅ 认证成功\n" + "=" * 50)
        except Exception as e:
            raise PublishError(f"qBittorrent 登录失败: {e}") from e

        results: dict[str, bool] = {}
        try:
            for file_path in files:
                file_path = Path(file_path)
                torrent_path = (torrent_base_dir / (file_path.name + ".torrent")) if torrent_base_dir else file_path.with_name(file_path.name + ".torrent")
                if not torrent_path.exists():
                    print(f"❌ 找不到种子: {torrent_path.name}")
                    results[file_path.name] = False
                    continue

                print(f"📤 添加种子: {torrent_path.name}")
                try:
                    with torrent_path.open("rb") as handle:
                        result = qb.torrents_add(
                            torrent_files=handle,
                            save_path=download_base,
                            is_skip_checking=True,
                            is_paused=False,
                        )
                    ok = result == "Ok."
                    print(f"  {'🚀 做种成功' if ok else f'⚠️ {result}'}")
                    results[file_path.name] = ok
                except Exception as e:
                    print(f"  ❌ 添加失败: {e}")
                    results[file_path.name] = False
                print("-" * 50)
        finally:
            qb.auth_log_out()
            print("🔌 已断开 qBittorrent")

        return results

    @staticmethod
    def build_release_plan(title: str,
                           episode_key: str,
                           torrent_path: str | Path | None,
                           resolution: str = "1080p",
                           languages: list[str] | None = None,
                           subtitle: str = "INTERNAL",
                           fmt: str = "MKV",
                           use_torrent_file: bool = False) -> ReleasePlan:
        normalized_torrent = Path(torrent_path).expanduser().resolve() if torrent_path else None
        return ReleasePlan(
            title=title,
            episode_key=episode_key,
            torrent_path=normalized_torrent,
            resolution=resolution,
            languages=tuple(languages or []),
            fmt=fmt,
            subtitle=subtitle,
            mode="torrent-file" if use_torrent_file else "json-magnet",
        )

    @staticmethod
    def publish_anibt(
        bgm_id: int,
        title: str,
        episode_key: str,
        torrent_path: str | Path | None = None,
        magnet_base64: str | None = None,
        *,
        resolution: str = "1080p",
        languages: list[str] | None = None,
        subtitle: str = "INTERNAL",
        fmt: str = "MKV",
        file_size: int | None = None,
        notes: str = "",
        trackers: list[str] | None = None,
        token: str | None = None,
        api_url: str | None = None,
        use_torrent_file: bool = False,
    ) -> dict:
        cfg = Publisher._load_anibt_config(token, api_url)
        token = cfg["token"]
        api_url = cfg["api_url"]

        if not token:
            raise PublishError(
                "缺少 API Token。请通过参数、环境变量 ANIBT_TOKEN 或 ~/.config/bml/anibt_config.json 提供"
            )

        if torrent_path:
            torrent_path = Path(torrent_path)
            if not torrent_path.exists():
                raise FileNotFoundError(f"种子文件不存在: {torrent_path}")

            if use_torrent_file:
                return Publisher._publish_torrent_file(
                    api_url=api_url,
                    token=token,
                    torrent_path=torrent_path,
                    bgm_id=bgm_id,
                    title=title,
                    episode_key=episode_key,
                    resolution=resolution,
                    languages=languages or [],
                    subtitle=subtitle,
                    fmt=fmt,
                    notes=notes,
                )

            info = Publisher._read_torrent_info(torrent_path)
            if file_size is None:
                file_size = info["total_size"]
            if trackers is None:
                trackers = info["trackers"]

            magnet = Publisher._build_magnet(
                info_hash=info["info_hash"],
                name=info["name"],
                file_size=file_size,
                trackers=trackers,
            )
            magnet_base64 = base64.b64encode(magnet.encode()).decode()
            print(f"📤 发布种子: {torrent_path.name}")
        elif not magnet_base64:
            raise PublishError("必须提供 torrent_path 或 magnet_base64 之一")

        payload = {
            "bgmId": bgm_id,
            "title": title,
            "magnetBase64": magnet_base64,
            "episodeKey": episode_key,
            "resolution": resolution,
            "language": languages or [],
            "subtitle": subtitle,
            "format": fmt,
            "fileSize": file_size,
            "notes": notes,
            "trackers": trackers or [],
        }

        import requests

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        print(f"🌐 POST {api_url}")
        print(f"   bgmId={bgm_id} episode={episode_key}")

        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=60)
            print(f"   HTTP {response.status_code}")
            data = response.json()
            response.raise_for_status()
            print("✅ 发布成功")
            return data
        except requests.exceptions.RequestException as e:
            raise PublishError(f"API 发布失败: {e}") from e

    @staticmethod
    def _build_magnet(info_hash: str, name: str,
                      file_size: int, trackers: list[str]) -> str:
        params = [
            f"xt=urn:btih:{info_hash}",
            f"dn={urllib.parse.quote(name, safe='')}",
            f"xl={file_size}",
        ]
        for tracker in trackers:
            params.append(f"tr={urllib.parse.quote(tracker, safe='')}")
        return "magnet:?" + "&".join(params)

    @staticmethod
    def _publish_torrent_file(api_url, token, torrent_path, bgm_id, title,
                              episode_key, resolution, languages, subtitle,
                              fmt, notes) -> dict:
        import requests

        data = {
            "bgmId": str(bgm_id),
            "title": title,
            "episodeKey": episode_key,
            "resolution": resolution,
            "language": ",".join(languages),
            "subtitle": subtitle,
            "format": fmt,
        }
        if notes:
            data["notes"] = notes

        print(f"📤 上传种子文件: {torrent_path.name}")
        print(f"🌐 POST {api_url} (multipart)")
        print(f"   bgmId={bgm_id} episode={episode_key}")

        try:
            with torrent_path.open("rb") as handle:
                response = requests.post(
                    api_url,
                    headers={"Authorization": f"Bearer {token}"},
                    files={"torrent": (torrent_path.name, handle, "application/x-bittorrent")},
                    data=data,
                    timeout=60,
                )
            print(f"   HTTP {response.status_code}")
            payload = response.json()
            response.raise_for_status()
            print("✅ 发布成功")
            return payload
        except requests.exceptions.RequestException as e:
            raise PublishError(f"API 发布失败: {e}") from e

    @staticmethod
    def _read_torrent_info(torrent_path: Path) -> dict:
        import libtorrent as lt

        info = lt.torrent_info(str(torrent_path))
        return {
            "info_hash": str(info.info_hashes().v1),
            "name": info.name(),
            "total_size": info.total_size(),
            "trackers": [tracker.url for tracker in info.trackers()],
        }

    @staticmethod
    def _load_anibt_config(token=None, api_url=None) -> dict:
        config_file = Path.home() / ".config" / "bml" / "anibt_config.json"
        file_cfg = {}
        if config_file.exists():
            try:
                with config_file.open() as handle:
                    file_cfg = json.load(handle)
            except (json.JSONDecodeError, PermissionError) as e:
                print(f"⚠️ 配置文件读取失败 {config_file}: {e}")

        env = os.environ
        return {
            "token": token or env.get("ANIBT_TOKEN") or file_cfg.get("token"),
            "api_url": api_url or env.get("ANIBT_API_URL") or file_cfg.get("api_url", "https://anibt.net/api/releases/publish"),
        }
