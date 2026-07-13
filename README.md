# bml-subpro

`bml-subpro` 是一套面向 **BML 动漫字幕制作 / 转录 / 编码 / 封装 / 上传 / 做种 / 发布** 的 Python 工具库。  
它不是偏一次性命令的 CLI，而是更适合在 **Python 脚本、Jupyter Notebook、工作流编排代码** 中按模块组合使用。

包名：`bmlsub`
仓库路径：`Project/bml-subpro`

---

## 1. 功能总览

本项目围绕单集与合集两种场景提供能力：

- 单集资源扫描与上下文发现
- MKV 音轨 / 字幕轨提取
- MLX Whisper 转录
- HEVC / x264 编码
- ASS 字幕头校验、标准化与内置繁化姬简繁转换
- MKV 内封 / MP4 硬压封装
- Cloudflare R2 上传
- `.torrent` 生成与磁力信息读取
- qBittorrent 做种
- anibt 发布辅助
- Workstation / 合集目录规划与批量检查

---

## 2. 安装与环境

### 2.1 Python 环境

按当前工作约定，默认使用：

```bash
conda activate base
```

### 2.2 安装项目

```bash
conda activate base
cd /Users/miwata/Movies/BML/Project/bml-subpro
pip install -e ".[all]"
```

### 2.3 Python 依赖

主要依赖包括：

- `tqdm`
- `boto3`
- `botocore`
- `requests`
- `pydub`
- `qbittorrent-api`
- `mlx-whisper`（转录）
- `libtorrent`（种子）
- `huggingface_hub`（模型下载）

### 2.4 系统依赖

| 工具 | 用途 |
| --- | --- |
| `ffmpeg` | 音轨提取、字幕提取、转码、硬压 |
| `ffprobe` | 媒体流信息探测、元数据检查 |
| `mkvmerge` | MKV 内封 |
| `mkvpropedit` | MKV 元数据清理 |

---

## 3. 快速导入

```python
from bmlsub import (
    Pipeline,
    PipelineConfig,
    ProjectNaming,
    WorkstationConfig,
    R2Uploader,
    Packager,
    Transcriber,
    Encoder,
    SubtitleValidator,
    SubtitleConversionConfig,
    TorrentCreator,
    Publisher,
)
```

---

## 4. 推荐使用入口

### 4.1 单集推荐入口：`Pipeline`

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, ProjectNaming

work_dir = Path('/Users/miwata/Movies/BML/Project/01')
project = ProjectNaming(
    group='Billion Meta Lab',
    name_chs='作品名',
    name_cht='作品名',
    romaji='Romaji',
)
config = PipelineConfig(work_dir=work_dir, project=project)
pipe = Pipeline(config)

summary = pipe.inspect_episode(work_dir, episode_id='01')
print(summary)
```

### 4.2 合集 / Workstation 推荐入口：`WorkstationConfig + Pipeline`

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, WorkstationConfig

root = Path('/Users/miwata/Movies/BML/某项目')
pipe = Pipeline(PipelineConfig(work_dir=root))

ws = WorkstationConfig(
    root_dir=root,
    episode_ids='01-12',
    group='Billion Meta Lab',
    name_chs='作品名',
    name_cht='作品名',
    romaji='Romaji',
    r2_prefix='作品名/season1',
    bgm_id=123456,
)

print(pipe.inspect_workstation(ws).summary())
print(pipe.plan_workstation(ws).summary())
```

---

## 5. 包公开导出（`bmlsub.__init__`）

下列对象可直接从 `bmlsub` 顶层导入：

### 配置与命名

- `PipelineConfig`
- `EncodePreset`
- `SubtitleStandard`
- `SubtitleConversionConfig`
- `ProductNaming`
- `LanguageStrategy`
- `TrackMetaConfig`
- `ProjectNaming`
- `WorkstationConfig`
- `PRESET_HEVC_VT_DEFAULT`
- `PRESET_X264_SLOW`
- `PRESET_X264_VERYSLOW`
- `SUB_STANDARD_HD`
- `PRODUCT_FORMATS`
- `parse_episode_ids`

### 媒体 / 转录 / 封装 / 上传

- `MediaExtractor`
- `ExtractedTrack`
- `PreferredSubs`
- `SubtitleInfo`
- `Transcriber`
- `TranscriptionError`
- `model_short_name`
- `Encoder`
- `SubtitleValidator`
- `SubtitleConversionError`
- `Packager`
- `PackagingError`
- `R2Uploader`
- `R2UploadError`

### 流水线 / 资源发现

- `EpisodeFiles`
- `Pipeline`
- `StageStatus`
- `EpisodeStagePlan`
- `WorkstationStage0Summary`
- `WorkstationBatchResult`
- `scan_products`
- `check_products`
- `product_path`
- `product_torrent_path`

### 种子 / 做种 / 发布

- `TorrentCreator`
- `DEFAULT_TRACKERS`
- `TorrentMetadata`
- `read_torrent_metadata`
- `RemoteSeeder`
- `SeederError`
- `Publisher`
- `PublishError`

### 进度与模型工具

- `ProgressBar`
- `SpeedMeter`
- `StageTimer`
- `PipelineTimer`
- `detect_platform`
- `is_apple_silicon`
- `get_recommended_models`
- `check_model_available`
- `download_model`
- `resolve_model`
- `list_cached_models`
- `print_model_guide`
- `ModelRecommendation`
- `ResolvedModel`
- `backup_if_exists`

---

## 6. 核心调用流程

### 6.1 单集完整流程

典型顺序：

1. `inspect_episode()` / `plan_episode()`：先看资源与缺失项
2. `extract_audio()` / `extract_subtitles()`：提取素材
3. `transcribe_episode()`：做 AI 转录
4. `encode_episode()`：生成 HEVC 成品基础视频
5. `validate_subtitles()`：统一 ASS 头
6. `package_episode()`：输出 MP4 / MKV
7. `upload_files_to_r2()`：上传成品
8. `seed_torrents()`：做种（可选）

### 6.2 一条龙调用

```python
result = pipe.process_episode(
    episode_dir=work_dir,
    episode_id='01',
    source_video=work_dir / 'raw_source_v2.mkv',
    chs_subtitle=work_dir / 'custom_chs.ass',
    cht_subtitle=work_dir / 'custom_cht.ass',
    project=project,
    r2_prefix='作品名/01',
    skip_seed=True,
)
print(result)
```

说明：即使 `source_video` / `chs_subtitle` / `cht_subtitle` 使用了自定义文件名，
中间产物和最终产物仍然按 `episode_id='01'` 命名，例如：

- `01_audio_*.aac`
- `01_sub_*.ass`
- `01_HEVC10bit.mkv`
- 最终 MP4 / MKV 成品名中的 `[01]`

### 6.3 只跑本地流程

```python
result = pipe.process_episode(
    episode_dir=work_dir,
    episode_id='01',
    source_video=work_dir / 'raw_source_v2.mkv',
    chs_subtitle=work_dir / 'custom_chs.ass',
    cht_subtitle=work_dir / 'custom_cht.ass',
    project=project,
    skip_upload=True,
    skip_seed=True,
)
print(result)
```

---

## 7. 模块与函数详细说明

以下按文件说明每个主要类、函数、参数和常见调用方式。

---

# 7.1 `config.py`

该模块负责所有配置对象、命名模板、语言策略与批量集数解析。

## `EncodePreset`

编码预设对象。

### 字段

- `codec: str = "hevc_videotoolbox"`：视频编码器名
- `preset: str = "slow"`：编码 preset
- `crf: int | None = None`：软编码 CRF
- `quality: int = 60`：VideoToolbox 质量参数
- `pixel_fmt: str = "p010le"`：像素格式
- `audio_codec: str = "aac"`：音频编码器
- `audio_bitrate: str = "192k"`：音频码率
- `extra_params: list[str]`：附加 ffmpeg 参数

