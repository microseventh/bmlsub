# 工作站快速模式

[文档首页](README.md) · [CLI 手册](cli.md) · [Python API](python-api.md)

工作站快速模式用于在完整交付流程已经完成部分准备工作的情况下，只执行一个明确步骤。它适合恢复中断任务、按需生成单个产品、复用已经登记的 Artifact，以及把耗时编码拆成可独立运行的操作。

快速模式不是另一套媒体实现，也不是“先执行完整流程，再读取某一步状态”。它直接调用与完整模式相同的 `Pipeline`、ProductionRequest、StageRunner、SQLite 和 Artifact validator。

## 1. 初始化番组配置

在创建数字单集目录前，可以通过 CLI 或 Notebook 创建番组配置：

```bash
bmlsub workstation series create \
  --parent-dir /path/to/series-parent \
  --series-folder-name ExampleSeries \
  --title-chs 示例番组 --title-cht 示例番組 \
  --romanized-title ExampleSeries \
  --group-chs 示例制作组 --group-cht 示例製作組
```

未指定上级目录时默认使用 `~/Downloads`。创建函数严格验证完整 JSON，原子写入，默认拒绝覆盖；Notebook 可直接调用 `create_series_metadata()`，或调用 `prompt_series_metadata()` 进入逐题询问模式。

## 2. 使用条件

单集目录必须是番组根目录的直接数字子目录：

```text
<series-root>/
├── bgminfo/
│   └── series.json
└── 11/
    ├── source.mkv
    ├── 11.chs&jpn.ass
    ├── *.ttf / *.otf / *.ttc
    └── workstation/
```

快速模式从以下位置读取状态：

```text
<episode>/workstation/state/state.sqlite3
<episode>/workstation/state/manifest.json
```

SQLite 是权威状态；`manifest.json` 提供快速编排需要的当前 Artifact ID。仅存在物理文件但没有有效 Artifact，不能满足步骤依赖。

## 3. 番组配置继承

执行时通过单集直接父级的 `bgminfo/series.json` 继承：

- 制作组和简繁番名；
- HEVC Production Profile；
- H.264 hardsub Production Profile；
- ASS 与 Torrent Profile；
- 发布配置和 credential alias。

显式 CLI 参数只覆盖本集对应字段。最终解析值写入 `workstation/state/config.json`。快速模式和完整模式都使用最终的 `config.delivery.hevc_parameters`、`hardsub_parameters` 和 `torrent_profile`，不会让 CLI 默认空 JSON 覆盖番组 Profile。

## 4. 命令和步骤

```bash
bmlsub workstation delivery \
  --workspace /path/to/series/11 \
  --episode-id 11 \
  --step STEP
```

| STEP | 作用 | 必需的已登记上游 |
|---|---|---|
| `validate_subtitles_fonts` | 将已有 ASS/font analysis 记录为非阻断诊断成功 | `font-report.json` |
| `encode_hevc` | 从直接源视频生成 HEVC 10-bit 中间文件 | source video Artifact |
| `encode_hardsub_chs` | 生成简体 H.264 内嵌 MP4 | source video、简体字幕、字体 Artifacts |
| `encode_hardsub_cht` | 生成繁体 H.264 内嵌 MP4 | source video、繁体字幕、字体 Artifacts |
| `mux_subtitles` | 将 HEVC、简繁字幕和字体封装为 MKV | HEVC、简体、繁体、字体 Artifacts |
| `create_torrents` | 为已登记的三类正式产品创建 Torrent | 三类产品 Artifacts |

`--step all` 和 `--step delivery` 表示完整交付流程，不属于快速单步骤执行。

## 5. 关键步骤逻辑

### 5.1 非阻断字体诊断

Aegisub Fonts Collector 是字体 family、粗体/斜体变体和 glyph 完整性的权威边界。BMLSub 的 ASS analysis 只提供诊断，不用 missing variant 或 missing glyph 计数阻断视频生产。

报告存在时，步骤记录为：

```text
status = succeeded
blocking = false
validation_owner = aegisub
```

BMLSub 仍会把字体作为视频的真实 Artifact 输入，并在 MKV 中验证附件数量和身份。

### 5.2 HEVC 中间文件

```text
source.video
  → production.encode_hevc
  → generated.video.hevc
  → workstation/delivery/intermediate/<episode>_HEVC10bit.mkv
```

成功后，HEVC Artifact ID 写入 `manifest.products.hevc_intermediate_artifact_id`。步骤使用 ffprobe 验证 codec、Main 10/bit depth、分辨率、时长和音频要求。

### 5.3 简繁内封 MKV

```text
generated.video.hevc
workstation.subtitle.chs
workstation.subtitle.cht
source.font × N
        ↓
production.mux_subtitle
        ↓
generated.video.muxed
```

默认字幕顺序：

1. `zh-Hans`：默认轨；
2. `zh-Hant`：非默认轨。

默认没有 forced 字幕。全部登记字体作为 Matroska 附件加入，输出同时由 mkvmerge identify 和 ffprobe 验证。

### 5.4 简繁 MP4

简体和繁体 hardsub 始终从直接源视频并列分叉，不会从 HEVC 中间文件二次编码：

```text
source.video + CHS ASS + fonts → CHS MP4
source.video + CHT ASS + fonts → CHT MP4
```

可以分别执行：

```bash
bmlsub workstation delivery --workspace /path/to/series/11 --step encode_hardsub_chs
bmlsub workstation delivery --workspace /path/to/series/11 --step encode_hardsub_cht
```

如果当前批次只需要内封 MKV，两条 MP4 request 可以保持 `pending`，不应伪记为失败。

## 6. 依赖、复用和失败语义

快速模式不会隐式补跑依赖。例如没有 `hevc_intermediate_artifact_id` 时执行 `mux_subtitles` 会失败，而不会先自动编码 HEVC。

每个步骤继续遵守 StageRunner 规则：

1. 重新验证输入 Artifact；
2. 登记实际 `stage_inputs`；
3. 按输入、参数和工具指纹查找历史成功；
4. 历史输出仍有效时返回 `skipped`、`reused=true`；
5. 输入或工具变化时创建新的执行；
6. 输出验证成功后才登记 Artifact 和更新 manifest。

可使用 `--force` 强制重跑，但不会绕过输入和输出验证。

## 7. 状态和批次快照

每个步骤写入：

```text
workstation/state/steps/delivery.<step>.json
workstation/state/artifacts/<artifact-id>.json
workstation/state/manifest.json
workstation/state/summary.json
```

发布准备还可以冻结：

```text
workstation/state/credentials-status.json
workstation/state/release-batch.json
```

`credentials-status.json` 只保存 credential alias 的脱敏可用性，不保存密钥。`release-batch.json` 记录本轮包含和延后的产品、Artifact 及验证结果。`ready_for_torrent` 只表示本地媒体已准备好，不表示已经创建 Torrent 或执行外部发布。

## 8. 可复现验收要求

在临时或自有单集 workspace 中，可按以下标准验收快速模式：

1. 字体诊断步骤为非阻断成功；
2. HEVC 输出通过 ffprobe 的 Main 10、分辨率、时长和音频检查；
3. 内封 MKV 的字幕顺序为 `zh-Hans`、`zh-Hant`，默认/forced 标志符合 Profile；
4. mkvmerge 和 ffprobe 报告的字体附件数量与输入字体 Artifact 数量一致；
5. 重复执行在输入、参数和工具不变时返回 `skipped/reused=true`；
6. 输入或 Profile 改变后产生新执行，不复用旧结果；
7. 未满足依赖时只失败当前步骤，不隐式运行完整流水线。
