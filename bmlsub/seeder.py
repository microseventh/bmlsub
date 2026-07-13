"""
远程做种 — 通过 SSH + curl 操控服务器上的 qBittorrent
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class SeederError(Exception):
    """远程做种异常"""
    pass


@dataclass(frozen=True)
class SeedSubmissionResult:
    target: str
    ok: bool
    mode: str
    message: str = ""


@dataclass
class QBTaskStatus:
    name: str
    info_hash: str
    state: str
    progress: float
    size: int
    uploaded: int
    ratio: float
    upspeed: int
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error:
            return "error"
        if self.state.lower().startswith("checking"):
            return "checking"
        if self.state in {"metaDL", "forcedMetaDL"}:
            return "metadata"
        if self.progress >= 1 and self.state in {"uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP"}:
            return "seedable"
        if self.progress >= 1:
            return "complete"
        return "incomplete"

    def summary(self) -> dict:
        return {
            "name": self.name,
            "hash": self.info_hash,
            "state": self.state,
            "progress": self.progress,
            "size": self.size,
            "uploaded": self.uploaded,
            "ratio": self.ratio,
            "upspeed": self.upspeed,
            "error": self.error,
            "verdict": self.verdict,
        }


class RemoteSeeder:
    """远程 qBittorrent 做种器。"""

    def __init__(self, ssh_alias: str,
                 host: str | None = None,
                 port: int | None = None,
                 username: str | None = None,
                 password: str | None = None,
                 download_base: str | None = None):
        self.ssh_alias = ssh_alias
        cfg = self._load_config(host, port, username, password, download_base)

        self.host = cfg["host"] or "localhost"
        self.port = cfg["port"] or 8081
        self.username = cfg["username"] or "admin"
        self.password = cfg["password"] or ""
        self.download_base = cfg["download_base"] or ""

        if self.host.startswith("http://") or self.host.startswith("https://"):
            self._base_url = self.host.rstrip("/")
        else:
            self._base_url = f"http://{self.host}:{self.port}"
        self._cookie_remote: str | None = None

    def login(self) -> bool:
        self._cookie_remote = f"/tmp/qb_cookies_{uuid.uuid4().hex[:8]}"
        login_cmd = (
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"-c '{self._cookie_remote}' "
            f"-X POST '{self._base_url}/api/v2/auth/login' "
            f"--data-urlencode 'username={self.username}' "
            f"--data-urlencode 'password={self.password}'"
        )
        result = self._ssh_run(self.ssh_alias, login_cmd, capture=True)
        status = result.stdout.strip()
        check = self._ssh_run(
            self.ssh_alias,
            f"grep -q SID '{self._cookie_remote}' 2>/dev/null && echo OK || echo FAIL",
            capture=True,
        )
        if "OK" in check.stdout:
            print("✅ qBittorrent 登录成功")
            return True

        self._cookie_remote = None
        raise SeederError(f"qBittorrent 登录失败 (HTTP {status})。请检查用户名和密码")

    def logout(self) -> bool:
        if self._cookie_remote:
            try:
                self._ssh_run(
                    self.ssh_alias,
                    f"curl -s -b '{self._cookie_remote}' "
                    f"-X POST '{self._base_url}/api/v2/auth/logout' > /dev/null 2>&1; "
                    f"rm -f '{self._cookie_remote}'",
                    capture=True,
                )
            except SeederError:
                pass
            self._cookie_remote = None
            print("🔌 已登出 qBittorrent")
        return True

    def add_torrent(self, remote_torrent_path: str | Path,
                    save_path: str | None = None,
                    skip_checking: bool = True,
                    paused: bool = False) -> bool:
        return self.submit_remote_torrents(
            [remote_torrent_path],
            save_path=save_path,
            skip_checking=skip_checking,
            paused=paused,
        )[0].ok

    def add_magnet(self, magnet_uri: str, save_path: str | None = None,
                   skip_checking: bool = False, paused: bool = False) -> bool:
        return self.submit_magnets(
            [magnet_uri],
            save_path=save_path,
            skip_checking=skip_checking,
            paused=paused,
        )[0].ok

    def add_magnets(self, magnet_uris: list[str], save_path: str | None = None,
                    skip_checking: bool = False, paused: bool = False) -> dict[str, bool]:
        results = self.submit_magnets(
            magnet_uris,
            save_path=save_path,
            skip_checking=skip_checking,
            paused=paused,
        )
        return {result.target: result.ok for result in results}

    def get_torrent_statuses(self, info_hashes: list[str] | None = None,
                             names: list[str] | None = None) -> list[dict]:
        statuses = self.query_statuses(info_hashes=info_hashes, names=names)
        return [status.summary() for status in statuses]

    def query_statuses(self, info_hashes: list[str] | None = None,
                       names: list[str] | None = None) -> list[QBTaskStatus]:
        hash_filter = {value.lower() for value in (info_hashes or []) if value}
        name_filter = {value for value in (names or []) if value}

        print(f"\n🔍 查询 qBittorrent 状态 ({self._base_url} via {self.ssh_alias})...")
        self.login()
        try:
            cmd = (
                f"curl -s -G -b '{self._cookie_remote}' "
                f"--data-urlencode 'filter=all' "
                f"--data-urlencode 'sort=name' "
                f"'{self._base_url}/api/v2/torrents/info'"
            )
            result = self._ssh_run(self.ssh_alias, cmd, capture=True)
            payload = self._parse_json_response(result.stdout.strip())
        finally:
            self.logout()

        if not isinstance(payload, list):
            raise SeederError("qBittorrent 状态接口返回的不是任务列表")

        statuses = [
            QBTaskStatus(
                name=item.get("name", ""),
                info_hash=item.get("hash", ""),
                state=item.get("state", ""),
                progress=float(item.get("progress", 0)),
                size=int(item.get("size", 0) or 0),
                uploaded=int(item.get("uploaded", 0) or 0),
                ratio=float(item.get("ratio", 0) or 0),
                upspeed=int(item.get("upspeed", 0) or 0),
                error=item.get("error", ""),
            )
            for item in payload
        ]
        return [
            status for status in statuses
            if (not hash_filter and not name_filter)
            or status.info_hash.lower() in hash_filter
            or status.name in name_filter
        ]

    def add_torrents(self, remote_dir_or_paths: str | list[str | Path],
                     save_path: str | None = None,
                     skip_checking: bool = True,
                     paused: bool = False,
                     glob_pattern: str = "*.torrent") -> dict[str, bool]:
        paths = self._resolve_remote_torrent_paths(remote_dir_or_paths, glob_pattern=glob_pattern)
        results = self.submit_remote_torrents(
            paths,
            save_path=save_path,
            skip_checking=skip_checking,
            paused=paused,
        )
        return {Path(result.target).name: result.ok for result in results}

    def upload_and_seed(self, torrent_paths: list[str | Path],
                        remote_dir: str | None = None,
                        save_path: str | None = None,
                        skip_checking: bool = True,
                        paused: bool = False) -> dict[str, bool]:
        remote_dir = (remote_dir or self.download_base).rstrip("/")
        save_path = (save_path or remote_dir).rstrip("/")

        print(f"\n📤 SCP 上传 {len(torrent_paths)} 个种子到 {self.ssh_alias}:{remote_dir}/")
        uploaded: list[Path] = []
        for path in torrent_paths:
            torrent_path = Path(path)
            if not torrent_path.exists():
                print(f"❌ 本地文件不存在: {torrent_path}")
                continue
            try:
                subprocess.run(
                    ["scp", str(torrent_path), f"{self.ssh_alias}:{remote_dir}/"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                print(f"  ✅ {torrent_path.name}")
                uploaded.append(torrent_path)
            except subprocess.CalledProcessError as e:
                print(f"  ❌ SCP 失败 {torrent_path.name}: {e.stderr.strip()}")

        if not uploaded:
            print("⚠️ 无文件成功上传")
            return {}

        remote_paths = [f"{remote_dir}/{path.name}" for path in uploaded]
        return self.add_torrents(remote_paths, save_path=save_path,
                                 skip_checking=skip_checking, paused=paused)

    def submit_remote_torrents(self,
                               remote_paths: list[str | Path],
                               save_path: str | None = None,
                               skip_checking: bool = True,
                               paused: bool = False) -> list[SeedSubmissionResult]:
        if not remote_paths:
            print("⚠️ 未找到 .torrent 文件")
            return []

        save_path = (save_path or self.download_base).rstrip("/")
        paused_str = "true" if paused else "false"
        skip_str = "true" if skip_checking else "false"

        print(f"\n🔗 连接 qBittorrent ({self._base_url} via {self.ssh_alias})...")
        self.login()
        results: list[SeedSubmissionResult] = []
        try:
            for remote_path in [str(path) for path in remote_paths]:
                filename = Path(remote_path).name
                exists = self._ssh_run(
                    self.ssh_alias,
                    f"test -f '{remote_path}' && echo OK || echo MISSING",
                    capture=True,
                )
                if "MISSING" in exists.stdout:
                    print(f"❌ 服务器上找不到: {remote_path}")
                    results.append(SeedSubmissionResult(target=remote_path, ok=False, mode="torrent", message="missing"))
                    continue

                cmd = (
                    f"curl -s -X POST "
                    f"-b '{self._cookie_remote}' "
                    f"-F 'torrents=@{remote_path}' "
                    f"-F 'save_path={save_path}' "
                    f"-F 'skip_checking={skip_str}' "
                    f"-F 'paused={paused_str}' "
                    f"-F 'root_folder=false' "
                    f"'{self._base_url}/api/v2/torrents/add'"
                )
                raw = self._ssh_run(self.ssh_alias, cmd, capture=True).stdout.strip()
                ok = self._parse_add_response(raw)
                if ok:
                    print(f"📥 已提交至 qBittorrent: {filename}")
                else:
                    print(f"⚠️ 添加失败 {filename}: {raw}")
                results.append(SeedSubmissionResult(target=remote_path, ok=ok, mode="torrent", message=raw))
        finally:
            self.logout()

        ok_count = sum(1 for result in results if result.ok)
        print(f"\n✅ 做种提交完成: {ok_count}/{len(results)} 成功")
        return results

    def submit_magnets(self,
                       magnet_uris: list[str],
                       save_path: str | None = None,
                       skip_checking: bool = False,
                       paused: bool = False) -> list[SeedSubmissionResult]:
        if not magnet_uris:
            print("⚠️ 未提供磁力链接")
            return []

        save_path = (save_path or self.download_base).rstrip("/")
        paused_str = "true" if paused else "false"
        skip_str = "true" if skip_checking else "false"

        print(f"\n🔗 连接 qBittorrent ({self._base_url} via {self.ssh_alias})...")
        self.login()
        results: list[SeedSubmissionResult] = []
        try:
            for magnet_uri in magnet_uris:
                if not magnet_uri.startswith("magnet:?"):
                    raise ValueError("不是有效的磁力链接")
                cmd = (
                    f"curl -s -X POST "
                    f"-b '{self._cookie_remote}' "
                    f"--data-urlencode 'urls={magnet_uri}' "
                    f"--data-urlencode 'save_path={save_path}' "
                    f"--data-urlencode 'skip_checking={skip_str}' "
                    f"--data-urlencode 'paused={paused_str}' "
                    f"--data-urlencode 'root_folder=false' "
                    f"'{self._base_url}/api/v2/torrents/add'"
                )
                raw = self._ssh_run(self.ssh_alias, cmd, capture=True).stdout.strip()
                ok = self._parse_add_response(raw)
                if ok:
                    print("📥 磁力链接已提交至 qBittorrent")
                else:
                    print(f"⚠️ 磁力添加失败: {raw}")
                results.append(SeedSubmissionResult(target=magnet_uri, ok=ok, mode="magnet", message=raw))
        finally:
            self.logout()

        ok_count = sum(1 for result in results if result.ok)
        print(f"\n✅ 磁力提交完成: {ok_count}/{len(results)} 成功")
        return results

    def _resolve_remote_torrent_paths(self,
                                      remote_dir_or_paths: str | list[str | Path],
                                      glob_pattern: str = "*.torrent") -> list[str]:
        if isinstance(remote_dir_or_paths, (str, Path)):
            remote_value = str(remote_dir_or_paths)
            if "." in Path(remote_value).name:
                return [remote_value]
            remote_value = remote_value.rstrip("/")
            result = self._ssh_run(
                self.ssh_alias,
                f"find '{remote_value}' -maxdepth 1 -name '{glob_pattern}' -type f | sort",
                capture=True,
            )
            return [path for path in result.stdout.strip().split("\n") if path]
        return [str(path) for path in remote_dir_or_paths]

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()
        return False

    @staticmethod
    def _load_config(host, port, username, password, download_base) -> dict:
        config_file = Path.home() / ".config" / "bml" / "qb_config.json"
        file_cfg = {}
        if config_file.exists():
            try:
                with open(config_file) as handle:
                    file_cfg = json.load(handle)
            except (json.JSONDecodeError, PermissionError) as e:
                print(f"⚠️ 配置文件读取失败 {config_file}: {e}")

        env = os.environ

        def _to_int(value):
            if value is None:
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None

        return {
            "host": host or env.get("QB_HOST") or file_cfg.get("host"),
            "port": port or _to_int(env.get("QB_PORT")) or file_cfg.get("port"),
            "username": username or env.get("QB_USERNAME") or file_cfg.get("username"),
            "password": password or env.get("QB_PASSWORD") or file_cfg.get("password"),
            "download_base": download_base or env.get("QB_DOWNLOAD_BASE") or file_cfg.get("download_base"),
        }

    @staticmethod
    def _ssh_run(ssh_alias: str, cmd: str, capture: bool = False) -> subprocess.CompletedProcess:
        try:
            kwargs = {"check": True}
            if capture:
                kwargs.update({"capture_output": True, "text": True})
            return subprocess.run(["ssh", ssh_alias, cmd], **kwargs)
        except subprocess.CalledProcessError as e:
            msg = f"SSH 远程命令失败 ({ssh_alias})"
            if capture:
                msg += f": {e.stderr.strip()}" if e.stderr else f": exit {e.returncode}"
            raise SeederError(msg) from e

    @staticmethod
    def _parse_json_response(raw: str):
        if not raw:
            raise SeederError("qBittorrent 未返回 JSON 数据")
        decoder = json.JSONDecoder()
        for marker in ("[", "{"):
            start = raw.find(marker)
            if start < 0:
                continue
            try:
                value, _ = decoder.raw_decode(raw[start:])
                return value
            except json.JSONDecodeError:
                continue
        raise SeederError(f"无法解析 qBittorrent JSON 响应: {raw}")

    @staticmethod
    def _parse_add_response(raw: str) -> bool:
        if not raw:
            return False
        if raw == "Ok.":
            return True
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if data.get("success_count", 0) > 0:
                    return True
                if data.get("added_torrent_ids"):
                    return True
            except json.JSONDecodeError:
                pass
        return False