### `to_ffmpeg_video_params()`

```python
preset.to_ffmpeg_video_params() -> list[str]
```

用途：把预设转换成 ffmpeg 视频参数列表。  
常用于：`Encoder.encode_hevc_vt()`、`Encoder.encode_x264()`。

### `to_ffmpeg_audio_params()`

```python
preset.to_ffmpeg_audio_params() -> list[str]
```

用途：生成 ffmpeg 音频参数列表。

---

## `SubtitleStandard`

ASS 字幕头规范。

### 字段

- `play_res_x: int = 1920`
- `play_res_y: int = 1080`
- `color_matrix: str = "TV.709"`
- `script_type: str = "v4.00+"`
- `wrap_style: int = 0`
- `scaled_border_and_shadow: str = "yes"`

### `expected_header`

```python
standard.expected_header -> dict[str, str]
```

用途：返回标准 ASS 头键值，用于 `SubtitleValidator.validate_ass_header()` 和 `standardize_ass()`。

---

### `SubtitleConversionConfig`

简繁字幕转换配置。

主要字段：

- `api_url: str = 'https://api.zhconvert.org/convert'`：默认繁化姬 API
- `converter: str = 'Taiwan'`：默认转换模式，台湾繁体
- `timeout: int = 60`：请求超时秒数
- `backup_dir_name: str = '_backup'`：旧繁体字幕备份目录名
- `regenerate_existing_cht: bool = True`：当简繁字幕同时存在时，是否默认以简体为基准重建繁体

---

## `ProductNaming`

最终产物命名模板。

### 字段

- `formats: dict[str, str]`
  - `mp4_chs`: 简体 MP4 模板
  - `mp4_cht`: 繁体 MP4 模板
  - `mkv_hevc`: HEVC MKV 模板
- `cht_keys: set[str]`：哪些键属于繁体命名

调用方通常无需直接调用方法，而是把它放入 `PipelineConfig.naming` 或 `WorkstationConfig.naming`。

---

## `LanguageStrategy`

字幕语言优先级与语言别名配置。

### 字段

- `preferred: list[str] = ["chi", "eng", "jpn"]`
- `aliases: dict[str, set[str]]`：语言别名映射

### `classify(lang)`

```python
LanguageStrategy.classify(lang: str) -> str
```

参数：

- `lang`：语言代码，如 `chi`、`eng`、`ja`

返回：

- `chi` / `eng` / `jpn` / `other`

用于：智能字幕提取时的语言归类。

---

## `TrackMetaConfig`

MKV 内封时字幕轨道元数据配置。

### 字段

- `names`：轨道标题，如 `简体中文+日语`
- `defaults`：默认轨标记
- `languages`：轨语言代码

被 `Packager._detect_subtitle_meta()` 使用。

---

## `_compose_prefix(group, title, romaji)`

```python
_compose_prefix(group: str, title: str, romaji: str) -> str
```

用途：拼装 `[字幕组] 标题 罗马字` 前缀。  
通常不会直接手调，而由 `ProjectNaming.prefix_chs/prefix_cht` 间接调用。

---

## `ProjectNaming`

单项目命名配置。

### 字段

- `group: str = "Billion Meta Lab"`
- `name_chs: str = "作品名"`
- `name_cht: str = "作品名"`
- `romaji: str = "Romaji"`

### `prefix_chs`

```python
project.prefix_chs -> str
```

生成简体成品命名前缀。

### `prefix_cht`

```python
project.prefix_cht -> str
```

生成繁体成品命名前缀。

### 调用示例

```python
project = ProjectNaming(
    group='Billion Meta Lab',
    name_chs='不虐待我的继母与继姐',
    name_cht='不虐待我的繼母與繼姐',
    romaji='Ibitte Konai Gibo to Gishi',
)
print(project.prefix_chs)
print(project.prefix_cht)
```

---

## `WorkstationConfig`

合集 / 项目级目录配置。

### 构造参数

```python
WorkstationConfig(
    root_dir=..., 
    episode_ids=..., 
    group='Billion Meta Lab',
    name_chs='作品名',
    name_cht='作品名',
    romaji='Romaji',
    raw_dir_name='RAW',
    sub_dir_name='CHS&JPN',
    sub_tj_dir_name='CHT&JPN',
    hevc_label='[1080P][HEVC-10bit][简繁日外挂]',
    chs_label='[1080P][简日内嵌]',
    cht_label='[1080P][繁日內嵌]',
    hevc_subdir_name='HEVC-10Bit',
    r2_prefix='',
    bgm_id=None,
    notes='',
    naming=ProductNaming(),
)
```

### 常用属性 / 方法

#### `__post_init__()`

- 规范化 `root_dir`
- 通过 `parse_episode_ids()` 解析 `episode_ids`

#### `prefix_chs` / `prefix_cht`

与 `ProjectNaming` 类似，生成合集输出前缀。

#### `effective_episode_ids`

```python
ws.effective_episode_ids -> list[str]
```

如果构造时未传 `episode_ids`，则自动从 `RAW/*.mkv` 推断。

#### `ep_range`

```python
ws.ep_range -> str
```

返回诸如 `01-12` 的合集范围字符串。

#### 目录属性

- `raw_dir`
- `sub_dir`
- `sub_tj_dir`
- `hevc_pack_dir`
- `hevc_sub_dir`
- `chs_pack_dir`
- `cht_pack_dir`

#### 关键路径方法

- `infer_episode_ids()`：从 RAW 目录推断集号
- `source_video(ep_id)`：返回源视频路径
- `hevc_raw_video(ep_id)`：返回 HEVC 原始输出路径
- `resolve_chs_sub(ep_id)`：定位简体字幕文件
- `resolve_cht_sub(ep_id)`：定位繁体字幕文件
- `hevc_path(ep_id)`：HEVC 输出路径
- `x264_path(ep_id, kind)`：MP4 输出路径，`kind` 只能是 `chs` 或 `cht`
- `release_pack_dir(kind)`：返回发布目录，`kind` 为 `hevc/chs/cht`
- `release_torrent_path(kind)`：返回发布目录对应的 `.torrent` 路径

#### 检查与汇总

- `stage0_checks()`：逐集检查 RAW / 简中字幕 / 繁中字母是否存在
- `missing_summary()`：按类别汇总缺失项
- `sample_outputs()`：给出样例输出路径
- `summary()`：返回完整配置摘要

### 调用示例

```python
ws = WorkstationConfig(root_dir=root, episode_ids='01-12')
print(ws.summary())
print(ws.source_video('01'))
print(ws.resolve_chs_sub('01'))
```

---

## `PipelineConfig`

主流水线配置。

### 主要字段

- `work_dir: Path`：默认工作目录
- `whisper_fast_model: str`：直接转录模型
- `whisper_detailed_model: str`：分段转录模型
- `language: str = 'ja'`
- `hevc_preset: EncodePreset`
- `x264_preset: EncodePreset`
- `sub_standard: SubtitleStandard`
- `subtitle_conversion: SubtitleConversionConfig`
- `chunk_sec: int = 240`
- `overlap_sec: int = 5`
- `output_transcripts_dir: Path`
- `naming: ProductNaming`
- `subtitle_strategy: LanguageStrategy`
- `track_meta: TrackMetaConfig`
- `project: ProjectNaming`

### `__post_init__()`

用途：规范化路径，如 `work_dir`、`output_transcripts_dir`。

---

## `parse_episode_ids(value)`

