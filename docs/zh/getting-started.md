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

## 4. 检查安装

```bash
bmlsub --version
bmlsub --help
command -v ffmpeg ffprobe mkvmerge ssh
```

预期包版本：

```text
bmlsub 1.0.0
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

预处理只自动接受唯一顶层源视频。交付从直接父级 `bgminfo/series.json` 继承发布命名和 Production Profile，要求正式简日 ASS 与顶层 Aegisub 字体，并允许显式单集覆盖。未传 `--confirm-external-action` 时，发布不会产生外部副作用。
