# bmlsub 中文文档

当前源码版本：`1.3.6`。

最新稳定 Release：[`v1.3.6`](https://github.com/microseventh/bmlsub/releases/tag/v1.3.6)。

本次变更见 [1.3.6 英文发布说明](../releases/v1.3.6.md)。

[English README](../../README.md)

[GitHub 仓库](https://github.com/microseventh/bmlsub)

- [快速开始](../quickstart.md)
- [从 GitHub Release 安装](../release-installation.md)
- [CLI 参考](../cli.md)
- [Workstation 工作流](../workstation.md)
- [独立操作与状态](../operations.md)
- [凭据与安全存储](../credentials.md)
- [发布与断点续跑](../publishing.md)
- [开发与测试](../development.md)
- [故障排查](../troubleshooting.md)
- [架构概览](../architecture.md)

命令行为以 `bmlsub --help` 为准。旧版 `workstation`、`credentials` 等命令不属于当前公开 CLI。

ASR 支持 MLX Whisper 和 Qwen3-ASR。默认完整转录依次运行 Whisper medium 整段、
Whisper large-v3-turbo 切片、Qwen3-ASR 1.7B 8-bit 整段三项任务；也可以在问答中
选择仅运行 Qwen3-ASR。安装 `[transcription]` 可选依赖即可启用这两个后端。

Qwen 默认将完整音频交给模型库，bmlsub 不额外切片；模型库内部仍会处理长音频分块，
返回的是音频块边界时间。策略、模型和时间标记说明见 [Workstation 工作流](../workstation.md)。
