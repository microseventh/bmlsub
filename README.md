# bml-subpro

`bml-subpro` 是面向 BML 动漫字幕制作与发布流程的 Python 工具库和命令行工具，覆盖单集与合集的资源检查、轨道提取、转录、编码、字幕处理、封装、上传与做种。

## 功能总览

- 单集资源发现与阶段规划
- MKV 音轨、字幕轨提取
- MLX Whisper 转录
- HEVC / x264 编码
- ASS 字幕校验、标准化和简繁转换
- MKV 内封与 MP4 硬压
- Cloudflare R2 上传
- qBittorrent 做种与发布辅助
- Workstation / 合集目录检查和批处理规划

## 安装

默认使用 conda base 环境：

```bash
conda activate base
cd /Users/miwata/Movies/BML/Project/bmlsub
pip install -e ".[all]"
```

也可以从 Git 仓库安装：

```bash
pip install "git+https://github.com/microseventh/bmlsub.git"
```

系统侧按使用范围安装 `ffmpeg`、`ffprobe`、`mkvmerge` 和 `mkvpropedit`。

## 最短上手

### 命令行

先在项目目录保存一次常用配置：

```bash
cd /path/to/project
bmlsub config init \
  --group "Billion Meta Lab" \
  --name-chs 作品名 \
  --name-cht 作品名 \
  --romaji Romaji \
  --episodes 01-12 \
  --r2-prefix 作品名/season1
```

这会生成 `bmlsub-project.json`。之后命令会自动读取其中的项目命名、集号和非敏感发布参数；显式命令参数优先覆盖配置。目录中没有该文件时，命令仍按原方式执行。

检查单集（配置中只有一集时可省略 `--episode-id`）：

```bash
bmlsub episode inspect \
  --episode-dir /path/to/01 \
  --episode-id 01
```

运行仅本地的单集流程：

```bash
bmlsub episode run \
  --episode-dir /path/to/01 \
  --episode-id 01 \
  --name-chs 作品名 \
  --name-cht 作品名 \
  --romaji Romaji \
  --local-only
```

检查合集：

```bash
bmlsub workstation inspect \
  --root-dir /path/to/project \
  --episodes 01-12
```

所有命令把最终结构化结果输出到 stdout，流水线进度输出到 stderr，便于配合 `jq` 或脚本使用。

### Python API

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, ProjectNaming

work_dir = Path("/path/to/01")
project = ProjectNaming(
    group="Billion Meta Lab",
    name_chs="作品名",
    name_cht="作品名",
    romaji="Romaji",
)
pipe = Pipeline(PipelineConfig(work_dir=work_dir, project=project))
print(pipe.inspect_episode(work_dir, episode_id="01"))
```

## ASS 文本分析与繁化

`bmlsub` 提供 ASS 感知的文本提取能力：覆盖标签会被移除，`\N` / `\n` 转成实际换行，`\h` 转成空格，`\p` 绘图坐标不会进入正文统计。

```bash
bmlsub episode analyze-ass --ass-file ./01.chs&jpn.ass
```

默认生成同目录的 `01.chs&jpn.analysis.json`。JSON 保留事件行号、时间、样式、原始文本和清洗文本，并按 `zh`、`ja`、`mixed`、`other` 分组和汇总。也可从 Python 调用：

```python
from bmlsub import extract_ass_analysis, strip_ass_tags

text = strip_ass_tags(r"这{\b1}里面{\b0}有东西")
analysis = extract_ass_analysis("01.chs&jpn.ass", "01.analysis.json")
```

繁化默认使用 ASS 感知模式，只提交识别为中文的 Dialogue 文本，保留日文、Comment、样式、字体、标签和矢量绘图。若 Events 无法解析或有汉字却无法形成可靠转换任务，会自动退回完整文件繁化，避免把简体原样写成 CHT。可用 `--full-file-hanvert` 跳过分析，直接将完整文件交给繁化姬；需要严格禁止自动回退时使用 `--no-full-file-fallback`。带多个文本节点的标签行若转换后长度变化会拒绝写入，避免标签范围错位。

ASS 感知处理思路参考 [ass-hanvert](https://github.com/oborozuk1/ass-hanvert)（MIT）；当前实现不依赖其 Python 3.12 包，项目最低版本仍为 Python 3.10。

## 按场景阅读

- [文档索引](docs/index.md)
- [使用指南：单集、合集与 Python API](docs/usage.md)
- [CLI 命令和可复制示例](docs/cli.md)
- [核心对象与 API 摘要](docs/api.md)

## 系统依赖

| 工具 | 用途 |
| --- | --- |
| `ffmpeg` | 音轨/字幕提取、转码、硬压 |
| `ffprobe` | 媒体流探测与元数据检查 |
| `mkvmerge` | MKV 内封 |
| `mkvpropedit` | MKV 元数据清理 |

详细参数和各阶段前置条件请查阅独立文档，不建议在未经检查的目录直接运行会编码、上传或做种的命令。
