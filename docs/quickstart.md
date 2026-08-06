# 快速开始

## 安装依赖

```bash
conda create -n bmlsub python=3.12
conda activate bmlsub
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
brew install mkvtoolnix
python -m pip install -e '.[transcription]'
```

如果只执行不含 Whisper 的操作，可以省略 `[transcription]`。运行 `bmlsub --version` 确认安装版本。

## 准备目录

```text
Series/
  bgminfo/series.json       # 首次 ws start 可创建
  01/
    01.mkv
```

可从 `Series/` 或 `Series/01/` 启动命令。番组根目录必须直接包含数字单集目录。

## Workstation

```bash
cd Series
bmlsub ws start
```

首次运行按问答填写番组标题、制作组、Bangumi/Anime 身份和发布说明。创建 `01/` 后再次运行，选择是否执行预处理和转录。

人工翻译、校对和收集字体后，将正式字幕（通常为 `<episode>.chs&jpn.ass`）及字体放在单集目录，再运行：

```bash
bmlsub ws end
```

首次交付会配置非敏感路径和 Credential Profile，并逐阶段要求确认。配置完成后，已审阅过的工作区可使用：

```bash
bmlsub ws end yes
```

该模式会自动启用 Nyaa 代发；不希望启用 Nyaa 时使用交互式 `ws end`。

## 独立操作

```bash
cd /path/to/ordinary-directory
bmlsub build encode
bmlsub build torrent
bmlsub rebuild encode
```

每条命令只执行一个操作；需要修改已登记结果时使用同名 `rebuild`。
