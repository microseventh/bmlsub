"""
旧版 croc 传输入口已移除。

请改用 Cloudflare R2：

    from bmlsub import R2Uploader
"""

from __future__ import annotations


class TransferError(RuntimeError):
    """旧版传输接口已移除。"""


class SSHConnectionError(TransferError):
    """保留旧异常名以提示迁移。"""


class HashVerificationError(TransferError):
    """保留旧异常名以提示迁移。"""


class CrocTransferError(TransferError):
    """保留旧异常名以提示迁移。"""


class Transfer:
    """兼容 stub：所有 croc/SSH 传输能力均已移除。"""

    def __init__(self, *args, **kwargs):
        raise TransferError(
            "croc/SSH 传输已从 bmlsub 移除；请改用 R2Uploader 或 Pipeline.upload_files_to_r2()。"
        )