```python
parse_episode_ids(value: str | list[str] | None) -> list[str]
```

用途：解析 notebook 风格集号输入。

支持示例：

```python
parse_episode_ids('01-03')        # ['01', '02', '03']
parse_episode_ids('01,03,05')     # ['01', '03', '05']
parse_episode_ids(['01', '02'])   # ['01', '02']
```

---

# 7.2 `episode.py`

负责“单集上下文发现”，把某一集目录中的视频、字幕、字体、转录输入、成品输出统一收敛为一个对象。

## `EpisodeFiles`

### 主要字段

- `episode_dir: Path`
- `episode_id: str`
- `config: PipelineConfig`
- `pure_mkv: Path | None`：当前单集实际使用的视频输入；默认是 `01.mkv`，也可以由 `source_video` 显式覆盖
- `hevc_mkv: Path | None`：如 `01_HEVC10bit.mkv`
- `subtitles: dict[str, list[Path]]`
- `fonts: list[Path]`
- `extracted_audio: list[Path]`
- `extracted_subtitles: list[Path]`
- `expected_products: dict[str, Path]`
- `existing_products: dict[str, Path | None]`
- `torrent_products: dict[str, Path | None]`
- `source_video_path: Path | None`：显式传入的源视频路径
- `override_subtitles: dict[str, Path]`：显式传入的 `chs/cht` 字幕覆盖路径

### `discover(...)`

```python
EpisodeFiles.discover(
    episode_dir: Path | str,
    episode_id: str | None = None,
    prefix_chs: str | None = None,
    prefix_cht: str | None = None,
    config: PipelineConfig | None = None,
    project: ProjectNaming | None = None,
    source_video: Path | str | None = None,
    chs_subtitle: Path | str | None = None,
    cht_subtitle: Path | str | None = None,
) -> EpisodeFiles
```

参数：

- `episode_dir`：单集目录
- `episode_id`：集号；不传时尝试自动推断
- `prefix_chs` / `prefix_cht`：手工覆盖成品前缀
- `config`：流水线配置
- `project`：项目命名配置，优先级高于 `config.project`
- `source_video`：显式指定源视频路径；不改 `episode_id`，只改输入定位
- `chs_subtitle` / `cht_subtitle`：显式指定简繁字幕路径；不要求文件名必须是 `01.*.ass`

用途：统一发现单集相关的所有输入 / 输出路径。  
常作为 `Pipeline.context()` 的底层实现。

### `_resolve_prefixes(...)`

内部方法，负责解析命名优先级：

1. 显式传入的 `prefix_*`
2. `project.prefix_*`
3. `config.project.prefix_*`

### `_discover_fonts(directory)`

扫描 `.ttf` / `.otf` / `.ttc` 字体文件。

### `_discover_subtitles(directory, episode_id, config)`

按内建规则扫描字幕文件：

- `chs`
- `cht`
- `eng`
- `jpn`
- `chi`
- `other`

### `all_subs`

```python
ctx.all_subs -> list[Path]
```

按优先顺序返回所有字幕路径。

### `subtitle_for(sub_type)`

```python
ctx.subtitle_for(sub_type: str) -> Path | None
```

参数：

- `sub_type`：如 `chs`、`cht`、`eng`

返回某一类字幕的第一候选文件。

### `summary()`

返回当前集资源摘要字典。

### 调用示例

```python
ctx = EpisodeFiles.discover(
    '/path/to/ep01',
    episode_id='01',
    source_video='raw_source_v2.mkv',
    chs_subtitle='custom_chs.ass',
    cht_subtitle='custom_cht.ass',
)
print(ctx.pure_mkv)
print(ctx.source_video_path)
print(ctx.override_subtitles)
print(ctx.all_subs)
print(ctx.summary())
```

---

# 7.3 `media.py`

负责媒体流探测、音轨提取、字幕提取与智能筛选。

## 数据类

### `SubtitleInfo`

提取前的字幕轨信息：

- `index`
- `language`
- `title`
- `codec_name`

### `ExtractedTrack`

提取后的轨道信息：

- `index`
- `codec_type`
- `language`
- `title`
- `codec_name`
- `output_path`

### `PreferredSubs`

智能字幕筛选结果：

- `chi`
- `eng`
- `jpn`
- `other`

方法：

- `total_count`
- `has_any`
- `all_tracks()`
- `summary()`

---

## `MediaExtractor`

### `__init__(work_dir='.')`

设置默认工作目录，提取后的文件默认落在这里。

### `find_digit_mkvs()`

```python
extractor.find_digit_mkvs() -> list[Path]
```

用途：查找形如 `01.mkv` 的原始单集，不包含 `*_HEVC10bit.mkv`。

### `find_all_mkvs()`

返回当前目录全部 `.mkv`。

### `probe_streams(video_path)`

```python
extractor.probe_streams(video_path: Path) -> list[dict]
```

用途：通过 `ffprobe` 读取流信息。

### `list_subtitle_streams(video_path)`

返回所有字幕流的 `SubtitleInfo` 列表。

### `extract_audio_tracks(video_path, progress=None, output_stem=None)`

```python
extractor.extract_audio_tracks(
    video_path: Path,
    progress=None,
    output_stem: str | None = None,
) -> list[ExtractedTrack]
```

参数：

- `video_path`：输入 MKV
- `progress`：可选进度条对象，需要有 `.update(n)`
- `output_stem`：输出文件名前缀；不传时默认使用 `video_path.stem`

输出文件命名：

- 默认：`{video_path.stem}_audio_{lang}_{index}.aac`
- 指定 `output_stem='01'` 时：`01_audio_{lang}_{index}.aac`

### `extract_subtitle_tracks(video_path, output_stem=None)`

```python
extractor.extract_subtitle_tracks(
    video_path: Path,
    output_stem: str | None = None,
) -> list[ExtractedTrack]
```

输出文件命名：

- 默认：`{video_path.stem}_sub_{lang}_{index}.ass`
- 指定 `output_stem='01'` 时：`01_sub_{lang}_{index}.ass`

### `extract_preferred_subtitles(video_path, langs=None, output_stem=None)`

```python
extractor.extract_preferred_subtitles(
    video_path: Path,
    langs: list[str] | None = None,
    output_stem: str | None = None,
) -> PreferredSubs | None
```

参数：

- `video_path`：输入视频
- `langs`：语言优先级，默认 `['chi', 'eng', 'jpn']`
- `output_stem`：输出文件名前缀；适合让提取结果继续按 `episode_id` 命名

逻辑：

1. 先列出字幕流
2. 归类为中 / 英 / 日 / 其他
3. 如果只有一种语言，则全部提取
4. 否则逐条提取并分桶

### `extract_all(video_path, output_stem=None)`

返回：

```python
(audio_tracks, subtitle_tracks)
```

### `extract_smart(video_path, output_stem=None)`

返回：

```python
(audio_tracks, preferred_subs)
```

### `get_audio_track(video_path, index=0, output_stem=None)`

快速获取第 `index` 条音轨对应的输出路径。

### 语言工具方法

- `is_chi(lang)`
- `is_eng(lang)`
- `is_jpn(lang)`

### 内部辅助方法

- `_get_non_attachment_streams(video_path)`
- `_get_streams_dict(video_path)`
- `_classify_lang(lang)`
- `_extract_single_sub(video_path, stream)`
- `_stream_to_track(video_path, stream, track_type)`
- `_bundle_preferred(extracted, info_list)`
- `_classify_lang_raw(lang)`

### 调用示例

```python
extractor = MediaExtractor('/path/to/ep01')
audio_tracks = extractor.extract_audio_tracks(
    Path('raw_source_v2.mkv'),
    output_stem='01',
)
subs = extractor.extract_preferred_subtitles(
    Path('raw_source_v2.mkv'),
    output_stem='01',
)
print(audio_tracks)
print(subs.summary() if subs else '无字幕')
```

