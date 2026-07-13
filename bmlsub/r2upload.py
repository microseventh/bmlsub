"""
Cloudflare R2 上传 — S3 兼容 API，自动分片上传

用法:
    from bmlsub import R2Uploader

    uploader = R2Uploader(bucket_name="bml-releases")
    uploader.upload_file("01_HEVC10bit.mkv", "作品名/01/01_HEVC10bit.mkv")
    uploader.upload_files([
        "01_HEVC10bit.mkv",
        "[Billion Meta Lab] 作品名 [01][1080P][简日内嵌].mp4",
    ], remote_folder="作品名/01")
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class R2UploadError(Exception):
    """R2 上传异常"""
    pass


class R2Uploader:
    """Cloudflare R2 上传器 — 上传文件到 R2，自动分片。"""

    ENDPOINT_TEMPLATE = "https://{account_id}.r2.cloudflarestorage.com"
    MULTIPART_THRESHOLD = 50 * 1024 * 1024
    MULTIPART_CHUNKSIZE = 50 * 1024 * 1024
    MAX_CONCURRENCY = 3
    MAX_RETRIES = 3

    def __init__(self, account_id=None, access_key_id=None,
                 secret_access_key=None, bucket_name=None, endpoint=None):
        import boto3
        from botocore.config import Config

        cfg = self._load_config(account_id, access_key_id, secret_access_key,
                                bucket_name, endpoint)

        for required in ("account_id", "access_key_id", "secret_access_key", "bucket_name"):
            if not cfg[required]:
                raise R2UploadError(
                    f"缺少必要凭证: {required}。请通过参数、环境变量或 ~/.config/bml/r2_config.json 提供"
                )

        self.account_id = cfg["account_id"]
        self.bucket_name = cfg["bucket_name"]
        self.endpoint = cfg["endpoint"] or self.ENDPOINT_TEMPLATE.format(account_id=self.account_id)

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            config=Config(retries={"max_attempts": self.MAX_RETRIES, "mode": "adaptive"}),
        )
        self._hashes: dict[str, str] = {}

    def upload_file(self, local_path: str | Path, remote_key: str | None = None,
                    progress: bool = True) -> str:
        local_path = Path(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"文件不存在: {local_path}")

        if remote_key is None:
            remote_key = local_path.name
        remote_key = str(remote_key).strip("/")
        if not remote_key:
            raise R2UploadError("remote_key 不能为空")

        file_size = local_path.stat().st_size
        if progress:
            print(f"📤 上传: {local_path.name} ({file_size / 1024 / 1024:.0f} MB) → {remote_key}")

        local_hash = self._sha256_local(local_path)
        if file_size < self.MULTIPART_THRESHOLD:
            result = self._upload_small(local_path, remote_key)
        else:
            result = self._upload_large(local_path, remote_key)

        self._hashes[remote_key] = local_hash
        if progress:
            print(f"✅ 上传完成: {remote_key}")
        return result

    def upload_files(self, paths: list[str | Path],
                     remote_folder: str = "",
                     progress: bool = True) -> list[str]:
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

    def list_remote(self, prefix: str = "") -> list[str]:
        prefix = prefix.strip("/")
        paginator = self.client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": self.bucket_name}
        if prefix:
            kwargs["Prefix"] = prefix
        keys: list[str] = []
        for page in paginator.paginate(**kwargs):
            for item in page.get("Contents", []):
                keys.append(item["Key"])
        return keys

    def delete_remote(self, key: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=key)
            print(f"已删除: {key}")
            self._hashes.pop(key, None)
            return True
        except Exception as e:
            print(f"❌ 删除失败 {key}: {e}")
            return False

    def recorded_hashes(self) -> dict[str, str]:
        return dict(self._hashes)

    def _load_config(self, account_id, access_key_id, secret_access_key, bucket_name, endpoint):
        cfg = {
            "account_id": account_id or os.environ.get("R2_ACCOUNT_ID"),
            "access_key_id": access_key_id or os.environ.get("R2_ACCESS_KEY_ID"),
            "secret_access_key": secret_access_key or os.environ.get("R2_SECRET_ACCESS_KEY"),
            "bucket_name": bucket_name or os.environ.get("R2_BUCKET_NAME"),
            "endpoint": endpoint or os.environ.get("R2_ENDPOINT"),
        }
        config_path = Path.home() / ".config" / "bml" / "r2_config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                for key, value in data.items():
                    cfg.setdefault(key, value)
                    if not cfg.get(key):
                        cfg[key] = value
            except Exception:
                pass
        return cfg

    def _upload_small(self, local_path: Path, remote_key: str) -> str:
        self.client.upload_file(str(local_path), self.bucket_name, remote_key)
        return remote_key

    def _upload_large(self, local_path: Path, remote_key: str) -> str:
        config = {
            "multipart_chunksize": self.MULTIPART_CHUNKSIZE,
            "max_concurrency": self.MAX_CONCURRENCY,
        }
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(
            str(local_path),
            self.bucket_name,
            remote_key,
            Config=TransferConfig(**config),
        )
        return remote_key

    def _sha256_local(self, file_path: Path) -> str:
        h = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
