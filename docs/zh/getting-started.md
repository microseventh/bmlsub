# 安装与快速开始

[English](../getting-started.md) · [文档首页](README.md)

`bmlsub` 当前面向 macOS / Apple Silicon，要求 Python 3.10 或更高版本。推荐使用 Conda base。

## 1. 启用 Python 环境

```bash
conda activate base
python --version
```

## 2. 安装系统程序

pip 会安装全部 Python 依赖，但媒体处理和远端操作还会调用系统可执行程序。

### FFmpeg 与 ffprobe

使用本项目指定的 Homebrew FFmpeg tap 公式：

```bash
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
```

该公式同时提供 `ffmpeg` 和 `ffprobe`。安装后检查 Homebrew 是否识别为已安装并 linked：

```bash
brew info homebrew-ffmpeg/ffmpeg/ffmpeg
```

预期包含类似输出：

```text
==> Installed Versions
homebrew-ffmpeg/ffmpeg/ffmpeg <version> (...) [Linked]
```

然后检查两个命令：

```bash
ffmpeg -version
ffprobe -version
```

`ffmpeg` 用于音频/字幕提取、转录切片、HEVC 编码和 H.264 硬字幕；`ffprobe` 用于视频登记、轨道检查和输出验证。

### MKVToolNix

```bash
brew install mkvtoolnix
mkvmerge --version
```

`mkvmerge` 用于 Matroska 简繁内封、轨道识别和附件验证。

### SSH

直接使用 macOS 自带 SSH，不需要通过 Homebrew 安装：

```bash
/usr/bin/ssh -V
ssh -V
```

SSH 用于 remote pull、qBittorrent SSH 隧道和有界远端 probe。

## 3. 安装 bmlsub

最简单的支持方式会同时安装代码和全部 Python 功能依赖：

```bash
python -m pip install git+https://github.com/microseventh/bmlsub.git
```

默认依赖已经包含 requests、fonttools、xxhash、MLX Whisper、libtorrent、boto3 和 keyring，不再需要 extras selector。

本地 editable 安装：

```bash
cd /path/to/bmlsub
python -m pip install -e .
```

## 4. Workstation 快速模式

普通用户从番组根目录启动，只需要记住三个命令：

```bash
# Workstation 交互式快速模式
bmlsub workstation start

# Workstation 交互式外部交付
bmlsub workstation start delivery

# 外部交付无人值守模式
bmlsub workstation start delivery -y
```

### Workstation 交互式快速模式

`bmlsub workstation start` 会自动使用当前目录作为番组根目录，并在交互界面中：

1. 检查或引导创建 `bgminfo/series.json`；
2. 列出数字单集目录并选择本集；
3. 自动识别预处理、人工翻译交接、本地生产或发布阶段；
4. 在预处理阶段选择快速模式、完整模式或不转录；
5. 在本地生产阶段选择完整产品、仅 MKV、仅 MP4 或自定义范围；
6. 在执行前展示摘要并确认。

普通用户不需要手动填写 `--series-root`、`--episode-id`、`--execute`、`--transcription`、`--delivery-scope` 等底层参数。这些参数仅用于高级自动化和 CLI 参考。快速模式不会越过人工翻译边界，也不会自动开始外部发布。

### Workstation 交互式外部交付

本地产品和 Torrent 完成后运行：

```bash
bmlsub workstation start delivery
```

命令会选择单集并检查 Credential Manifest、macOS Keychain 中的 R2/qB/Anibt Profile、SSH alias、VPS 宿主机目录和 qB 容器目录。默认只打印一次简洁摘要，然后按以下顺序逐产品确认：

```text
全部 R2 上传 → 全部 VPS 拉取 → 全部 qB 做种 → 全部 Anibt 发布
```

首次配置、替换或修复凭证时使用交互选项 `--configure`；需要查看每个文件的完整路径映射时使用 `--verbose-plan`。有效 Stage 指纹和 receipt 会自动复用。

### 外部交付无人值守模式

已有完整且有效的 Manifest、Keychain Profile、SSH 和公开路径配置后，可以运行：

```bash
bmlsub workstation start delivery -y
```

`-y/--yes` 会跳过外部交付确认，按同样的 R2 → VPS → qB → Anibt 顺序执行。它会重新评估所有 Stage，但复用有效指纹和 receipt；因此 `-y` 不等于 `--force`，不会无条件重复上传或发布。缺失或无效凭证会返回 `needs_review`，不会在无人值守模式中要求输入 Secret。

如果当前终端仍可交互，命令可以让用户选择单集；真正的非交互自动化在番组包含多个单集时必须通过高级 CLI 接口明确指定单集。普通交互式快速模式仍不要求用户手工填写这些参数。

失败后重新运行时，默认重新评估完整交付链并复用有效结果。`--resume` 表达从已有有效状态继续，`--restart` 表达从第一阶段重新评估；两者都不会自动删除 R2 对象、VPS 文件、qB 数据或撤回 Anibt 发布。


## 5. 检查安装

```bash
bmlsub --version
bmlsub --help
command -v ffmpeg ffprobe mkvmerge ssh
```

预期包版本：

```text
bmlsub 1.1.3
```

如果 `ffmpeg` 或 `ffprobe` 指向异常位置，请先通过 `brew info homebrew-ffmpeg/ffmpeg/ffmpeg` 确认 tap 公式已 linked，再检查 `brew --prefix` 和 `PATH`。

## 5. 创建番组配置

```bash
bmlsub workstation series create \
  --parent-dir /path/to/series-parent \
  --series-folder-name ExampleSeries \
  --title-chs 示例番组 --title-cht 示例番組 \
  --romanized-title ExampleSeries \
  --group-chs 示例制作组 --group-cht 示例製作組
```

省略 `--parent-dir` 时默认创建到 `~/Downloads/<番组文件夹>/bgminfo/series.json`。默认拒绝覆盖已有文件；需要替换时必须显式使用 `--replace`。也可传 `--interactive` 逐项回答问题。

## 6. 启动单集工作空间

大多数底层命令的 `--workspace` 默认是当前目录，并使用 `.bmlsub/state.sqlite3`。工作站命令把 `--workspace` 视为数字单集目录，并使用 `workstation/state/state.sqlite3` 以及 config、manifest、summary、步骤和 Artifact JSON 快照。

```bash
bmlsub asset register-video \
  --workspace /path/to/series/01 \
  --episode-id 01 \
  --video /path/to/series/01/source.mkv \
  --purpose source \
  --purpose extract

bmlsub workstation preprocess --workspace /path/to/series/01 --episode-id 01
bmlsub workstation status --workspace /path/to/series/01
```

预处理只自动接受唯一顶层源视频。交付从直接父级 `bgminfo/series.json` 继承发布命名和 Production Profile，要求正式 `<集数>.CHS&JPN.ass` 与顶层 Aegisub 字体，并允许显式单集覆盖。Workstation 随后检查可选的正式 `<集数>.CHT&JPN.ass`（文件名大小写不敏感）：存在时直接登记，并用于 `h264-cht` 成品和 MKV 的繁体字幕轨；不存在时，才通过配置的台湾繁化服务从简体字幕生成繁体字幕。未传 `--confirm-external-action` 时，发布不会产生外部副作用。