---

# 7.4 `transcribe.py`

负责 AI 转录。

## `TranscriptionError`

转录异常。

## `model_short_name(model_path)`

```python
model_short_name(model_path: str) -> str
```

用途：把完整模型路径缩短成输出文件名中的标识。

示例：

```python
model_short_name('mlx-community/whisper-large-v3-turbo')
# 'large-v3-turbo'
```

---

## `Transcriber`

### `__init__(...)`

```python
Transcriber(
    model='mlx-community/whisper-large-v3-turbo',
    language='ja',
    chunk_sec=240,
    overlap_sec=5,
    export_format='mp3',
    output_root='./output_transcripts',
)
```

参数说明：

- `model`：默认模型
- `language`：语种代码
- `chunk_sec`：分段转录切片长度
- `overlap_sec`：切片重叠长度
- `export_format`：切片导出格式
- `output_root`：chunked 工作目录根路径

### `transcribe_direct(audio_path, model=None, output_path=None, force=False)`

用途：整轨一次性转录，适合快速结果。  
输出命名：`{stem}_direct_{模型简称}.txt`

参数：

- `audio_path`：音频路径
- `model`：覆盖实例默认模型
- `output_path`：自定义输出路径
- `force`：是否强制覆盖已有结果

返回：

- `Path | None`

### `transcribe_chunked(audio_path, model=None, manual_cuts=None, output_dir=None, force=False)`

用途：切片转录后再合并，精细度更高。  
输出命名：`{stem}_chunked_{模型简称}_final.txt`

参数：

- `audio_path`
- `model`
- `manual_cuts`：如 `['10:00', '20:00']`
- `output_dir`：工作目录
- `force`：是否强制重跑

### `transcribe_both(audio_path, fast_model=..., detailed_model=..., manual_cuts=None)`

顺序执行：

1. `transcribe_direct()`
2. `transcribe_chunked()`

返回：

```python
{
    'direct': Path | None,
    'chunked': Path | None,
}
```

### 内部方法

- `_split_audio(audio_path, work_dir, manual_cuts)`：按时长与手动切点切片
- `_transcribe_chunks(chunks, model_name, mlx_whisper)`：逐片转录
- `_merge_chunks(work_dir, final_path)`：合并文本
- `_safe_sort_key(f)`：chunk 文件排序
- `_timestamp_to_ms(ts)`：`mm:ss` / `hh:mm:ss` 转毫秒

### 调用示例

```python
transcriber = Transcriber(language='ja')
res = transcriber.transcribe_both(
    Path('01_audio_jpn_1.aac'),
    manual_cuts=['01:30', '22:00'],
)
print(res)
```

---

# 7.5 `encode.py`

负责 HEVC / x264 编码与元数据清理。

## `Encoder`

### `__init__(hevc_preset=None, x264_preset=None)`

参数：

- `hevc_preset`：HEVC 使用的 `EncodePreset`
- `x264_preset`：x264 使用的 `EncodePreset`

### `encode_hevc_vt(src, dst=None, audio_streams=None, strip_metadata=True)`

```python
encoder.encode_hevc_vt(
    src: Path,
    dst: Path | None = None,
    audio_streams: list[int] | None = None,
    strip_metadata: bool = True,
) -> Path
```

用途：使用 macOS VideoToolbox 做 HEVC 10bit 编码。

参数：

- `src`：源视频
- `dst`：输出路径，默认 `{src.stem}_HEVC10bit.mkv`
- `audio_streams`：仅保留指定音轨索引；不传则保留全部音轨
- `strip_metadata`：编码后是否清理元数据

### `encode_x264(src, dst, ass_subtitle=None, preset=None)`

用途：x264 软件编码，可选烧录 ASS 字幕。

参数：

- `src`：源视频
- `dst`：输出 MP4
- `ass_subtitle`：要烧录的 ASS 路径
- `preset`：覆盖默认 x264 预设

### `strip_metadata(video_path)`

优先使用 `mkvpropedit` 深度清理；失败时回退到 `ffmpeg` 流拷贝方式。

### `verify_metadata_clean(video_path)`

返回剩余潜在源泄漏标签的报告字典，空字典表示基本干净。

### 调用示例

```python
encoder = Encoder()
hevc = encoder.encode_hevc_vt(Path('01.mkv'))
mp4 = encoder.encode_x264(Path('01.mkv'), Path('01.mp4'), ass_subtitle=Path('01.chs&jpn.ass'))
print(encoder.verify_metadata_clean(hevc))
```

---

# 7.6 `subtitle.py`

负责 ASS 字幕校验、标准化与简繁转换。

## `SubtitleValidator`

### `__init__(standard=None, config=None)`

参数：

- `standard`：字幕头规范，默认 `SUB_STANDARD_HD`
- `config`：流水线配置

### `check_subtitle_exists(episode_dir, episode_id, sub_type, chs_subtitle=None, cht_subtitle=None)`

返回指定类型字幕路径或 `None`。如果传入 `chs_subtitle` / `cht_subtitle`，优先使用显式覆盖路径。

### `validate_for_episode(episode_dir, episode_id, chs_subtitle=None, cht_subtitle=None)`

返回单集字幕校验结果：

- 是否有 `chs`
- 是否有 `cht`
- 头是否合规
- 全部字幕文件列表

### `validate_ass_header(ass_path)`

```python
validator.validate_ass_header(ass_path: Path) -> dict[str, str]
```

返回违反规范的头字段。

### `standardize_ass(ass_path, output_path=None)`

用途：把字幕头修正到标准格式。  
如果 `output_path is None`，则原地修改并先备份旧文件。

### `standardize_extracted_subs(episode_dir, episode_id, source_video=None, chs_subtitle=None, cht_subtitle=None)`

仅处理提取出来的 `*_sub_*.ass`。这里的 override 参数主要用于与单集上下文保持一致。

### `convert_chs_to_cht(chs_path, output_path=None, *, converter=None, api_url=None, timeout=None, backup_existing=True)`

用途：调用内置繁化姬 API，把简体字幕转换成繁体字幕。  
默认使用 `PipelineConfig.subtitle_conversion` 中的设置，即：

- API：`https://api.zhconvert.org/convert`
- 转换模式：`Taiwan`

如果 `output_path` 为空，会自动把 `*.chs&jpn.ass` / `*.chs.ass` 推导为对应的 `*.cht&jpn.ass` / `*.cht.ass`。

### `ensure_episode_subtitles(episode_dir, episode_id, ..., converter=None, api_url=None, timeout=None, regenerate_cht=None, standardize=True)`

用途：按工作流规则确保单集字幕状态正确：

- 只有简体时：自动生成繁体
- 简繁同时存在时：默认把旧繁体移到 `_backup/`，再以简体重建繁体
- 只有繁体时：跳过繁化，仅校验现有繁体

返回结果包含：

- `chs`
- `cht`
- `generated_cht`
- `backed_up`
- `validated`
- `standardized`
- `missing`
- `all_ok`

### `derive_cht_path(chs_path)`

根据简体字幕路径自动推导默认繁体字幕路径。

### `move_to_backup(path, backup_dir=None)`

把旧字幕移动到备份目录，默认目录名来自 `subtitle_conversion.backup_dir_name`。

### `_parse_ass_header(ass_path)`

内部方法，用于解析 `[Script Info]` 段。

### 调用示例

```python
validator = SubtitleValidator()
issues = validator.validate_ass_header(Path('01.chs&jpn.ass'))
if issues:
    validator.standardize_ass(Path('01.chs&jpn.ass'))
```

