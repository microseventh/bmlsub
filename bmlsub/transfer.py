"""
安全传输 — croc P2P 加密传输 + SSH + SHA-256 双重校验

流程:
  1. 本地计算 SHA-256 → tar.gz 打包
  2. croc 加密 P2P 传输（通过 PTY 捕获随机暗号）
  3. SSH 远程 croc receive
  4. 远程哈希校验 → 解压 → 逐文件哈希对账
  5. 自动清理两端临时文件
"""

import hashlib
import os
import pty
import re
import sys
import tarfile
import threading
import time
import subprocess
from pathlib import Path


class TransferError(Exception):
    """传输异常"""
    pass


class SSHConnectionError(TransferError):
    """SSH 连接失败"""
    pass


class HashVerificationError(TransferError):
    """哈希校验不一致"""
    pass


class CrocTransferError(TransferError):
    """croc 传输失败"""
    pass


class Transfer:
    """安全传输管理器"""

    def __init__(self, ssh_config: dict | None = None, remote_dir: str = "/opt/qb/downloads"):
        """
        Parameters
        ----------
        ssh_config : {host, port, user, key_path}
        remote_dir : 远程目标目录
        """
        self.ssh_config = ssh_config or {}
        self.remote_dir = remote_dir

    # ═══════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════

    def send_files(self, local_paths: list[str | Path],
                    verify: bool = True,
                    cleanup: bool = True) -> bool:
        """
        完整传输流程

        Returns
        -------
        True 表示全部传输并校验通过
        """
        import paramiko

        if isinstance(local_paths, (str, Path)):
            local_paths = [local_paths]
        local_paths = [Path(p) for p in local_paths]

        if not local_paths:
            raise TransferError("文件列表为空")

        archive_name = "transfer_package.tar.gz"
        local_archive = Path.cwd() / archive_name
        ssh: paramiko.SSHClient | None = None

        try:
            # ── 阶段一：本地哈希 + 打包 ──
            print("\n=== 🟡 阶段一：本地准备与哈希建档 ===")
            local_hashes: dict[str, str] = {}
            for p in local_paths:
                if p.exists():
                    name = p.name
                    h = self._sha256_local(p)
                    local_hashes[name] = h
                    print(f"💻 [{name}] → {h}")
                else:
                    raise TransferError(f"文件不存在: {p}")

            if not self._pack_tar(local_paths, local_archive):
                raise TransferError("打包失败")

            archive_hash = self._sha256_local(local_archive)
            print(f"🗜️  压缩包哈希: {archive_hash}")

            # ── 阶段二：croc 传输 ──
            print("\n=== 🟢 阶段二：Croc 加密传输 ===")
            proc, master_fd, code = self._croc_send(local_archive)

            if not code:
                raise CrocTransferError("未能获取 croc 暗号")

            ssh = self._ssh_connect()
            self._croc_receive_remote(ssh, self.remote_dir, code)

            # ── 阶段三：远程校验 ──
            if verify:
                print("\n=== 🔵 阶段三：远程校验与解压 ===")
                self._remote_verify_and_decompress(
                    ssh, archive_name, local_hashes, archive_hash
                )

            # 清理远程压缩包
            if cleanup:
                ssh.exec_command(f"rm -f '{self.remote_dir}/{archive_name}'")
                print("🗑️  远程临时文件已清理")

            return True

        except KeyboardInterrupt:
            print("\n⚠️ 用户中断传输")
            return False
        finally:
            if ssh:
                ssh.close()
            if 'local_archive' in dir() and local_archive.exists() and cleanup:
                local_archive.unlink()
                print("🗑️  本地临时文件已清理")

    # ═══════════════════════════════════════════════
    # 远程校验子流程
    # ═══════════════════════════════════════════════

    def _remote_verify_and_decompress(self, ssh, archive_name: str,
                                       local_hashes: dict[str, str],
                                       archive_hash: str) -> None:
        """阶段三：远程校验"""
        remote_archive = f"{self.remote_dir}/{archive_name}"
        remote_archive_hash = self._sha256_remote(ssh, remote_archive)
        print(f"☁️  远程压缩包哈希: {remote_archive_hash}")

        if archive_hash != remote_archive_hash:
            raise HashVerificationError("压缩包传输损坏！")

        print("🌟 压缩包校验通过，开始解压...")

        # 远程解压
        if not self._remote_decompress(ssh, self.remote_dir, archive_name):
            raise TransferError("远程解压失败")

        # 逐个文件哈希对账
        print("🔍 逐文件哈希对账...")
        all_ok = True
        for filename, local_hash in local_hashes.items():
            remote_file_hash = self._sha256_remote(ssh, f"{self.remote_dir}/{filename}")
            status = "✅" if local_hash == remote_file_hash else "🚨"
            print(f"  {status} [{filename}]")
            if local_hash != remote_file_hash:
                all_ok = False

        if all_ok:
            print("🏆 全部文件校验一致！")
        else:
            raise HashVerificationError("部分文件哈希不一致！")

    # ═══════════════════════════════════════════════
    # 内部工具
    # ═══════════════════════════════════════════════

    @staticmethod
    def _sha256_local(filepath: Path) -> str:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _sha256_remote(ssh, remote_path: str) -> str | None:
        try:
            _, stdout, stderr = ssh.exec_command(f"sha256sum '{remote_path}'")
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            if err and not out:
                return None
            return out.split()[0]
        except Exception as e:
            print(f"❌ 远程哈希异常: {e}")
            return None

    @staticmethod
    def _pack_tar(source_paths: list[Path], archive_path: Path) -> bool:
        print(f"📦 打包 → {archive_path.name}")
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                for p in source_paths:
                    tar.add(p, arcname=p.name)
                    print(f"  ➕ {p.name}")
            print("✅ 打包完成")
            return True
        except Exception as e:
            print(f"❌ 打包失败: {e}")
            return False

    @staticmethod
    def _remote_decompress(ssh, remote_dir: str, archive_name: str) -> bool:
        print("🗜️  远程解压中...")
        try:
            cmd = (f"export LANG=C.UTF-8 && cd '{remote_dir}' && "
                   f"tar -xzf '{archive_name}' -C '{remote_dir}'")
            _, stdout, stderr = ssh.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                print("✅ 远程解压成功")
                return True
            err = stderr.read().decode()
            print(f"❌ 远程解压失败: {err}")
            return False
        except Exception as e:
            print(f"❌ 远程解压异常: {e}")
            return False

    # ═══════════════════════════════════════════════
    # SSH
    # ═══════════════════════════════════════════════

    def _ssh_connect(self):
        import paramiko

        cfg = self.ssh_config
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            key = paramiko.RSAKey.from_private_key_file(cfg["key_path"])
            ssh.connect(cfg["host"], port=cfg.get("port", 22),
                         username=cfg["user"], pkey=key, timeout=10)
            print(f"✅ SSH 连接成功: {cfg['host']}")
            return ssh
        except Exception as e:
            raise SSHConnectionError(f"SSH 连接失败: {e}") from e

    # ═══════════════════════════════════════════════
    # Croc P2P 传输
    # ═══════════════════════════════════════════════

    def _croc_send(self, file_path: Path) -> tuple:
        """启动 croc send，通过 PTY 捕获随机暗号"""
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        print(f"🚀 启动 croc send (PTY)...")
        master_fd, slave_fd = pty.openpty()

        proc = subprocess.Popen(
            ["croc", "--remember", "--no-compress", "send", str(file_path)],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            text=True, close_fds=True,
        )
        os.close(slave_fd)

        code = None
        buf = ""
        deadline = time.time() + 15

        print("⏳ 解析 croc 暗号...")
        while time.time() < deadline and code is None:
            try:
                char = os.read(master_fd, 1).decode("utf-8", errors="ignore")
                if not char:
                    break
                buf += char
                if char == "\n":
                    line = buf.strip()
                    buf = ""
                    match = re.search(r'croc\s+([a-zA-Z0-9-]+)', line)
                    if match and "send" not in line and "croc-stdin" not in line:
                        code = match.group(1)
                        print(f"💥 暗号: {code}")
                        break
            except Exception:
                break

        if code:
            t = threading.Thread(target=self._drain_pty, args=(master_fd,), daemon=True)
            t.start()
        else:
            # 超时：终止 croc 子进程防止孤儿进程
            print("⚠️ croc 暗号超时，终止发送进程...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    pass

        return proc, master_fd, code

    def _croc_receive_remote(self, ssh, remote_dir: str, code: str) -> None:
        """通过 SSH 在远程执行 croc receive（带超时保护）"""
        print(f"🔗 远程 croc receive: {self.ssh_config['host']} ...")
        cmd = (f"cd '{remote_dir}' && "
               f"CROC_SECRET={code} croc --yes --remember --overwrite receive")
        _, stdout, stderr = ssh.exec_command(cmd, get_pty=True)

        channel = stdout.channel
        print("📡 传输中...\n")

        deadline = time.time() + 600  # 10 分钟超时
        while not channel.exit_status_ready():
            if time.time() > deadline:
                print("\n⏰ 远程 croc receive 超时（10 分钟）")
                channel.close()
                raise CrocTransferError("远程 croc receive 超时")
            if channel.recv_ready():
                output = channel.recv(1024).decode("utf-8", errors="ignore")
                sys.stdout.write(output)
                sys.stdout.flush()
                if "100%" in output or "complete" in output.lower():
                    print("\n🎉 远程接收完成！")
                    break
            time.sleep(0.1)

    @staticmethod
    def _drain_pty(fd: int) -> None:
        """后台排气阀：防止 PTY 缓冲区写满导致 croc 死锁"""
        try:
            while True:
                data = os.read(fd, 1024)
                if not data:
                    break
        except OSError:
            pass
