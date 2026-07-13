"""
bmlsub 命令行入口已移除。

请改用 Python API / notebook：

    from bmlsub import Pipeline, PipelineConfig, R2Uploader
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise RuntimeError(
        "bmlsub 的 CLI 已移除；请改用 Python API、notebook 或自定义脚本调用。"
    )


if __name__ == "__main__":
    raise SystemExit(main())