---

# 7.7 `package.py`

负责产物封装：MP4 硬压 + MKV 内封。

## `PackagingError`

封装异常。

## `PackagingPlan`

描述当前集是否满足封装条件。

### 字段

- `episode_id`
- `pure_mkv`
- `hevc_mkv`
- `chs_sub`
- `cht_sub`
- `fonts`
- `mkv_output`
- `mp4_chs_output`
- `mp4_cht_output`
- `missing_for_mkv`
- `missing_for_mp4`

### `has_mkv_inputs`

是否满足 MKV 内封输入条件。

### `has_mp4_inputs`

是否满足 MP4 硬压输入条件。

### `summary()`

返回计划摘要。

---

## `Packager`

### `__init__(episode_dir, episode_id, config=None, source_video=None, chs_subtitle=None, cht_subtitle=None)`

设置单集工作目录、集号和配置，并允许显式指定输入视频 / 字幕。

### `context()`

返回当前集的 `EpisodeFiles`。

### `get_available_files()`

统一返回：

- `pure_mkv`
- `hevc_mkv`
- `chs_sub`
- `cht_sub`
- `eng_sub`
- `jpn_sub`
- `all_subs`
- `fonts`
- `context`

### `build_plan(prefix_chs=None, prefix_cht=None, project=None)`

构造 `PackagingPlan`。

参数：

- `prefix_chs` / `prefix_cht`：覆盖输出前缀
- `project`：命名配置

### `mkvmerge_package(output_template=None, output_path=None)`

用途：把 HEVC 视频、简繁字幕和字体一起封成 MKV。

参数：

- `output_template`：模板形式的输出名
- `output_path`：直接给定完整输出路径

注意：

- 必须有 `HEVC10bit.mkv`
- 必须同时有简繁字幕
- 必须有字体

### `ffmpeg_hardsub_encode(chs_template=None, cht_template=None, chs_output=None, cht_output=None)`

用途：对原始视频分别烧录简中 / 繁中字幕并输出 MP4。

参数：

- `chs_template` / `cht_template`：模板名
- `chs_output` / `cht_output`：直接输出路径

返回：

- `list[Path]`

### `package_expected(prefix_chs=None, prefix_cht=None, project=None)`

按照 `EpisodeFiles.expected_products` 的默认命名进行封装。  
推荐在新版工作流中优先使用。

### `package_all(mkv_tmpl, chs_tmpl, cht_tmpl)`

旧式模板驱动封装入口：

- `mkv_tmpl`
- `chs_tmpl`
- `cht_tmpl`

### 内部方法

- `_resolve_output_path(output_template=None, output_path=None)`
- `_backup_output(output_path, label)`
- `_detect_subtitle_meta(sub_type)`
- `_detect_font_mime(font_path)`

### 调用示例

```python
packager = Packager(
    '/path/to/ep01',
    '01',
    source_video='raw_source_v2.mkv',
    chs_subtitle='custom_chs.ass',
    cht_subtitle='custom_cht.ass',
)
plan = packager.build_plan()
print(plan.summary())
files = packager.package_expected()
print(files)
```

---

# 7.8 `r2upload.py`

负责 Cloudflare R2 上传。

## `R2UploadError`

R2 上传异常。

## `R2Uploader`

### `__init__(account_id=None, access_key_id=None, secret_access_key=None, bucket_name=None, endpoint=None)`

凭证来源优先级：

1. 构造参数
2. 环境变量
3. `~/.config/bml/r2_config.json`

支持的环境变量：

- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_ENDPOINT`

### `upload_file(local_path, remote_key=None, progress=True)`

```python
uploader.upload_file(
    local_path: str | Path,
    remote_key: str | None = None,
    progress: bool = True,
) -> str
```

参数：

- `local_path`：本地文件路径
- `remote_key`：远端对象 key；不传时会按本地文件名生成
- `progress`：是否打印日志

返回：

- 最终上传的远端 key

### `upload_files(paths, remote_folder='', progress=True)`

批量上传多个文件。  
每个文件的 key 为：`remote_folder + '/' + 文件名`。

### `list_remote(prefix='')`

列出远端指定前缀下所有 key。

### `delete_remote(key)`

删除远端对象。

### `recorded_hashes()`

返回当前 uploader 进程中记录的本地 SHA256 映射。

### 内部方法

- `_load_config(...)`
- `_upload_small(local_path, remote_key)`
- `_upload_large(local_path, remote_key)`
- `_sha256_local(file_path)`

### 调用示例

```python
uploader = R2Uploader(bucket_name='bml-releases')
key = uploader.upload_file('01_HEVC10bit.mkv', '作品名/01/01_HEVC10bit.mkv')
print(key)

uploaded = uploader.upload_files(
    ['01_HEVC10bit.mkv', '01.mp4'],
    remote_folder='作品名/01',
)
print(uploaded)
```

---

# 7.9 `torrent.py`

负责 `.torrent` 计划、创建和磁力读取。

## 常量

### `DEFAULT_TRACKERS`

默认 tracker 列表。

## 数据类

### `TorrentMetadata`

字段：

- `name`
- `info_hash_v1`
- `info_hash_v2`
- `trackers`
- `magnet_uri`

### `TorrentPlan`

字段：

- `src`
- `dst`
- `piece_size`
- `v1_only`
- `tracker_count`

方法：`summary()`

### `TorrentBatchPlan`

字段：

- `plans`

方法：`summary()`

---

## `read_torrent_metadata(torrent_path)`

```python
read_torrent_metadata(torrent_path: Path | str) -> TorrentMetadata
```

用途：读取本地 `.torrent` 并生成标准磁力链接。

---

## `TorrentCreator`

### `__init__(trackers=None, extra_trackers=None, piece_length=None, comment='', created_by='BML')`

参数：

- `trackers`：完全覆盖默认 tracker 列表
- `extra_trackers`：附加 tracker
- `piece_length`：手工分块大小
- `comment`：种子注释
- `created_by`：种子创建者标记

### `build_plan(src, dst=None, v1_only=False)`

生成单个种子计划。

### `build_batch_plan(sources, v1_only=False)`

批量生成计划。

### `create(src, dst=None, v1_only=False)`

真正生成 `.torrent`。

### `create_many(sources, v1_only=False)`

批量生成多个种子。

### 内部方法

- `_estimate_piece_size(src)`
- `_calc_piece_size(total_bytes)`
- `_num_pieces_str(src, piece_size)`
- `_total_size(src)`

### 调用示例

```python
creator = TorrentCreator()
plan = creator.build_plan('/path/to/release_dir', v1_only=True)
print(plan.summary())

