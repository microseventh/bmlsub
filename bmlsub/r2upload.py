"""
Cloudflare R2 上传 — S3 兼容 API，自动分片上传

用法:
    from bmlsub import R2Uploader

    uploader = R2Uploader(bucket_name="bml-releases")
    uploader.upload_file("01_HEVC10bit.mkv", "不虐待我的继母与继姐/01/")

    # 上传后通知服务器拉取
    uploader.sync_to_server(
        ssh_alias="us-vps",
        remote_dir="/opt/qb/downloads/",
        r2_prefix="不虐待我的继母与继姐/01/",
    )

凭证配置（优先级从高到低）:
    1. 构造函数参数
    2. 环境变量: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
    3. 配置文件: ~/.config/bml/r2_config.json

服务器端需安装 rclone 并配置 R2 remote:
    rclone config  # 创建名为 'r2' 的 S3 兼容 remote
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .progress import SpeedMeter, _fmt_size


class R2UploadError(Exception):
    """R2 上传异常"""
    pass


class R2Uploader:
    """Cloudflare R2 上传器 — 上传文件到 R2，自动分片"""

    ENDPOINT_TEMPLATE = "https://{account_id}.r2.cloudflarestorage.com"
    MULTIPART_THRESHOLD = 50 * 1024 * 1024   # 50 MB 以上用分片
    MULTIPART_CHUNKSIZE = 50 * 1024 * 1024   # 每片 50 MB
    MAX_CONCURRENCY = 3                       # 最多 3 片并发
    MAX_RETRIES = 3                           # 失败重试次数

    def __init__(self, account_id=None, access_key_id=None,
                 secret_access_key=None, bucket_name=None, endpoint=None):
        """
        Parameters
        ----------
        account_id : Cloudflare Account ID（Dashboard 首页 URL 可见）
        access_key_id : R2 API Token Access Key ID
        secret_access_key : R2 API Token Secret Access Key
        bucket_name : R2 存储桶名称
        endpoint : 自定义 S3 端点（默认自动拼接 R2 端点）
        """
        import boto3
        from botocore.config import Config

        cfg = self._load_config(account_id, access_key_id, secret_access_key,
                                bucket_name, endpoint)

        for required in ("account_id", "access_key_id", "secret_access_key", "bucket_name"):
            if not cfg[required]:
                raise R2UploadError(
                    f"缺少必要凭证: {required}。请通过参数、环境变量或 "
                    f"~/.config/bml/r2_config.json 提供"
                )

        self.account_id = cfg["account_id"]
        self.bucket_name = cfg["bucket_name"]
        self.endpoint = cfg["endpoint"] or self.ENDPOINT_TEMPLATE.format(
            account_id=self.account_id
        )

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            config=Config(
                retries={"max_attempts": self.MAX_RETRIES, "mode": "adaptive"},
            ),
        )

        # 跟踪上传的文件哈希，供 sync_to_server 校验用
        self._hashes: dict[str, str] = {}

    # ═══════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════

    def upload_file(self, local_path: str | Path, remote_key: str = None,
                    progress: bool = True) -> str:
        """
        上传单个文件

        Parameters
        ----------
        local_path : 本地文件路径
        remote_key : R2 上的对象 key（如 "番剧名/01/01_HEVC10bit.mkv"）
                     默认与本地文件名相同
        progress : 是否打印进度

        Returns
        -------
        R2 对象 key
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"文件不存在: {local_path}")

        if remote_key is None:
            remote_key = local_path.name

        file_size = local_path.stat().st_size
        if progress:
            print(f"📤 上传: {local_path.name} ({file_size / 1024 / 1024:.0f} MB)")

        # 先算哈希（上传前）
        local_hash = self._sha256_local(local_path)

        if file_size < self.MULTIPART_THRESHOLD:
            result = self._upload_small(local_path, remote_key)
        else:
            result = self._upload_large(local_path, remote_key)

        # 记录哈希
        self._hashes[remote_key] = local_hash

        if progress:
            print(f"✅ 上传完成: {remote_key}")

        return result

    def upload_files(self, paths: list[str | Path],
                     remote_folder: str = "",
                     progress: bool = True) -> list[str]:
        """
        批量上传

        Parameters
        ----------
        paths : 本地文件列表
        remote_folder : R2 上的目标文件夹（如 "不虐待我的继母与继姐/01/"）

        Returns
        -------
        成功上传的 R2 key 列表
        """
        remote_folder = remote_folder.strip("/")
        results = []

        for p in paths:
            p = Path(p)
            key = f"{remote_folder}/{p.name}" if remote_folder else p.name
            try:
                self.upload_file(p, key, progress=progress)
                results.append(key)
            except Exception as e:
                if progress:
                    print(f"❌ 跳过 {p.name}: {e}")

        return results

    def sync_to_server(self, ssh_alias: str, remote_dir: str,
                       r2_prefix: str = "", delete_after: bool = True) -> bool:
        """
        通过 SSH 让服务器从 R2 同步文件 → 校验 → 删除 R2

        .. note::
            服务器端需安装 rclone 并配置名为 'r2' 的 remote

        Parameters
        ----------
        ssh_alias : SSH 别名（~/.ssh/config 中配置，如 "us-vps"）
        remote_dir : 服务器上的目标目录（如 "/opt/qb/downloads/"）
        r2_prefix : R2 上前缀，只同步该前缀下的文件
        delete_after : 校验通过后是否删除 R2 文件

        Returns
        -------
        True 表示全部校验通过
        """
        remote_dir = remote_dir.rstrip("/")
        r2_prefix = r2_prefix.strip("/")

        rclone_src = f"r2:{self.bucket_name}"
        if r2_prefix:
            rclone_src += f"/{r2_prefix}"

        # ── Step 1: rclone sync ──
        print(f"\n🔄 服务器同步: {rclone_src} → {ssh_alias}:{remote_dir}/")
        rclone_cmd = f"export LANG=en_US.utf8 && rclone sync '{rclone_src}' '{remote_dir}/' --progress"
        self._ssh_run(ssh_alias, rclone_cmd)

        # ── Step 2: 逐文件 SHA-256 校验 ──
        print(f"\n🔍 校验文件完整性...")
        r2_files = self.list_remote(r2_prefix)
        if not r2_files:
            print("⚠️ R2 上无文件，跳过校验")
            return True

        # 安全检查：如果没有本地哈希记录且要删除，直接拒绝
        if delete_after and not self._hashes:
            print("🛑 无本地哈希记录，无法校验完整性，拒绝删除 R2 文件。")
            print("   请在同一 R2Uploader 实例上先调用 upload_files()，")
            print("   或设置 delete_after=False 跳过删除。")
            return False

        all_ok = True
        for key in r2_files:
            filename = Path(key).name

            # 本地哈希（upload_file 时记录）
            local_hash = self._hashes.get(key)
            if not local_hash:
                print(f"  ⚠️ {filename}: 无本地哈希记录，跳过")
                all_ok = False
                continue

            # 服务器哈希
            remote_path = f"{remote_dir}/{filename}"
            result = subprocess.run(
                ["ssh", ssh_alias, f"sha256sum '{remote_path}'"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                print(f"  ❌ {filename}: 远程文件不存在或无法读取")
                all_ok = False
                continue

            remote_hash = result.stdout.split()[0]
            if local_hash == remote_hash:
                print(f"  ✅ {filename}")
            else:
                print(f"  🚨 {filename} 哈希不一致！")
                all_ok = False

        # ── Step 3: 删除 R2 ──
        if delete_after and all_ok:
            print(f"\n🗑️  清理 R2 ({len(r2_files)} 个文件)...")
            for key in r2_files:
                self.delete_remote(key)
                print(f"  已删除: {key}")
            self._hashes.clear()
        elif not all_ok:
            print(f"\n⚠️ 校验未通过，保留 R2 文件")

        return all_ok

    def list_remote(self, prefix: str = "") -> list[str]:
        """
        列出 R2 上的文件

        Parameters
        ----------
        prefix : 目录前缀，空字符串列出所有

        Returns
        -------
        对象 key 列表
        """
        prefix = prefix.strip("/")
        if prefix:
            prefix += "/"

        try:
            resp = self.client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
            )
        except Exception as e:
            raise R2UploadError(f"列文件失败: {e}") from e

        contents = resp.get("Contents", [])
        # 过滤掉目录标记
        return [o["Key"] for o in contents if not o["Key"].endswith("/")]

    def delete_remote(self, key: str) -> bool:
        """
        删除 R2 上的文件

        Returns
        -------
        True 表示删除成功
        """
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as e:
            raise R2UploadError(f"删除失败 {key}: {e}") from e

    # ═══════════════════════════════════════════════
    # 凭证加载
    # ═══════════════════════════════════════════════

    @staticmethod
    def _load_config(account_id, access_key_id, secret_access_key,
                     bucket_name, endpoint) -> dict:
        """凭证优先级: 参数 > 环境变量 > JSON 配置文件"""
        config_file = Path.home() / ".config" / "bml" / "r2_config.json"
        file_cfg = {}
        if config_file.exists():
            try:
                with open(config_file) as f:
                    file_cfg = json.load(f)
            except (json.JSONDecodeError, PermissionError) as e:
                print(f"⚠️ 配置文件读取失败 {config_file}: {e}")

        env = os.environ
        return {
            "account_id":       account_id       or env.get("R2_ACCOUNT_ID")       or file_cfg.get("account_id"),
            "access_key_id":    access_key_id    or env.get("R2_ACCESS_KEY_ID")    or file_cfg.get("access_key_id"),
            "secret_access_key": secret_access_key or env.get("R2_SECRET_ACCESS_KEY") or file_cfg.get("secret_access_key"),
            "bucket_name":      bucket_name      or env.get("R2_BUCKET_NAME")      or file_cfg.get("bucket_name"),
            "endpoint":         endpoint         or env.get("R2_ENDPOINT")         or file_cfg.get("endpoint"),
        }

    # ═══════════════════════════════════════════════
    # 上传实现
    # ═══════════════════════════════════════════════

    def _upload_small(self, local_path: Path, key: str) -> str:
        """小于 50MB: 单次 PUT"""
        self.client.upload_file(str(local_path), self.bucket_name, key)
        return key

    def _upload_large(self, local_path: Path, key: str) -> str:
        """大于等于 50MB: multipart 分片上传（带实时网速）"""
        from boto3.s3.transfer import TransferConfig

        config = TransferConfig(
            multipart_threshold=self.MULTIPART_THRESHOLD,
            multipart_chunksize=self.MULTIPART_CHUNKSIZE,
            max_concurrency=self.MAX_CONCURRENCY,
        )

        file_size = local_path.stat().st_size
        meter = SpeedMeter()
        last_pct = [0]

        def _progress_cb(bytes_transferred):
            meter.add_bytes(bytes_transferred - meter._total_bytes
                           if meter._total_bytes else bytes_transferred)
            pct = int(bytes_transferred / file_size * 100)
            if pct >= last_pct[0] + 10 or pct == 100:
                print(f"  ⏳ {key}: {pct}% "
                      f"({_fmt_size(bytes_transferred)}) "
                      f"⚡ {meter.avg_speed_str}")
                last_pct[0] = pct

        self.client.upload_file(
            str(local_path), self.bucket_name, key,
            Config=config,
            Callback=_progress_cb,
        )
        return key

    # ═══════════════════════════════════════════════
    # SSH
    # ═══════════════════════════════════════════════

    @staticmethod
    def _ssh_run(ssh_alias: str, cmd: str) -> subprocess.CompletedProcess:
        """通过本地 ssh 命令在远程执行"""
        try:
            return subprocess.run(
                ["ssh", ssh_alias, cmd],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise R2UploadError(f"SSH 远程命令失败 ({ssh_alias}): {e}") from e

    # ═══════════════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════════════

    @staticmethod
    def _sha256_local(filepath: Path) -> str:
        """计算本地文件 SHA-256"""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
