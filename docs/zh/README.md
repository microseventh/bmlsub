# bmlsub 1.1.2 中文指南

`bmlsub` 是面向 macOS / Apple Silicon 的本地 Headless 媒体生产核心。公开入口是 `bmlsub` CLI、`Pipeline` Python API 和 `CredentialService`。本页描述本版本已经实现的能力，不包含规划中的功能。

[English README](../../README.md)

## 从 GitHub 全量安装

远程仓库同步后，可用一条命令安装代码和全部 Python 可选依赖：

```bash
conda activate base
python -m pip install git+https://github.com/microseventh/bmlsub.git
```

该命令默认安装全部 Python 功能依赖。系统工具 `ffmpeg`、`ffprobe`、`mkvmerge` 和 macOS 自带 `ssh` 不由 pip 安装，详见[安装与快速开始](getting-started.md)。

## 当前实际功能

| 领域 | 已实现公开能力 |
|---|---|
| 字幕兼容入口 | 校验 CHS/CHT ASS，ASS 感知简转繁，显式高风险整文件模式 |
| 素材 | 视频、ASS/SRT、字体、章节、附件登记；查询、候选匹配和明确确认 |
| 媒体 | ffprobe 轨道清单；归档/转录音频、文本字幕轨和全部附件提取 |
| 转录 | MLX Whisper `direct`、`chunked`、`both` |
| ASS | `ass-analysis-v4` 分析、显式规范化、字体需求、OP/ED/IN 折叠和标准 ASS 重建 |
| ProductionRequest | `encode + hevc-10bit`、`hardsub + h264-chs/h264-cht`、`mux_subtitle + mkv-subtitle` |
| Release | libtorrent torrent、R2 上传、SSH+rclone 拉取、qBittorrent 做种、Anibt preview/正式发布 |
| Credentials | Login Keychain 的 R2/qB/Anibt；OpenSSH profile；remote-pull profile；CRUD/status/validate/probe |
| 状态与恢复 | Run、Stage、Artifact、ProductionRequest、SQLite、指纹、stale、事务化文件提交和安全复用 |
| 工作站 | 番组配置继承、三阶段单集流程、真实 delivery 单步骤、非阻断字体诊断和发布批次快照 |

虽然 `ProductionOperation` 模型中存在 `remux` 枚举，但当前 CLI 不接受该 operation，`normalize_profile()` 也不允许执行；因此 1.1.2 不提供独立 remux 功能。

## 最小本地流程

```bash
bmlsub asset register-video \
  --workspace /path/to/workspace \
  --episode-id 01 \
  --video /path/to/source/01.mkv \
  --purpose source \
  --purpose extract

bmlsub asset list --workspace /path/to/workspace --episode-id 01
```

命令输出一个 JSON 文档。后续操作使用返回的 `artifact_id`，而不是扫描目录猜测文件。

## 当前实现边界

- 没有通用的 episode/Artifact/输出跨进程任务锁；同一输出不应由多个进程并发执行。Credential manifest 自身有 `flock` 锁，SQLite 写入和正式文件替换也各有自己的串行化/原子边界。
- 默认字幕转换 Provider 每次执行一次同步 HTTP 请求，没有自动重试或指数退避。
- 外部已有文件不会仅因存在而自动登记为历史成功 Stage。
- `run show`/`Pipeline.get_run()` 是只读查询；没有取消、暂停、继续或队列控制 API。
- 没有 GUI、daemon、任务队列或 Remote Worker。
- 文件原子提交与 SQLite Artifact 登记不是一个跨系统原子事务；登记失败时 Stage 失败，已提交文件不会被当作可复用成功。

## 文档

| 主题 | 当前实现手册 |
|---|---|
| 安装、extras 和系统工具 | [安装与快速开始](getting-started.md) |
| 状态、Artifact、复用与当前限制 | [核心概念](concepts.md) |
| 逐命令参数与副作用 | [CLI 手册](cli.md) |
| `Pipeline` 和 `CredentialService` | [Python API](python-api.md) |
| Manifest、Keychain 和 profile | [凭证配置](credentials.md) |
| Secret、文件和外部操作边界 | [安全边界](security.md) |
| 包与执行结构 | [架构](architecture.md) |
| 测试和开发 | [开发与测试](development.md) |
| 发布 Profile 与链路 | [发布](release.md) |
| 按需恢复和单步骤媒体生产 | [工作站快速模式](workstation-quick-mode.md) |

## 架构决策记录

[ADR 0001](adr/0001-reliability-core.md) 记录可靠性内核的正式架构决策。当前行为以包源码和上表手册为准。

CLI stdout 为一个最终 JSON；附带输出与 Diagnostic 写入 stderr。退出码为：普通成功/复用 `0`、失败 `1`、`needs_review` `2`。

许可证：MIT。