torrent_path = creator.create('/path/to/release_dir', v1_only=True)
meta = read_torrent_metadata(torrent_path)
print(meta.magnet_uri)
```

---

# 7.10 `publish.py`

负责本地 qBittorrent 做种辅助与 anibt 发布。

## `PublishError`

发布异常。

## `ReleasePlan`

字段：

- `title`
- `episode_key`
- `torrent_path`
- `resolution`
- `languages`
- `fmt`
- `subtitle`
- `mode`

方法：`summary()`

---

## `Publisher`

### `seed_qbittorrent(host, files, torrent_base_dir=None, download_base='/downloads', username='admin', password='')`

参数：

- `host`：qBittorrent 地址
- `files`：成品文件路径列表；代码会寻找同名 `.torrent`
- `torrent_base_dir`：若种子不在成品目录，可指定基目录
- `download_base`：保存路径
- `username` / `password`：登录凭据

返回：

- `dict[str, bool]`，键为文件名，值为是否添加成功

### `build_release_plan(title, episode_key, torrent_path, resolution='1080p', languages=None, subtitle='INTERNAL', fmt='MKV', use_torrent_file=False)`

用途：构造发布计划对象，不发请求。

### `publish_anibt(...)`

```python
Publisher.publish_anibt(
    bgm_id: int,
    title: str,
    episode_key: str,
    torrent_path: str | Path | None = None,
    magnet_base64: str | None = None,
    *,
    resolution: str = '1080p',
    languages: list[str] | None = None,
    subtitle: str = 'INTERNAL',
    fmt: str = 'MKV',
    file_size: int | None = None,
    notes: str = '',
    trackers: list[str] | None = None,
    token: str | None = None,
    api_url: str | None = None,
    use_torrent_file: bool = False,
) -> dict
```

参数重点：

- `bgm_id`：Bangumi / 站点 ID
- `title`：标题
- `episode_key`：集号标识
- `torrent_path`：本地种子路径
- `magnet_base64`：若已有 base64 磁力，可直接传
- `languages`：语言列表
- `token`：API Token
- `api_url`：API 地址
- `use_torrent_file`：是否改为 multipart 上传 `.torrent`

逻辑：

1. 先加载 token / api_url
2. 若给 `torrent_path`，则读取种子并构造 magnet
3. 发 `requests.post()` 请求

### 内部方法

- `_build_magnet(info_hash, name, file_size, trackers)`
- `_publish_torrent_file(...)`
- `_read_torrent_info(torrent_path)`
- `_load_anibt_config(token=None, api_url=None)`

### 配置来源

- 参数
- 环境变量：`ANIBT_TOKEN`、`ANIBT_API_URL`
- `~/.config/bml/anibt_config.json`

---

# 7.11 `model_utils.py`

负责平台检测、推荐模型、模型缓存检查与下载。

## 数据类

### `ModelRecommendation`

字段：

- `model_id`
- `backend`
- `name`
- `description`
- `speed`
- `accuracy`
- `lang_specialty`
- `size_gb`
- `install_cmd`
- `cache_dir_help`

### `ResolvedModel`

字段：

- `model_id`
- `backend`
- `available`
- `cache_path`
- `platform_info`
- `recommendation`
- `notes`

---

## 函数

### `detect_platform()`

返回平台信息字典。

### `is_apple_silicon()`

返回当前是否为 Apple Silicon macOS。

### `get_recommended_models(language='ja')`

按语言和平台返回推荐模型列表。

### `_hf_cache_dir()`

返回 Hugging Face 缓存目录。

### `check_model_available(model_id, backend='auto')`

检查某模型是否已缓存到本地。

### `list_cached_models()`

列出本地已缓存模型。

### `download_model(model_id, backend='auto', force=False)`

使用 `huggingface_hub.snapshot_download` 下载模型。

### `resolve_model(model_id=None, language='ja', backend=None, auto_download=False)`

综合平台、推荐项和缓存状态，返回 `ResolvedModel`。

### `print_model_guide(language='ja')`

打印推荐模型说明。

### 调用示例

```python
from bmlsub import print_model_guide, resolve_model

print_model_guide(language='ja')
info = resolve_model(language='ja', auto_download=False)
print(info)
```

---

# 7.12 `pipeline.py`

这是整个项目最重要的编排模块。

## 数据类

### `StageStatus`

字段：

- `name`
- `ready`
- `missing`
- `outputs`
- `notes`

方法：`summary()`

### `EpisodeStagePlan`

字段：

- `episode_id`
- `inspect`
- `extract_subtitles`
- `extract_audio`
- `transcribe`
- `encode_hevc`
- `validate_subtitles`
- `package`

方法：`summary()`

### `WorkstationStage0Summary`

字段：

- `workstation`
- `stage0`

方法：`summary()`

### `WorkstationBatchResult`

字段：

- `mode`
- `stage`
- `ok`
- `items`
- `missing`
- `outputs`
- `notes`

方法：`summary()`

---

## `Pipeline`

### `__init__(config=None, **kwargs)`

参数：

- `config`：完整 `PipelineConfig`
- `**kwargs`：未传 `config` 时可直接传入 `PipelineConfig` 字段

### 懒加载属性

- `extractor`
- `transcriber`
- `encoder`
- `validator`

这些属性会按需创建对应对象。

### `context(episode_dir, episode_id=None, prefix_chs=None, prefix_cht=None, project=None, source_video=None, chs_subtitle=None, cht_subtitle=None)`

返回单集 `EpisodeFiles` 对象。显式输入覆盖只改变输入定位，不改变 `episode_id` 驱动的输出命名。

### `inspect_episode(...)`

返回单集资源摘要字典，适合最早期检查。

### `plan_episode(...)`

返回 `EpisodeStagePlan`，显示每一步是否 ready、缺什么、会输出什么。

### `build_workstation(root_dir, episode_ids=None, **kwargs)`

快捷构造 `WorkstationConfig`。

### `inspect_workstation(workstation, **kwargs)`

返回 `WorkstationStage0Summary`，用于合集 stage0 检查。

### `plan_workstation(workstation, **kwargs)`

按集汇总 `plan_episode()` 结果。

### `extract_subtitles(episode_dir, episode_id, smart=False, source_video=None, chs_subtitle=None, cht_subtitle=None)`

参数：

- `smart=False`：`False` 时提取全部字幕轨，`True` 时按优先语言筛选
- `source_video`：显式指定输入视频路径
- `chs_subtitle` / `cht_subtitle`：保留给统一上下文；提取阶段本身主要使用 `source_video`

注意：提取出的字幕文件仍按 `episode_id` 生成 `01_sub_*.ass` 这类名字。

返回：

```python
{
    'ok': bool,
    'missing': list[str],
    'tracks': list[str],
}
```

### `extract_audio(episode_dir, episode_id, source_video=None, chs_subtitle=None, cht_subtitle=None)`

返回结构与 `extract_subtitles()` 类似。

注意：即使 `source_video='raw_source_v2.mkv'`，输出音轨仍会按 `episode_id` 命名，例如 `01_audio_jpn_1.aac`。

### `extract_media(episode_dir=None, episodes=None, smart_subs=True)`

对目录中的多个 MKV 批量提取音轨与字幕。

### `validate_workstation_subtitles(workstation, **kwargs)`

合集模式批量字幕检查。

说明：

- 会按 `ws.effective_episode_ids` 逐集复用 `validate_subtitles()`
- 当前主流程默认等价于普通字幕头校验 / 标准化
- 如果你希望合集模式同时做“缺繁体自动生成 / 简体重建繁体”，可以显式遍历每一集并调用：

```python
for ep_id in ws.effective_episode_ids:
    result = pipe.validate_subtitles(
        pipe._single_episode_dir(ws, ep_id),
        ep_id,
        ensure_cht=True,
    )
    print(ep_id, result)
