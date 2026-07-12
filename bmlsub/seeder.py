"""
远程做种 — 通过 SSH + curl 将种子添加到远程服务器的 qBittorrent

用法:
    from bmlsub import RemoteSeeder

    # 模式 1: .torrent 已在服务器上（R2 同步后）
    seeder = RemoteSeeder(ssh_alias="my-server")
    seeder.add_torrents("/path/to/downloads/")

    # 模式 2: 本地上传 .torrent 再做种
    seeder.upload_and_seed(
        torrent_paths=["01.mkv.torrent", "01.mp4.torrent"],
        remote_dir="/path/to/downloads/",
    )

凭证配置（优先级从高到低）:
    1. 构造函数参数
    2. 环境变量: QB_HOST, QB_PORT, QB_USERNAME, QB_PASSWORD, QB_DOWNLOAD_BASE
    3. 配置文件: ~/.config/bml/qb_config.json

服务器端需能通过 http://localhost:<port> 访问 qBittorrent Web API
"""

import json
import os
import re
import subprocess
import uuid
from pathlib import Path


class SeederError(Exception):
    """远程做种异常"""
    pass


class RemoteSeeder:
    """远程 qBittorrent 做种器 — 通过 SSH + curl 操控服务器上的 qBittorrent

    Parameters
    ----------
    ssh_alias : SSH 别名（~/.ssh/config 中配置，如 "my-server"）
    host : qB Web UI 主机名（在服务器上可访问的地址，默认 localhost）
    port : qB Web UI 端口（Docker 映射，默认 8081）
    username : qB 登录用户名
    password : qB 登录密码
    download_base : 服务器上视频文件所在的下载根目录
    """

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

        # 支持完整 URL（如 https://qb.example.com）或 host:port 组合
        if self.host.startswith("http://") or self.host.startswith("https://"):
            self._base_url = self.host.rstrip("/")
        else:
            self._base_url = f"http://{self.host}:{self.port}"
        self._cookie_remote: str | None = None  # 服务器上的 cookie 文件路径

    # ═══════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════

    def login(self) -> bool:
        """登录 qBittorrent Web API，在服务器上保存会话 cookie

        Returns
        -------
        True 表示登录成功
        """
        self._cookie_remote = f"/tmp/qb_cookies_{uuid.uuid4().hex[:8]}"

        cmd = (
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"-c '{self._cookie_remote}' "
            f"-X POST '{self._base_url}/api/v2/auth/login' "
            f"-d 'username={self.username}' -d 'password={self.password}'"
        )
        result = self._ssh_run(self.ssh_alias, cmd, capture=True)
        status = result.stdout.strip()

        # 检查 cookie 文件是否包含 SID（比 HTTP 状态码更可靠）
        check = self._ssh_run(
            self.ssh_alias,
            f"grep -q SID '{self._cookie_remote}' 2>/dev/null && echo OK || echo FAIL",
            capture=True,
        )

        if "OK" in check.stdout:
            print("✅ qBittorrent 登录成功")
            return True
        else:
            self._cookie_remote = None
            raise SeederError(
                f"qBittorrent 登录失败 (HTTP {status})。"
                f"请检查用户名和密码"
            )

    def logout(self) -> bool:
        """登出并清理服务器上的 cookie 文件"""
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
                pass  # logout 失败不影响
            self._cookie_remote = None
            print("🔌 已登出 qBittorrent")
        return True

    def add_torrent(self, remote_torrent_path: str | Path,
                    save_path: str | None = None,
                    skip_checking: bool = True,
                    paused: bool = False) -> bool:
        """添加单个已存在于服务器上的 .torrent 文件到 qBittorrent

        Parameters
        ----------
        remote_torrent_path : 服务器上 .torrent 文件的绝对路径
        save_path : 视频文件在服务器上的保存路径（默认 self.download_base）
        skip_checking : True = 跳过哈希校验（文件已在原位）
        paused : False = 立即开始做种

        Returns
        -------
        True 表示添加成功
        """
        remote_torrent_path = str(remote_torrent_path)
        filename = Path(remote_torrent_path).name
        save_path = (save_path or self.download_base).rstrip("/")

        if not self._cookie_remote:
            raise SeederError("未登录。请先调用 login()")

        # 先检查远程文件是否存在
        check = self._ssh_run(
            self.ssh_alias,
            f"test -f '{remote_torrent_path}' && echo OK || echo MISSING",
            capture=True,
        )
        if "MISSING" in check.stdout:
            print(f"❌ 服务器上找不到: {remote_torrent_path}")
            return False

        # 调用 qB API 添加种子
        paused_str = "true" if paused else "false"
        skip_str = "true" if skip_checking else "false"

        cmd = (
            f"curl -s -X POST "
            f"-b '{self._cookie_remote}' "
            f"-F 'torrents=@{remote_torrent_path}' "
            f"-F 'save_path={save_path}' "
            f"-F 'skip_checking={skip_str}' "
            f"-F 'paused={paused_str}' "
            f"-F 'root_folder=false' "
            f"'{self._base_url}/api/v2/torrents/add'"
        )
        result = self._ssh_run(self.ssh_alias, cmd, capture=True)

        raw = result.stdout.strip()
        ok = self._parse_add_response(raw)
        if ok:
            print(f"📥 已提交至 qBittorrent: {filename}")
        else:
            print(f"⚠️ 添加失败 {filename}: {raw}")
        return ok

    def add_magnet(self, magnet_uri: str, save_path: str | None = None,
                   skip_checking: bool = False, paused: bool = False) -> bool:
        """将磁力链接提交给远程 qBittorrent。

        qBittorrent 必须先获取磁力元数据，之后才会对 save_path 中的现有文件执行校验。
        返回 True 只表示添加请求已受理，不表示已完成校验或开始做种。
        """
        if not self._cookie_remote:
            raise SeederError("未登录。请先调用 login()")
        if not magnet_uri.startswith("magnet:?"):
            raise ValueError("不是有效的磁力链接")

        save_path = (save_path or self.download_base).rstrip("/")
        paused_str = "true" if paused else "false"
        skip_str = "true" if skip_checking else "false"
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
        result = self._ssh_run(self.ssh_alias, cmd, capture=True)
        raw = result.stdout.strip()
        ok = self._parse_add_response(raw)
        if ok:
            print("📥 磁力链接已提交至 qBittorrent")
        else:
            print(f"⚠️ 磁力添加失败: {raw}")
        return ok

    def add_magnets(self, magnet_uris: list[str], save_path: str | None = None,
                    skip_checking: bool = False, paused: bool = False) -> dict[str, bool]:
        """批量提交磁力链接到远程 qBittorrent。"""
        if not magnet_uris:
            print("⚠️ 未提供磁力链接")
            return {}

        print(f"\n🔗 连接 qBittorrent ({self._base_url} via {self.ssh_alias})...")
        self.login()
        results: dict[str, bool] = {}
        try:
            for magnet_uri in magnet_uris:
                results[magnet_uri] = self.add_magnet(
                    magnet_uri,
                    save_path=save_path,
                    skip_checking=skip_checking,
                    paused=paused,
                )
        finally:
            self.logout()
        return results

    def get_torrent_statuses(self, info_hashes: list[str] | None = None,
                             names: list[str] | None = None) -> list[dict]:
        """查询远程 qBittorrent 任务状态，可按 info hash 或任务名称过滤。"""
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
            statuses = self._parse_json_response(result.stdout.strip())
        finally:
            self.logout()

        if not isinstance(statuses, list):
            raise SeederError("qBittorrent 状态接口返回的不是任务列表")
        return [
            status for status in statuses
            if (not hash_filter and not name_filter)
            or status.get("hash", "").lower() in hash_filter
            or status.get("name") in name_filter
        ]

    def add_torrents(self, remote_dir_or_paths: str | list[str | Path],
                     save_path: str | None = None,
                     skip_checking: bool = True,
                     paused: bool = False,
                     glob_pattern: str = "*.torrent") -> dict[str, bool]:
        """批量添加远程 .torrent 文件到 qBittorrent

        Parameters
        ----------
        remote_dir_or_paths : 服务器上的目录（会查找其中所有 .torrent）或文件路径列表
        save_path : 视频文件在服务器上的保存路径
        skip_checking : True = 跳过哈希校验
        paused : False = 立即开始做种
        glob_pattern : 当传入目录时，匹配 .torrent 文件的模式

        Returns
        -------
        {basename: True/False} 字典
        """
        # 解析为文件路径列表
        if isinstance(remote_dir_or_paths, (str, Path)):
            remote_dir_or_paths = str(remote_dir_or_paths)
            # 判断是目录还是文件
            if "." in Path(remote_dir_or_paths).name:
                # 像文件名
                paths = [remote_dir_or_paths]
            else:
                # 像目录 → find
                remote_dir_or_paths = remote_dir_or_paths.rstrip("/")
                result = self._ssh_run(
                    self.ssh_alias,
                    f"find '{remote_dir_or_paths}' -maxdepth 1 -name '{glob_pattern}' -type f | sort",
                    capture=True,
                )
                paths = [p for p in result.stdout.strip().split("\n") if p]
        else:
            paths = [str(p) for p in remote_dir_or_paths]

        if not paths:
            print("⚠️ 未找到 .torrent 文件")
            return {}

        print(f"\n🔗 连接 qBittorrent ({self._base_url} via {self.ssh_alias})...")
        self.login()

        print(f"📤 添加 {len(paths)} 个种子...")
        results: dict[str, bool] = {}
        try:
            for p in paths:
                ok = self.add_torrent(p, save_path=save_path,
                                      skip_checking=skip_checking,
                                      paused=paused)
                results[Path(p).name] = ok
        finally:
            self.logout()

        ok_count = sum(1 for v in results.values() if v)
        print(f"\n✅ 做种完成: {ok_count}/{len(paths)} 成功")
        return results

    def upload_and_seed(self, torrent_paths: list[str | Path],
                        remote_dir: str | None = None,
                        save_path: str | None = None,
                        skip_checking: bool = True,
                        paused: bool = False) -> dict[str, bool]:
        """SCP 上传本地 .torrent 到服务器，再添加到 qBittorrent

        Parameters
        ----------
        torrent_paths : 本地 .torrent 文件路径列表
        remote_dir : 服务器上目标目录（默认 self.download_base）
        save_path : 视频文件的保存路径（默认同 remote_dir）
        skip_checking : True = 跳过哈希校验
        paused : False = 立即开始做种

        Returns
        -------
        {basename: True/False} 字典
        """
        remote_dir = (remote_dir or self.download_base).rstrip("/")
        save_path = (save_path or remote_dir).rstrip("/")

        # Step 1: SCP 上传
        print(f"\n📤 SCP 上传 {len(torrent_paths)} 个种子到 {self.ssh_alias}:{remote_dir}/")
        uploaded = []
        for p in torrent_paths:
            p = Path(p)
            if not p.exists():
                print(f"❌ 本地文件不存在: {p}")
                continue
            try:
                subprocess.run(
                    ["scp", str(p), f"{self.ssh_alias}:{remote_dir}/"],
                    check=True, capture_output=True, text=True, timeout=30,
                )
                print(f"  ✅ {p.name}")
                uploaded.append(p)
            except subprocess.CalledProcessError as e:
                print(f"  ❌ SCP 失败 {p.name}: {e.stderr.strip()}")

        if not uploaded:
            print("⚠️ 无文件成功上传")
            return {}

        # Step 2: 添加做种
        remote_paths = [f"{remote_dir}/{p.name}" for p in uploaded]
        return self.add_torrents(remote_paths, save_path=save_path,
                                 skip_checking=skip_checking, paused=paused)

    # ═══════════════════════════════════════════════
    # 上下文管理器
    # ═══════════════════════════════════════════════

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *args):
        self.logout()
        return False

    # ═══════════════════════════════════════════════
    # 凭证加载
    # ═══════════════════════════════════════════════

    @staticmethod
    def _load_config(host, port, username, password, download_base) -> dict:
        """凭证优先级: 参数 > 环境变量 > JSON 配置文件"""
        config_file = Path.home() / ".config" / "bml" / "qb_config.json"
        file_cfg = {}
        if config_file.exists():
            try:
                with open(config_file) as f:
                    file_cfg = json.load(f)
            except (json.JSONDecodeError, PermissionError) as e:
                print(f"⚠️ 配置文件读取失败 {config_file}: {e}")

        env = os.environ

        def _to_int(v):
            if v is None:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None

        return {
            "host":          host          or env.get("QB_HOST")          or file_cfg.get("host"),
            "port":          port          or _to_int(env.get("QB_PORT")) or file_cfg.get("port"),
            "username":      username      or env.get("QB_USERNAME")      or file_cfg.get("username"),
            "password":      password      or env.get("QB_PASSWORD")      or file_cfg.get("password"),
            "download_base": download_base or env.get("QB_DOWNLOAD_BASE") or file_cfg.get("download_base"),
        }

    # ═══════════════════════════════════════════════
    # SSH
    # ═══════════════════════════════════════════════

    @staticmethod
    def _ssh_run(ssh_alias: str, cmd: str, capture: bool = False) -> subprocess.CompletedProcess:
        """通过本地 ssh 命令在远程执行"""
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

    # ═══════════════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════════════

    @staticmethod
    def _parse_json_response(raw: str):
        """从可能含 SSH 横幅的输出中提取 JSON 数组或对象。"""
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
        """解析 qBittorrent 添加种子响应

        qB 4.5-: 返回 "Ok."
        qB 4.6+: 返回 JSON {"added_torrent_ids": [...], "success_count": N, ...}

        注意：SSH 执行时 MOTD/登录横幅可能混入 stdout，
        因此需要从原始输出中提取 JSON 部分。
        """
        if not raw:
            return False
        if raw == "Ok.":
            return True
        # 尝试从原始输出中提取 JSON（处理 SSH 横幅污染）
        match = re.search(r'\{.*}', raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                # success_count > 0 或 added_torrent_ids 非空即成功
                if data.get("success_count", 0) > 0:
                    return True
                if data.get("added_torrent_ids"):
                    return True
            except json.JSONDecodeError:
                pass
        return False
