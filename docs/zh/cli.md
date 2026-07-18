# CLI 手册（以当前 parser 为准）

[English](../cli.md) · [文档首页](README.md)

## 输出和退出码

CLI 捕获 Python 调用期间写入 stdout 的附带内容并转发到 stderr，stdout 最终只打印一个 JSON 文档。退出码由最终 payload 决定：`status=needs_review` 为 `2`；`status=failed` 或存在 `error` 为 `1`；其他为 `0`。

## `episode validate`

| 参数 | 必需/默认 | 说明 |
|---|---|---|
| `--episode-dir` | 默认 `.` | workspace |
| `--episode-id` | 必需 | episode 标识 |
| `--chs-subtitle` / `--cht-subtitle` | 可选 | 未提供时按兼容命名规则发现 |
| `--ensure-cht` | false | 执行 CHS→CHT Stage |
| `--converter` | `Taiwan` | Provider converter |
| `--conversion-api-url` | zhconvert URL | HTTP Provider |
| `--conversion-timeout` | `60` | 秒 |
| `--full-file-hanvert` | false | 显式整文件高风险模式 |
| `--no-full-file-fallback` | deprecated | 兼容 flag；默认已经不 fallback |
| `--regenerate-cht` / `--keep-existing-cht` | 互斥 | 控制已有 CHT |
| `--state-dir`、`--force` | 可选 | 状态目录/重跑 |

## `asset`

### `register-video`

必需：`--episode-id`、`--video`、至少一个可重复 `--purpose`。可选：`--workspace .`、可重复 `--default-for`、`--reference`、`--ffprobe ffprobe`、`--probe-timeout 30.0`、`--state-dir`、`--force`。

### 其他登记

- `register-subtitle --subtitle PATH [--language LANG]`
- `register-font --font PATH`
- `register-chapter --chapter PATH [--language LANG]`
- `register-attachment --attachment PATH`

都要求 `--episode-id`，workspace 默认 `.`，支持 `--state-dir` 和 `--force`。

### 匹配和查询

- `match`：要求 episode、video Artifact；`--role` 可重复，未提供时使用 subtitle/font/chapter/attachment；`--replace-confirmed` 可替换已确认关系。
- `confirm`：要求 video Artifact、一个 role、至少一个可重复 `--artifact-id`。
- `manifest`：按 episode 返回素材、候选和确认关系。
- `show ARTIFACT_ID`：返回一个当前 Artifact，不存在时失败。
- `list`：可按 `--episode-id` 和 `--type` 过滤。

## `media`

所有 media 命令要求 `--episode-id`，并且在 `--video-artifact-id` 与 `--purpose` 中二选一。

- `tracks [--kind audio|subtitle]`
- `extract-audio [--stream-index N] [--language LANG] [--mode archive|transcribe|both]`，mode 默认 `both`，process timeout 默认 600 秒。
- `extract-subtitle [--stream-index N] [--language LANG]`，process timeout 默认 300 秒。
- `extract-attachments`：提取全部内嵌附件，默认不由其他提取命令隐式执行。

提取命令还接受 `--output-dir`、`--ffmpeg ffmpeg`、`--ffprobe ffprobe`、`--probe-timeout 30.0`、`--force`。

## `subtitle`

### `analyze-ass` / `normalize-ass`

共同必需：`--episode-id`、`--subtitle-artifact-id`。可选：`--video-artifact-id`、可重复 `--font-artifact-id`、`--profile-json {}`、`--state-dir`、`--force`。Analyze 可指定 `--output`；normalize 可指定 `--output` 和 `--analysis-output`。

### `reconstruct-ass`

要求 `--episode-id` 和 `--analysis-artifact-id`；可选 `--profile-json {}`、`--output`、`--state-dir`、`--force`。

Profile 的真实字段见[Python API 与 Profile](python-api.md)。

## `transcribe`

必需：`--episode-id`、`--audio-artifact-id`。默认值：mode `direct`、model `mlx-community/whisper-large-v3-turbo`、revision `main`、language `ja`、chunk 240 秒、overlap 5 秒、throttle 0、decoding `{}`、ffmpeg `ffmpeg`、timeout 600 秒。`--manual-cut` 可重复，并由 `parse_timestamp()` 解析。

## `production`

### `create`

要求 episode 和 video Artifact。operation choices：`encode`（默认）、`hardsub`、`mux_subtitle`；output profile choices：`hevc-10bit`（默认）、`h264-chs`、`h264-cht`、`mkv-subtitle`。

