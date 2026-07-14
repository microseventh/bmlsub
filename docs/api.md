# API 摘要

完整公开对象可从 `bmlsub` 顶层导入。本页聚焦最常用的配置和流程入口。

## 配置

### `PipelineConfig`

流水线总配置，常用字段：

- `work_dir`
- `whisper_fast_model`
- `whisper_detailed_model`
- `language`
- `hevc_preset` / `x264_preset`
- `sub_standard`
- `subtitle_conversion`
- `chunk_sec` / `overlap_sec`
- `output_transcripts_dir`
- `project`

### `ProjectNaming`

单项目命名配置：

```python
ProjectNaming(
    group="Billion Meta Lab",
    name_chs="作品名",
    name_cht="作品名",
    romaji="Romaji",
)
```

提供 `prefix_chs` 和 `prefix_cht` 属性。

### `ProjectConfig`

当前目录 `bmlsub-project.json` 的结构化表示，保存项目命名、集号以及 `r2_prefix`、`bgm_id`、`notes`、`qb_host` 等非敏感发布字段。

常用 helper：

- `project_config_path(directory=None)`
- `load_project_config(directory=None)`
- `save_project_config(config, directory=None, overwrite=False)`
- `PROJECT_CONFIG_FILENAME`

`load_project_config()` 在文件不存在时返回 `None`，格式错误或 schema 不支持时抛出 `ValueError`。

### `WorkstationConfig`

合集目录与命名配置。`episode_ids` 支持列表、逗号列表或范围；留空时从 `RAW/*.mkv` 推断。

常用属性和方法：

- `effective_episode_ids`
- `ep_range`
- `raw_dir` / `sub_dir` / `sub_tj_dir`
- `hevc_pack_dir` / `chs_pack_dir` / `cht_pack_dir`
- `source_video(ep_id)`
- `resolve_chs_sub(ep_id)` / `resolve_cht_sub(ep_id)`
- `stage0_checks()` / `summary()`

### `parse_episode_ids(value)`

```python
parse_episode_ids("01-03")       # ["01", "02", "03"]
parse_episode_ids("01,03,05")    # ["01", "03", "05"]
```

## ASS 文本与语言分析

### `strip_ass_tags(text)`

返回用于统计分析的可见文本：移除 `{...}` 覆盖标签和 `\p` 绘图坐标，把 `\N` / `\n` 转为实际换行、`\h` 转为空格。

```python
from bmlsub import strip_ass_tags

strip_ass_tags(r"这{\b1}里面{\b0}有东西")  # "这里面有东西"
```

### `extract_ass_analysis(ass_path, output_path=None, include_comments=False)`

按动态 `[Events] Format` 解析指定 ASS，尝试 `utf-8-sig`、`utf-8`、`gbk`、`shift-jis` 编码，并返回：

- `file`：路径、编码、事件格式和样式名；
- `summary`：Dialogue/Comment、语言、字符、标签、换行、绘图统计；
- `languages`：`zh`、`ja`、`mixed`、`other` 事件明细。

语言识别会综合分隔符边界明确的样式标记与文本证据；含假名的 CN 样式不会直接按中文处理，`NINJA`、`PROJECT` 等普通单词后缀也不会误命中语言标记。Sign、Note 等中性样式按文本内容判断。指定 `output_path` 时同步写 UTF-8 JSON。

### `convert_ass_with_fanhuaji(content, ...)`

默认仅把中文 Dialogue 的去标签文本送往繁化姬，然后恢复原标签、转义和事件结构。`full_file=True` 会跳过 ASS 分析并直传完整文件；默认的 `fallback_to_full_file=True` 会在 Events 无法解析或无法可靠找到转换候选时自动采用完整文件繁化。多标签文本节点发生长度变化时抛出 `HanvertConversionError`，避免标签边界错位。函数返回 `(converted_content, stats)`；日常流水线通常通过 `SubtitleValidator.convert_chs_to_cht()` 调用。

## `Pipeline`

### 发现与规划

- `context(...) -> EpisodeFiles`
- `inspect_episode(...) -> dict`
- `plan_episode(...) -> EpisodeStagePlan`
- `inspect_workstation(...) -> WorkstationStage0Summary`
- `plan_workstation(...) -> WorkstationBatchResult`

这些入口适合在执行耗时或外部操作前检查输入和预期输出。

### 单集阶段

- `extract_audio(episode_dir, episode_id, ...) -> dict`
- `extract_subtitles(episode_dir, episode_id, smart=False, ...) -> dict`
- `extract_media(episode_dir=None, episodes=None, smart_subs=True) -> dict`
- `transcribe_episode(...) -> dict`
- `encode_episode(...) -> Path`
- `validate_subtitles(..., ensure_cht=False, ...) -> dict`
- `package_episode(...) -> list[Path]`
- `upload_files_to_r2(file_paths, remote_folder="", ...) -> dict`
- `seed_torrents(files, qb_host, ...) -> dict[str, bool]`

### 单集总入口

`process_episode(...) -> dict` 按以下顺序编排：

1. 音轨和字幕提取
2. AI 转录
3. HEVC 编码
4. 字幕校验
5. 封装
6. R2 上传
7. qBittorrent 做种

使用 `skip_transcribe`、`skip_encode`、`skip_package`、`skip_upload`、`skip_seed` 控制阶段。

### 合集入口

- `validate_workstation_subtitles(...)`
- `encode_workstation_hevc(...)`
- `build_release_batch(...)`

当前 `encode_workstation_hevc()` 生成批量编码输入/输出规划；`build_release_batch()` 构建发布目录的 torrent 计划。

## `EpisodeFiles`

统一发现某集的源视频、HEVC 视频、字幕、字体、提取音轨和预期产物。

```python
ctx = EpisodeFiles.discover(
    episode_dir="/path/to/01",
    episode_id="01",
    source_video="/path/to/01/raw_source_v2.mkv",
    chs_subtitle="/path/to/01/custom_chs.ass",
    cht_subtitle="/path/to/01/custom_cht.ass",
)
print(ctx.summary())
```

## 媒体与转录

- `MediaExtractor`：探测和提取音轨/字幕轨
- `Transcriber`：直接转录、分段转录和双模型转录
- `Encoder`：HEVC VideoToolbox、x264 和元数据清理

## 字幕与封装

- `SubtitleValidator`：ASS 头校验、标准化、简繁转换和繁体保障
- `Packager`：构建封装计划、MKV 内封和 MP4 硬压
- `PackagingError` / `SubtitleConversionError`：对应阶段异常

字幕原地标准化或重建繁体可能产生备份文件；执行前应确认目标路径。

## 上传、种子与发布

- `R2Uploader`：Cloudflare R2 上传
- `TorrentCreator`：torrent 计划和创建
- `read_torrent_metadata()`：读取 torrent 与磁力信息
- `RemoteSeeder` / `Publisher`：远端做种与发布辅助

这些 API 会访问外部服务或创建发布产物，应由调用方显式配置凭证和目标。

## 返回对象

以下数据类提供 `summary()`：

- `StageStatus`
- `EpisodeStagePlan`
- `WorkstationStage0Summary`
- `WorkstationBatchResult`

CLI 会自动把这些对象以及 `Path`、列表和字典序列化为 JSON 友好结构。