```

也就是说：**v2 已支持合集模式，简繁转换能力也可用于合集，只是当前 `validate_workstation_subtitles()` 还没有把 `ensure_cht=True` 设为默认行为。**

### `encode_workstation_hevc(workstation, **kwargs)`

合集模式按规划路径生成 HEVC 输出计划项。

### `build_release_batch(workstation, **kwargs)`

按合集发布目录构造整体种子计划。

### `transcribe_episode(episode_dir, episode_id, direct_model=None, chunked_model=None, manual_cuts=None, source_video=None, chs_subtitle=None, cht_subtitle=None)`

参数：

- `direct_model`：覆盖快速模型
- `chunked_model`：覆盖精细模型
- `manual_cuts`：切点列表
- `source_video` / `chs_subtitle` / `cht_subtitle`：与单集上下文保持一致；转录阶段主要消费已提取的 `episode_id_audio_*.aac`

返回：

```python
{
    'ok': bool,
    'missing': list[str],
    'direct': Path | None,
    'chunked': Path | None,
}
```

### `encode_episode(episode_dir, episode_id, source_video=None, chs_subtitle=None, cht_subtitle=None)`

返回 HEVC 输出路径。

注意：即使输入视频不是 `01.mkv`，HEVC 输出仍固定为 `01_HEVC10bit.mkv`。

### `validate_subtitles(episode_dir, episode_id, source_video=None, chs_subtitle=None, cht_subtitle=None, ensure_cht=False, converter=None, api_url=None, timeout=None, regenerate_cht=None)`

返回：

- `all_ok`
- `standardized`
- `missing`
- `generated_cht`（当 `ensure_cht=True` 且发生生成时）
- `backed_up`（当 `ensure_cht=True` 且旧繁体被移入备份时）
- `validated`（当 `ensure_cht=True` 时）

说明：

- 默认行为仍是只做字幕头校验/标准化
- 当 `ensure_cht=True` 时，会启用内置繁化姬流程
- 默认 API 为 `https://api.zhconvert.org/convert`
- 默认转换模式为 `Taiwan`

### `package_episode(episode_dir, episode_id, mkv_template=None, chs_template=None, cht_template=None, prefix_chs=None, prefix_cht=None, project=None, source_video=None, chs_subtitle=None, cht_subtitle=None)`

- 如果模板参数未全给，则走 `Packager.package_expected()`
- 否则走 `Packager.package_all()`
- 即使字幕文件名不是 `01.*.ass`，只要通过 `chs_subtitle` / `cht_subtitle` 显式传入，也仍然会按 `episode_id` 命名最终成品

返回：

- `list[Path]`

### `upload_files_to_r2(file_paths, remote_folder='', uploader=None, **r2_kwargs)`

参数：

- `file_paths`：本地文件列表
- `remote_folder`：远端目录前缀
- `uploader`：复用已有 `R2Uploader`
- `**r2_kwargs`：没有 `uploader` 时传给 `R2Uploader(...)`

返回：

```python
{
    'bucket_name': str,
    'remote_folder': str,
    'uploaded_keys': list[str],
}
```

### `seed_torrents(files, qb_host, qb_user='admin', qb_pass='', download_base='/downloads')`

做种入口，内部调用 `Publisher.seed_qbittorrent()`。

### `process_episode(...)`

```python
pipe.process_episode(
    episode_dir: Path | str,
    episode_id: str | None = None,
    manual_cuts: dict | None = None,
    direct_model: str | None = None,
    chunked_model: str | None = None,
    mkv_template: str | None = None,
    chs_template: str | None = None,
    cht_template: str | None = None,
    prefix_chs: str | None = None,
    prefix_cht: str | None = None,
    project: ProjectNaming | None = None,
    r2_prefix: str | None = None,
    r2_uploader: R2Uploader | None = None,
    qb_host: str | None = None,
    skip_transcribe: bool = False,
    skip_encode: bool = False,
    skip_package: bool = False,
    skip_upload: bool = False,
    skip_seed: bool = False,
) -> dict
```

这是单集总入口。

关键参数说明：

- `episode_dir`：单集目录
- `episode_id`：不传则自动推断
- `manual_cuts`：`{'01': ['01:30', '22:00']}` 这种按集传切点
- `direct_model` / `chunked_model`：覆盖默认转录模型
- `mkv_template` / `chs_template` / `cht_template`：旧式模板封装
- `prefix_chs` / `prefix_cht`：命名覆盖
- `project`：项目命名对象
- `source_video`：显式指定输入视频；传入时建议同时显式给 `episode_id`
- `chs_subtitle` / `cht_subtitle`：显式指定简繁字幕路径
- `r2_prefix`：R2 目录前缀
- `r2_uploader`：已初始化的上传器
- `qb_host`：若不为空且 `skip_seed=False`，则会进入做种
- `skip_*`：逐阶段跳过开关

阶段顺序：

1. 素材提取（即使输入文件名自定义，提取结果仍按 `episode_id` 命名）
2. AI 转录
3. HEVC 编码（输出仍为 `episode_id_HEVC10bit.mkv`）
4. 字幕校验
5. 封装（最终成品名仍基于 `episode_id`）
6. R2 上传
7. 做种

### 内部辅助

- `_single_episode_dir(workstation, episode_id)`
- `_normalize_workstation(workstation, **kwargs)`

### 调用示例

```python
result = pipe.process_episode(
    episode_dir='/path/to/ep01',
    episode_id='01',
    source_video='raw_source_v2.mkv',
    chs_subtitle='custom_chs.ass',
    cht_subtitle='custom_cht.ass',
    project=project,
    manual_cuts={'01': ['01:30', '22:00']},
    r2_prefix='作品名/01',
    skip_seed=True,
)
print(result)
```

---

# 7.13 `scan.py`

负责最终产物路径计算与扫描。

## `product_path(ep_dir, ep_id, product_key, prefix_chs, prefix_cht=None, config=None)`

根据命名模板推导成品路径。

## `product_torrent_path(video_path)`

根据视频路径推导同名 `.torrent` 路径。

## `check_products(ep_dir, ep_id, prefix_chs, prefix_cht=None, config=None)`

检查当前集各类成品是否存在。

## `scan_products(ep_dir, ep_id, prefix_chs, prefix_cht=None, config=None)`

扫描并返回更完整的产物状态。

这些函数多被 `EpisodeFiles.discover()` 间接调用。

---

# 7.14 `progress.py`

负责显示速度、时间与阶段进度。

## 工具函数

### `_fmt_size(n_bytes)`

字节转人类可读大小。

### `_fmt_time(seconds)`

秒数转可读时间。

---

## `SpeedMeter`

方法：

- `add_bytes(n)`
- `speed()`
- `avg_speed()`
- `speed_str()`
- `avg_speed_str()`
- `total_str()`
- `elapsed()`
- `elapsed_str()`
- `reset()`

用于上传、下载、编码的速率显示。

## `ProgressBar`

### 构造参数

- `label`
- `total`
- `unit='B'`
- `show_speed=True`
- `show_eta=True`
- `bar_format=None`
- `**kwargs`

### 类方法

- `file_upload(filename, total_bytes, unit='B')`
- `file_download(filename, total_bytes, unit='B')`
- `encode(filename, duration_sec, unit='frames')`

### 实例方法

- `update(n=1)`
- `set_postfix(**kwargs)`
- `speed_str()`
- `elapsed_str()`
- `close()`
- `__enter__()` / `__exit__()`

## `StageTimer`

方法：

- `elapsed()`
- `elapsed_str()`
- `is_done()`
- `start()`
- `stop()`
- `add_bytes(n)`
- `bytes_str()`

## `PipelineTimer`

方法：

- `stage(name)`：上下文管理器方式记录阶段
- `stage_start(name)`：手工开始阶段
- `total_elapsed()`
- `summary(width=50)`：打印整条流程耗时总览

---

# 7.15 `seeder.py`

该模块提供远端做种能力封装。

## 数据类与异常

- `SeederError`
- `SeedSubmissionResult`
- `QBTaskStatus`
  - `verdict()`
  - `summary()`

## `RemoteSeeder`

### 构造参数

```python
RemoteSeeder(
    ssh_alias: str,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    download_base: str | None = None,
)
```

### 方法

