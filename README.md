# bmlsub 1.2.1

`bmlsub` 是一个面向字幕组本地工作站的字幕、转录、视频生产和发布工具。
它把每一步登记为可验证的状态和回执，并把外部网络动作放在明确的确认边界之后。

## 三个入口

```text
bmlsub ws start
bmlsub ws end [yes]
bmlsub build [option]
bmlsub rebuild [option]
```

- `ws start`：从番组初始化开始，完成源视频登记、参考字幕、音频和可选转录，停在人工字幕/字体交接处。
- `ws end`：从正式字幕和字体开始，完成本地压制，并按 R2、VPS、qBittorrent、Anibt 顺序交付。
- `ws end yes`：使用已保存配置无人值守继续交付；它会自动确认 Nyaa 代发，适合已审阅过配置的续跑。
- `build`：在当前目录只执行一个独立操作。
- `rebuild`：替换一个已有独立操作的结果；Anibt 不允许 rebuild。

全局帮助只有 `-h/--help` 和 `--version`。路径、输入范围、配方、输出位置和凭据引用在交互问答中选择，业务参数不会通过公开 CLI 传入。

## 安装

要求 Python 3.10+；Apple Silicon 的转录需要额外安装 `transcription` 依赖。

```bash
conda create -n bmlsub python=3.12
conda activate bmlsub
brew install homebrew-ffmpeg/ffmpeg/ffmpeg
brew install mkvtoolnix
python -m pip install -e '.[transcription]'
bmlsub --version
```

预期输出：`bmlsub 1.2.1`。

## 推荐流程

在番组根目录准备如下结构：

```text
Project/
  bgminfo/series.json
  01/
    01.mkv
```

先运行 `bmlsub ws start`。首次运行可通过问答创建 `series.json`；创建数字单集目录后，程序会登记源视频、提取参考字幕和音频，并按选择运行 direct/chunked Whisper。

人工翻译、校对并将正式 ASS 与字体放入单集目录后运行 `bmlsub ws end`。它会展示本地产品和外部动作，只有确认后才会访问 R2、VPS、qBittorrent 或 Anibt。

## 独立操作

| Option | 英文全称 | 中文含义 |
| --- | --- | --- |
| `bgminfo` | Bangumi Information | 番组信息 |
| `ensub` | English Subtitle Extraction | 英文参考字幕提取 |
| `trans` | Transcription | 音频提取与转录 |
| `pubinfo` | Publication Information | 发布信息配置 |
| `encode` | Encode | 视频编码与封装 |
| `torrent` | Torrent Creation | Torrent 创建 |
| `upr2` | Upload to R2 | 上传至 R2 |
| `dlvps` | Download to VPS | 下载到 VPS |
| `seed` | Torrent Seeding | qBittorrent 做种 |
| `anibt` | Anibt Publication | 发布至 Anibt |

每次 `build` 只执行一个 option；没有指定 option 时，TTY 中显示菜单，非 TTY 返回 `needs_review` 且不初始化状态。`rebuild` 会把本地旧目标移到 `.bmlsub/backups/`，验证新输出后才提交新回执。完整职责和输入输出见[独立操作与状态](docs/operations.md)。

## 状态与安全

独立操作状态位于当前目录的 `.bmlsub/build/`；Workstation 状态位于单集的 `workstation/state/`。计划是不可变的，回执记录文件身份、参数和上游关系。

密码、Token、Access Key 和私钥不会写入 `series.json`、计划、回执或普通输出。R2、qBittorrent、Anibt Secret 使用系统安全存储；SSH 只保存 OpenSSH Host 别名。

## 文档

- [快速开始](docs/quickstart.md)
- [CLI 参考](docs/cli.md)
- [Workstation 工作流](docs/workstation.md)
- [独立操作与状态](docs/operations.md)
- [凭据与安全存储](docs/credentials.md)
- [发布与断点续跑](docs/publishing.md)
- [开发与测试](docs/development.md)
- [故障排查](docs/troubleshooting.md)
- [架构概览](docs/architecture.md)
- [中文文档索引](docs/zh/README.md)
