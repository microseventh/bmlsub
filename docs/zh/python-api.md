# Python API 与 Profile

[English](../python-api.md) · [文档首页](README.md)

## 顶层入口

```python
from bmlsub import Pipeline, CredentialService, __version__
```

`Pipeline.__init__` 可注入 `SQLiteJobStore`、`state_dir`、字幕 Converter provider 和 `CredentialService`。

## 工作站公开 API

```python
from bmlsub.workstation import (
    plan_preprocess, run_preprocess,
    plan_delivery, run_delivery, run_delivery_step,
    plan_publish, run_publish, load_status,
)
```

这些入口在现有 `Pipeline` 上编排三阶段单集流程，固定使用 `<episode>/workstation/state`，并导出 config/manifest/summary、逐步 JSON 和 Artifact JSON。`WorkstationConfig.from_series_context()` 从单集直接父级的 `bgminfo/series.json` 构造发布命名、Production Profile、发布配置和 credential alias；显式参数只覆盖对应字段，最终解析值写入 `config.json`。`run_publish()` 只有在 `confirm_external_action=True` 时才执行外部副作用。

`run_delivery_step(step, episode_dir, ...)` 不是状态查询别名，而是真实执行指定步骤。支持 `validate_subtitles_fonts`、`encode_hevc`、`encode_hardsub_chs`、`encode_hardsub_cht`、`mux_subtitles`、`create_torrents`；`all`/`delivery` 转入完整流程。单步骤从 manifest 读取上游 Artifact，不隐式补跑依赖。

## 创建番组元数据

Notebook 或 Python 可直接调用纯函数：

```python
from bmlsub.workstation import create_series_metadata

metadata = create_series_metadata(
    "NipponSangoku",
    parent_dir="/path/to/series-parent",  # 省略时使用 ~/Downloads
    title_chs="日本三国",
    title_cht="日本三國",
    romanized_title="NipponSangoku",
    group_chs="简体制作组",
    group_cht="繁體製作組",
)
```

目标固定为 `<parent_dir>/<series_folder_name>/bgminfo/series.json`。函数在写入前执行与 `SeriesMetadata.load()` 相同的严格 schema、Profile、secret、路径、端口和 ID 校验，使用原子提交，且默认拒绝已有文件；只有 `replace=True` 才替换。番组目录本身允许已经存在。

交互式 Notebook 可先调用 `series_metadata_questions()` 查看问题，再调用 `prompt_series_metadata(input_fn=..., output_fn=...)` 逐项回答。询问函数只负责收集答案，最终仍委托 `create_series_metadata()`。

## `Pipeline` 当前公开方法

### 字幕兼容入口

`validate_subtitles(episode_dir, episode_id, source_video=None, chs_subtitle=None, cht_subtitle=None, ensure_cht=False, converter=None, api_url=None, timeout=None, regenerate_cht=None, full_file=False, fallback_to_full_file=False, force=False)`

`source_video` 和 `fallback_to_full_file` 当前为兼容参数并被忽略；默认不允许自动整文件 fallback。这个方法返回兼容字段（如 `all_ok`、`generated_cht`）以及结构化 Stage 字段。

### 素材和媒体

- `register_video(video, *, workspace, episode_id, purposes, default_for=(), reference=False, ffprobe="ffprobe", probe_timeout=30.0, force=False)`
- `register_source_asset(path, *, workspace, episode_id, kind, language=None, force=False)`，及 subtitle/font/chapter/attachment 便捷方法
- `get_asset()`、`list_assets()`、`resolve_video()`
- `match_assets()`、`confirm_asset_match()`、`get_episode_manifest()`
- `list_media_tracks()`
- `extract_audio_track()`、`extract_subtitle_track()`、`extract_attachments()`

Stage 方法返回 `StageResult.to_dict()`；`get_asset()` 返回 dict/`None`；`list_assets()` 返回 list；`resolve_video()` 返回带 `status`、`needs_review` 和 `artifact` 的 dict。

### 转录和 ASS