- `login()`
- `logout()`
- `add_torrent(remote_torrent_path, save_path=None, skip_checking=True, paused=False)`
- `add_magnet(magnet_uri, save_path=None, skip_checking=False, paused=False)`
- `add_magnets(magnet_uris, save_path=None, skip_checking=False, paused=False)`
- `get_torrent_statuses(info_hashes=None, names=None)`
- `query_statuses(info_hashes=None, names=None)`
- `add_torrents(remote_dir_or_paths, save_path=None, skip_checking=True, paused=False, glob_pattern='*.torrent')`
- `upload_and_seed(torrent_paths, remote_dir=None, save_path=None, skip_checking=True, paused=False)`
- `submit_remote_torrents(remote_paths, save_path=None, skip_checking=True, paused=False)`
- `submit_magnets(magnet_uris, save_path=None, skip_checking=False, paused=False)`

### 内部方法

- `_resolve_remote_torrent_paths(...)`
- `__enter__()` / `__exit__()`
- `_load_config(...)`
- `_ssh_run(ssh_alias, cmd, capture=False)`
- `_parse_json_response(raw)`
- `_parse_add_response(raw)`

如果你当前工作流主要在本地 qBittorrent，则优先使用 `Publisher.seed_qbittorrent()`；如果做远端播种自动化，可再接 `RemoteSeeder`。

---

# 7.16 `_backup.py`

负责旧文件备份。

## `_make_backup_dir(parent_dir)`

确保 `_backup/` 目录存在并返回路径。

## `backup_if_exists(file_path, suffix=None)`

如果文件存在，则移动到 `_backup/` 下并加时间戳。

## `backup_path_if_exists(file_path)`

若目标存在则备份，返回备份路径。

这些函数被以下模块广泛调用：

- `subtitle.py`
- `encode.py`
- `package.py`
- `torrent.py`

---

# 7.17 `cli.py`

## `main(argv=None)`

保留兼容入口。当前项目主要推荐 Python API / Notebook 方式，而非 CLI 驱动。

---

# 7.18 `transfer.py`

该模块保留旧传输层的兼容占位定义。

### 异常

- `TransferError`
- `SSHConnectionError`
- `HashVerificationError`
- `CrocTransferError`

### `Transfer.__init__(*args, **kwargs)`

当前不建议作为主路径使用。新版内建上传建议直接使用 `R2Uploader`。

---

## 8. 典型代码片段

### 8.1 先检查再跑单集流程

```python
plan = pipe.plan_episode(work_dir, episode_id='01', project=project)
print(plan.summary())
```

### 8.2 提取音轨与字幕

```python
audio_result = pipe.extract_audio(work_dir, '01')
sub_result = pipe.extract_subtitles(work_dir, '01', smart=True)
print(audio_result)
print(sub_result)
```

### 8.3 转录

```python
transcribe_result = pipe.transcribe_episode(
    work_dir,
    '01',
    direct_model='mlx-community/whisper-large-v3-turbo',
    chunked_model='mlx-community/whisper-medium-mlx',
    manual_cuts=['01:30', '22:00'],
)
print(transcribe_result)
```

### 8.4 编码

```python
hevc_path = pipe.encode_episode(work_dir, '01')
print(hevc_path)
```

### 8.5 字幕标准化

```python
subtitle_result = pipe.validate_subtitles(work_dir, '01', ensure_cht=True)
print(subtitle_result)
```

也可以显式覆盖转换参数：

```python
config = PipelineConfig(
    work_dir=work_dir,
    project=project,
    subtitle_conversion=SubtitleConversionConfig(
        api_url='https://api.zhconvert.org/convert',
        converter='Taiwan',
        timeout=90,
        backup_dir_name='_backup',
        regenerate_existing_cht=True,
    ),
)
pipe = Pipeline(config)
subtitle_result = pipe.validate_subtitles(work_dir, '01', ensure_cht=True)
```

### 8.6 封装

```python
pkg_files = pipe.package_episode(work_dir, '01', project=project)
print(pkg_files)
```

### 8.7 上传到 R2

```python
upload_result = pipe.upload_files_to_r2(
    pkg_files,
    remote_folder='作品名/01',
)
print(upload_result)
```

### 8.8 生成种子

```python
from bmlsub import TorrentCreator

creator = TorrentCreator()
torrent_path = creator.create('/path/to/release_dir', v1_only=True)
print(torrent_path)
```

### 8.9 发布到 qBittorrent

```python
seed_result = pipe.seed_torrents(
    files=pkg_files,
    qb_host='http://127.0.0.1:8080',
    qb_user='admin',
    qb_pass='your-password',
)
print(seed_result)
```

---

## 9. 参数优先级总结

### 9.1 命名优先级

多数涉及输出命名的位置，优先级通常为：

1. 函数参数显式传入的 `prefix_chs` / `prefix_cht`
2. 函数参数显式传入的 `project`
3. `PipelineConfig.project`

### 9.2 R2 配置优先级

1. `R2Uploader(...)` 构造参数
2. 环境变量
3. `~/.config/bml/r2_config.json`

### 9.3 anibt 配置优先级

1. `publish_anibt()` 的 `token` / `api_url`
2. 环境变量
3. `~/.config/bml/anibt_config.json`

### 9.4 `Pipeline.process_episode()` 阶段开关

- `skip_transcribe=True`：跳过 AI 转录
- `skip_encode=True`：跳过 HEVC 编码
- `skip_package=True`：跳过封装
- `skip_upload=True`：跳过 R2 上传
- `skip_seed=True`：跳过做种

---

## 10. 最推荐的实际用法

如果你只想稳定完成一集，推荐下面这套：

```python
from pathlib import Path
from bmlsub import Pipeline, PipelineConfig, ProjectNaming

episode_dir = Path('/Users/miwata/Movies/BML/Project/01')
project = ProjectNaming(
    group='Billion Meta Lab',
    name_chs='作品名',
    name_cht='作品名',
    romaji='Romaji',
)

pipe = Pipeline(PipelineConfig(work_dir=episode_dir, project=project))

print(pipe.plan_episode(episode_dir, episode_id='01', project=project).summary())

result = pipe.process_episode(
    episode_dir=episode_dir,
    episode_id='01',
    project=project,
    r2_prefix='作品名/01',
    skip_seed=True,
)

print(result)
```

如果你在做整季 / 合集，推荐先从：

```python
ws = pipe.build_workstation(root_dir='/Users/miwata/Movies/BML/某项目', episode_ids='01-12')
print(pipe.inspect_workstation(ws).summary())
print(pipe.plan_workstation(ws).summary())
```

开始，先把目录、命名、字幕、原视频全部理顺，再进入单集执行阶段。

如果合集阶段需要顺手完成“缺繁体自动生成 / 简体重建繁体”，可以继续按集调用：

```python
for ep_id in ws.effective_episode_ids:
    ep_dir = pipe._single_episode_dir(ws, ep_id)
    result = pipe.validate_subtitles(ep_dir, ep_id, ensure_cht=True)
    print(ep_id, result)
```

这表示：

- `bmlsub` 本身支持合集 / workstation 模式
- 新增的内置繁化姬能力也可以用于合集
- 当前更推荐的编排方式是：**先 `inspect_workstation()` / `plan_workstation()`，再对需要的阶段逐集执行**

---

## 11. 备注

- 当前代码主路径是 **Python API / Notebook 编排**。
- 上传主路径建议使用 `R2Uploader`。
- 批量工程建议先用 `WorkstationConfig` 做 stage0 检查。
- 合集模式下若需要自动补繁体，可在逐集循环里调用 `validate_subtitles(..., ensure_cht=True)`。
- 任何会覆盖原文件的动作（字幕标准化、重新编码、重新封装、重新生成种子）都内建了备份逻辑。

如果后续还需要，我可以继续把这份 README 再细化成：

1. **仅公开 API 版**（短）
2. **包含内部私有函数说明版**（更长）
3. **按 notebook 实战流程重排版**（最适合日常使用）