可重复字幕、字体、附件 Artifact；章节最多一个；`--parameters-json` 默认 `{}`。CLI 在 hardsub 时只把第一份字幕作为输入，在 mux 时保留全部字幕；最终契约仍由 ProductionRequest validator 检查。

### `show` / `list` / `execute`

`show REQUEST_ID`、`list [--episode-id]` 为只读。`execute REQUEST_ID` 默认使用 ffmpeg/ffprobe/mkvmerge，process timeout 7200 秒、probe timeout 30 秒，支持 `--force`。

## `credentials`

- `import-json --input PATH [--manifest PATH] [--replace]`
- `upsert-secret --alias --kind r2|qbittorrent|anibt --input PATH [--settings-json {}] [--replace]`
- `list`、`get --profile`
- `create --alias --kind r2|qbittorrent|anibt|ssh|remote_pull [--input] [--settings-json {}] [--label] [--description]`
- `update --profile`，可改 alias/kind/settings/secret/label/description
- `delete --profile --confirm-delete`
- `status`、`validate`
- `probe --profile [--connection-profile] [--probe-json {}] [--ssh ssh] --confirm-external-action`

`--confirm-external-action` 由 parser 强制要求，但 probe 实现本身只执行有界只读检查。

## `release`

- `create-torrent`：本地操作；要求 content Artifact；profile 默认 `{}`；tracker timeout 可覆盖 Profile。
- `upload-r2`：要求 artifact 和 Profile JSON；可选择环境变量名、0600 credential file 或 manifest+profile；强制 `--confirm-external-action`。
- `pull-remote`：要求 content Artifact、R2 receipt Artifact 和 Profile；可选 connection manifest/SSH profile；强制确认。
- `seed-qbittorrent`：要求 torrent/content/remote-content 三个 Artifact 和 Profile；支持 qB credential 与 SSH profile；强制确认。
- `publish-anibt`：要求 torrent Artifact 和 Profile；支持 token env、0600 config 或 credential profile；强制确认。

精确 Profile 字段见[发布](release.md)。

## `workstation series show | create`

- `workstation series show --workspace <数字单集目录>`：从单集直接父级读取、严格验证并显示 `bgminfo/series.json`。
- `workstation series create`：创建 `<上级目录>/<番组文件夹名>/bgminfo/series.json`。`--parent-dir` 未提供时默认使用当前用户的 `~/Downloads`；必须提供番组文件夹名、简繁番名、罗马音名和简繁制作组。可选 `--bgm-id`、`--anime-id`、`--production-json`、`--publish-json`。
- 默认拒绝覆盖已有 `series.json`；只有显式 `--replace` 才执行原子替换。番组目录本身可以已经存在。
- `--interactive` 进入询问模式，逐项收集相同字段；询问写到 stderr，stdout 仍只输出最终 JSON。交互模式不能和字段/Profile JSON 参数混用。

## `workstation preprocess | delivery | publish | status`

- `workstation preprocess --workspace EPISODE [--episode-id ID]`：只在顶层源视频唯一时自动选择，提取一个英语参考字幕、日语音频，并可执行配置的 Whisper job。
- `delivery`：默认从直接父级 `bgminfo/series.json` 继承制作组、番名和 Production Profile；显式 CLI 参数仅作为本集覆盖。完整模式验证正式简日 ASS 和顶层 Aegisub 字体包，生成工作站简繁字幕、非阻断字体诊断、三类视频和同完整文件名 Torrent。
- `delivery --step STEP`：真实单步骤执行，choices 为 `validate_subtitles_fonts`、`encode_hevc`、`encode_hardsub_chs`、`encode_hardsub_cht`、`mux_subtitles`、`create_torrents`；`all`/`delivery` 表示完整流程。单步骤只消费 `manifest.json` 已登记的上游 Artifact，不会隐式先跑完整 delivery；缺少依赖时失败。
- `publish --publish-config-json JSON [--confirm-external-action]`：未确认时返回 `awaiting_confirmation`，不调用 R2、SSH、qB 或 Anibt。
- `status [--step STEP]`：读取 `workstation/state` 的可读快照。SQLite 权威状态固定为 `workstation/state/state.sqlite3`。解析后的配置写入 `config.json`；凭证可用性和发布批次可分别冻结为 `credentials-status.json`、`release-batch.json`。

## `run show`

`run show RUN_ID [--workspace .] [--state-dir PATH]` 调用 `SQLiteJobStore.get_run_detail()`，只读返回 Run 及关联 Stage、输入和 Artifact 详情，不提供取消或队列控制。