- `transcribe(...)`
- `analyze_ass(...)`、`normalize_ass(...)`、`reconstruct_ass(...)`
- analysis 数据方法：load/export/combine/bundle/index/get

Analysis Stage 新输出 `ass-analysis-v4`。`load_ass_analysis(..., allow_legacy=True)` 可读取旧 schema；重建 Stage 的内容输入是 analysis Artifact。

### ProductionRequest

- `create_production_request(...)` → `{"status":"succeeded","request":...}`
- `get_production_request()` → dict/`None`
- `list_production_requests()` → list
- `execute_production_request()` → Stage dict，并附加最新 request

### Release 和 Run

- `create_torrent()`
- `upload_r2()`、`pull_remote()`、`seed_qbittorrent()`、`publish_anibt()`
- `get_run()` → dict/`None`

Python release 方法没有 CLI 的确认 flag。嵌入应用必须在自己的 UI/API 边界确认外部动作；测试应注入 fake client。

## `CredentialService`

当前公开方法：list/get/status、validate、create/update/delete、probe，以及 R2/qB/Anibt/SSH/remote-pull resolver。

- CRUD 返回脱敏 dict；
- `delete_profile(..., confirmed=False)` 会拒绝；
- resolver 返回短生命周期 credentials/reference，不应日志化；
- `probe_profile()` 调用有界只读 probe；Python 调用者自行负责用户确认。

## Production Profile 字段

### `encode + hevc-10bit`

`video_codec` 默认 `hevc_videotoolbox`，也允许 `libx265`；`pixel_format` 为 `p010le` 或 `yuv420p10le`；`quality` 1–100，默认 60；AAC bitrate 允许 128/160/192/256/320k；`include_audio`、`strip_metadata` 为布尔。

### `hardsub + h264-chs/h264-cht`

固定语言由 output profile 决定；codec 仅 `libx264`，preset 为 medium/slow/slower/veryslow，CRF 0–51，tune 仅 film，pixel format 仅 yuv420p，音频仅 AAC。v2 可选受控参数：`refs`、`bframes`、`qcomp`、`rc_lookahead`、`aq_mode`、`aq_strength`、二元 `deblock`、`me_range`、`mbtree`。未知字段和错误类型会拒绝。

### `mux_subtitle + mkv-subtitle`

字段：`include_audio`（默认 true）、`default_subtitle_ordinal`（默认 0，可为 null）、`forced_subtitle_ordinals`（无重复整数数组）。工作站标准内封路径先由 `encode_hevc` 生成 `generated.video.hevc`，再按 `(CHS, CHT)` 顺序创建 mux request；全部登记字体作为 MKV 附件，简体 ordinal 0 默认，forced 默认空。

番组继承的关键规则：完整 delivery 和单步骤都使用最终 `config.delivery.hevc_parameters`、`hardsub_parameters`、`torrent_profile`，不能用 CLI 默认空对象覆盖 `series.json`。

## ASS Profile 字段

`AssAnalysisProfile` 包含 metadata、project_garbage、event_ids、effect_collapse、text_split_rules、style_roles/languages、default_language、long-line/overlap threshold、resolve_registered_fonts 和 profile version。Unknown fields 会拒绝。

`AssReconstructionProfile` 控制分辨率、中/日字体和字号、边距、中文变体、style language/role 映射、需要翻译的语言及 sign readability tags。

## 结果契约

`StageResult.to_dict()` 精确包含：

```text
run_id, stage_name, status, artifacts, diagnostics, error,
retryable, needs_review, reused, started_at, finished_at, duration_ms
```

注意字段名是 `stage_name`，兼容 `validate_subtitles()` 另返回 `stage`。查询方法的返回结构不统一包装为 StageResult，调用方应按各方法说明处理。

## 顶层导出

`bmlsub.__all__` 还公开状态模型、SQLite store、Stage/Process runner、Artifact writer、素材/媒体函数、字幕转换函数、转录模型和 `normalize_h264_parameters()`。未从顶层导出的领域 helper 不作为主要兼容入口。
