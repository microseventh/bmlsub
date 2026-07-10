"""
发布 — qBittorrent 远程做种 + anibt.net API 发布
"""

import base64
import json
import os
import urllib.parse
from pathlib import Path


class PublishError(Exception):
    """发布异常"""
    pass


class Publisher:
    """发布管理器"""

    # ═══════════════════════════════════════════════
    # qBittorrent 做种
    # ═══════════════════════════════════════════════

    @staticmethod
    def seed_qbittorrent(host: str,
                          files: list[Path],
                          torrent_base_dir: Path | None = None,
                          download_base: str = "/downloads",
                          username: str = "admin",
                          password: str = "") -> dict[str, bool]:
        """
        连接远程 qBittorrent，匹配 .torrent 文件并添加做种

        Parameters
        ----------
        host : "ip:port" 格式
        files : 视频文件列表
        torrent_base_dir : .torrent 文件所在目录（默认与视频同目录）
        download_base : Docker 容器内下载路径
        username, password : qB 登录凭证

        Returns
        -------
        {filename: True/False} — 每个文件的添加状态
        """
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
                if torrent_base_dir:
                    torrent_path = torrent_base_dir / (file_path.name + ".torrent")
                else:
                    torrent_path = file_path.with_name(file_path.name + ".torrent")

                if not torrent_path.exists():
                    print(f"❌ 找不到种子: {torrent_path.name}")
                    results[file_path.name] = False
                    continue

                print(f"📤 添加种子: {torrent_path.name}")
                try:
                    with open(torrent_path, "rb") as f:
                        result = qb.torrents_add(
                            torrent_files=f,
                            save_path=download_base,
                            is_skip_checking=True,
                            is_paused=False,
                        )
                    ok = result == "Ok."
                    status = "🚀 做种成功" if ok else f"⚠️ {result}"
                    print(f"  {status}")
                    results[file_path.name] = ok
                except Exception as e:
                    print(f"  ❌ 添加失败: {e}")
                    results[file_path.name] = False

                print("-" * 50)

        finally:
            qb.auth_log_out()
            print("🔌 已断开 qBittorrent")

        return results

    # ═══════════════════════════════════════════════
    # anibt.net API 发布
    # ═══════════════════════════════════════════════

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
        """
        发布到 anibt.net

        支持两种方式：
        - 方式一（JSON + magnet）：传入 magnet_base64 或 torrent_path（自动提取 magnet）
        - 方式二（上传 .torrent 文件）：传入 torrent_path + use_torrent_file=True

        Parameters
        ----------
        bgm_id : Bangumi 条目 ID
        title : 发布标题（含组名、番名、集数、分辨率、编码等完整信息）
        episode_key : 集数标识，如 "11"
        torrent_path : 本地 .torrent 文件路径
        magnet_base64 : Base64 编码的 magnet URI（方式一，与 torrent_path 二选一）
        resolution : 分辨率，如 "1080p"
        languages : 语言标签列表，如 ["CHS","CHT","JP"]
        subtitle : 字幕类型，如 "INTERNAL"
        fmt : 格式，如 "MKV"
        file_size : 文件大小（字节），方式一时 None = 自动从 torrent 读取
        notes : 发布说明（Markdown）
        trackers : Tracker 列表，None 时默认空列表
        token : API Token，None 时从配置读取
        api_url : API 地址，None 时从配置读取
        use_torrent_file : True = 直接上传 .torrent 文件（方式二）

        Returns
        -------
        API 响应的 JSON dict

        凭证配置（优先级从高到低）:
            1. 函数参数 token / api_url
            2. 环境变量: ANIBT_TOKEN / ANIBT_API_URL
            3. 配置文件: ~/.config/bml/anibt_config.json
        """
        cfg = Publisher._load_anibt_config(token, api_url)
        token = cfg["token"]
        api_url = cfg["api_url"]

        if not token:
            raise PublishError("缺少 API Token。请通过参数、环境变量 ANIBT_TOKEN "
                               "或 ~/.config/bml/anibt_config.json 提供")

        # ── 解析数据来源 ──
        if torrent_path:
            torrent_path = Path(torrent_path)
            if not torrent_path.exists():
                raise FileNotFoundError(f"种子文件不存在: {torrent_path}")

            if use_torrent_file:
                # 方式二：直接上传 .torrent 文件（multipart）
                return Publisher._publish_torrent_file(
                    api_url=api_url, token=token,
                    torrent_path=torrent_path,
                    bgm_id=bgm_id, title=title, episode_key=episode_key,
                    resolution=resolution, languages=languages or [],
                    subtitle=subtitle, fmt=fmt, notes=notes,
                )

            # 方式一：提取 magnet 以 JSON 提交
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

        if languages is None:
            languages = []
        if trackers is None:
            trackers = []

        # ── 构建请求 ──
        payload = {
            "bgmId": bgm_id,
            "title": title,
            "magnetBase64": magnet_base64,
            "episodeKey": episode_key,
            "resolution": resolution,
            "language": languages,
            "subtitle": subtitle,
            "format": fmt,
            "fileSize": file_size,
            "notes": notes,
            "trackers": trackers,
        }

        import requests

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        print(f"🌐 POST {api_url}")
        print(f"   bgmId={bgm_id} episode={episode_key}")

        try:
            resp = requests.post(
                api_url, headers=headers, json=payload, timeout=60,
            )
            print(f"   HTTP {resp.status_code}")
            data = resp.json()
            resp.raise_for_status()
            print("✅ 发布成功")
            return data
        except requests.exceptions.RequestException as e:
            raise PublishError(f"API 发布失败: {e}") from e

    # ═══════════════════════════════════════════════
    # Magnet 构造
    # ═══════════════════════════════════════════════

    @staticmethod
    def _build_magnet(info_hash: str, name: str,
                      file_size: int, trackers: list[str]) -> str:
        """构造 magnet URI"""
        params = [
            f"xt=urn:btih:{info_hash}",
            f"dn={urllib.parse.quote(name, safe='')}",
            f"xl={file_size}",
        ]
        for tr in trackers:
            params.append(f"tr={urllib.parse.quote(tr, safe='')}")

        return "magnet:?" + "&".join(params)

    @staticmethod
    def _publish_torrent_file(api_url, token, torrent_path, bgm_id, title,
                              episode_key, resolution, languages, subtitle,
                              fmt, notes) -> dict:
        """方式二：multipart 上传 .torrent 文件"""
        import requests

        # languages → 逗号分隔字符串
        lang_str = ",".join(languages)

        data = {
            "bgmId": str(bgm_id),
            "title": title,
            "episodeKey": episode_key,
            "resolution": resolution,
            "language": lang_str,
            "subtitle": subtitle,
            "format": fmt,
        }
        if notes:
            data["notes"] = notes

        print(f"📤 上传种子文件: {torrent_path.name}")
        print(f"🌐 POST {api_url} (multipart)")
        print(f"   bgmId={bgm_id} episode={episode_key}")

        try:
            with open(torrent_path, "rb") as f:
                resp = requests.post(
                    api_url,
                    headers={"Authorization": f"Bearer {token}"},
                    files={"torrent": (torrent_path.name, f, "application/x-bittorrent")},
                    data=data,
                    timeout=60,
                )
            print(f"   HTTP {resp.status_code}")
            data = resp.json()
            resp.raise_for_status()
            print("✅ 发布成功")
            return data
        except requests.exceptions.RequestException as e:
            raise PublishError(f"API 发布失败: {e}") from e

    @staticmethod
    def _read_torrent_info(torrent_path: Path) -> dict:
        """从 .torrent 文件读取 info_hash、名称、大小、trackers"""
        import libtorrent as lt

        info = lt.torrent_info(str(torrent_path))
        return {
            "info_hash": str(info.info_hashes().v1),
            "name": info.name(),
            "total_size": info.total_size(),
            "trackers": [t.url for t in info.trackers()],
        }

    # ═══════════════════════════════════════════════
    # 凭证加载
    # ═══════════════════════════════════════════════

    @staticmethod
    def _load_anibt_config(token=None, api_url=None) -> dict:
        """凭证优先级: 参数 > 环境变量 > JSON 配置文件"""
        config_file = Path.home() / ".config" / "bml" / "anibt_config.json"
        file_cfg = {}
        if config_file.exists():
            try:
                with open(config_file) as f:
                    file_cfg = json.load(f)
            except (json.JSONDecodeError, PermissionError) as e:
                print(f"⚠️ 配置文件读取失败 {config_file}: {e}")

        env = os.environ
        return {
            "token":   token   or env.get("ANIBT_TOKEN")   or file_cfg.get("token"),
            "api_url": api_url or env.get("ANIBT_API_URL") or file_cfg.get("api_url", "https://anibt.net/api/releases/publish"),
        }
