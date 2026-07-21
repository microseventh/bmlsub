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

## `workstation start`

```bash
bmlsub workstation start [--series-root PATH] [--episode-id ID] [--execute]
```

统一入口先解析番组根目录；省略 `--series-root` 时使用当前目录，也可传入数字单集目录并解析其直接父目录。交互式 TTY 启动后首先选择“中文 / English”，直接按 Enter 明确默认使用中文；本次命令之后的所有问答和摘要统一使用所选语言。所有可采用默认值的输入都会完整提示“直接按 Enter 使用默认值：…”，而“留空表示不设置”的字段会单独说明。非交互模式不增加语言问答，stdout JSON、状态码和配置字段保持不变。命令严格检查 `bgminfo/series.json`，交互终端缺失时进入问答创建；`--init-template` 只生成 `bgminfo/series.template.json`。配置有效后列出直接数字子目录，选择单集，并优先依据 SQLite/manifest/summary、否则依据顶层物理文件保守识别阶段。

当识别到人工交接已完成、进入本地生产时，`start` 会先显示正式字幕、字体、简繁命名、继承的 HEVC/H.264 Profile、目标路径和可复用产物，然后询问压制范围：`1` 完整压制（默认，三个视频和 Torrent）、`2` 仅简繁内封 MKV、`3` 仅简繁内嵌 MP4、`4` 自定义产品。局部范围还会询问是否制作所选产品的 Torrent，最后只做一次总确认；确认前不会登记 Artifact 或启动编码。

非交互模式可使用 `--delivery-scope full|mkv|mp4|custom`、重复的 `--delivery-product mp4_chs|mp4_cht|mkv_hevc` 和 `--delivery-torrents selected|none`。规划只读取 `bgminfo` 和当前文件，执行仍复用 StageRunner；再次运行会跳过有效产物。局部生产不会触发发布，只有三类视频和 Torrent 都完成后才进入 publish。

普通 `start` 在本地生产完成后只报告已完成状态，并明确提示下一步使用 `bmlsub workstation start delivery`；它不会执行文件上传、远程拉取、做种或 Anibt 发布。

## Workstation 快速模式入口

普通用户只需要记住三个命令：

```bash
# Workstation 交互式快速模式
bmlsub workstation start

# Workstation 交互式外部交付
bmlsub workstation start delivery

# 外部交付无人值守模式
bmlsub workstation start delivery -y
```

单独的 `--series-root`、`--episode-id`、`--execute`、`--transcription` 和 delivery selection 参数属于高级参数化调用，不是普通快速模式的必需输入。交互式快速模式会在终端中完成单集、转录策略、本地产品范围和执行确认的选择。


### `workstation start delivery`

高级参数形式：

```bash
bmlsub workstation start delivery \
  [--configure] [-y|--yes] [--resume|--restart] [--verbose-plan] [--force]
```

默认模式先检查 Credential Manifest、macOS Keychain 中的 R2/qB/Anibt Profile、SSH alias 和公开路径配置，然后只打印一次简洁摘要。`--configure` 在 TTY 中强制进入凭证/交付配置向导；`-y/--yes` 使用现有配置和 Keychain，不询问 Secret 或确认，适合无人值守外部交付。正式链路中视频和 `.torrent` 都先上传 R2，再分别拉到 VPS 平铺目录并验证；qB Web API 使用本地已经由 libtorrent 验证的同一 Torrent Artifact 上传，远端 Torrent receipt 作为 R2/VPS 副本一致性的必需证据，不再通过 SSH 把二进制种子读回本机。`publish.remote_root` 严格表示 VPS 宿主机平铺目录，例如 `/data/dcapp/qb/downloads`；`publish.qb_save_path` 严格表示 qB Docker 容器内目录，默认 `/downloads`，两者由 Docker volume 映射连接相同文件名。若已有同 hash/name/size 的不完整任务位于已知错误宿主机路径，只删除 qB 任务记录（`deleteFiles=false`）后按容器路径重加；其他未知 save path 一律阻断。R2 object key 继续按 `<series>/<episode>/<filename>` 分层。若公开发布配置或 credential alias 不完整，TTY 模式可进入配置问答：优先列出并复用本机已有可用 Profile；首次缺少 Credential Manifest 时创建非敏感 `0600` Manifest；新建 R2/qB/Anibt Profile 时用隐藏输入把 Secret 直接存入 macOS Keychain，SSH 只引用 `~/.ssh/config`。非敏感路径和 alias 经确认后原子写入 `series.json`，已有 NOTE 和 Production 配置保持不变。

配置完成仍不会自动发布，而是回到简洁摘要和交付确认。交互式外部交付按全部 R2 → 全部 VPS 拉取 → 全部 qB → 全部 Anibt 的顺序逐产品确认；`-y` 自动接受这些确认。每类 Profile 都可在 `--configure` 向导中选择复用 available、修复 unavailable 或新建。R2 新建/修复会询问 Account ID、Access Key ID 和隐藏输入的 Secret Access Key，并直接写入 macOS Keychain。`-y` 保留 Stage 指纹和 receipt 复用，不等于 `--force`；凭证缺失时返回 `needs_review`。自定义 Credential Manifest 可用 `--credential-manifest PATH`，该运行时路径不会写入 `series.json`。

预处理执行前会选择转录策略：`quick` 只执行一次 direct；`full`（默认）分别登记 direct 完整转录和 chunked 切片转录；`none` 不调用 Whisper，但仍生成归档音频和转录 WAV。参数模式使用 `--transcription quick|full|none`。
## `workstation rebuild`

```bash
bmlsub workstation rebuild --series-root PATH --episode-id ID --target TARGET --confirm-rebuild
```

TARGET 可为完整 `preprocess`、完整 `delivery`，或一个真实 delivery 单步。重建始终使用底层 `force=True`，保留历史和 validator，不删除状态；没有 publish target，不会重做 R2、远端、qB 或 Anibt。交互模式可选择单集、范围和转录策略。

番组初始化只输入简体番名和简体制作组；繁体字段由 Taiwan 转换 provider 自动生成。转换失败会把原文、错误和 pending 状态写入 `bgminfo/series.json`，不会伪造繁体值。下次 `workstation start` 可重试，或使用：

```bash
bmlsub workstation series retry-traditionalization --series-root PATH
```

繁化未完成时允许查看配置，但阻断需要正式繁体命名的本地生产和发布。

## `workstation series show | create`

- `workstation series show --workspace <数字单集目录>`：从单集直接父级读取、严格验证并显示 `bgminfo/series.json`。
- `workstation series create`：创建 `<上级目录>/<番组文件夹名>/bgminfo/series.json`。`--parent-dir` 未提供时默认使用当前用户的 `~/Downloads`；必须提供番组文件夹名、简繁番名、罗马音名和简繁制作组。可选 `--bgm-id`、`--anime-id`、`--production-json`、`--publish-json`。
- 默认拒绝覆盖已有 `series.json`；只有显式 `--replace` 才执行原子替换。番组目录本身可以已经存在。
- `--interactive` 进入询问模式，逐项收集相同字段；询问写到 stderr，stdout 仍只输出最终 JSON。交互模式不能和字段/Profile JSON 参数混用。

## `workstation preprocess | delivery | publish | status`

- `workstation preprocess --workspace EPISODE [--episode-id ID]`：只在顶层源视频唯一时自动选择，提取一个英语参考字幕、日语音频，并可执行配置的 Whisper job。
- `delivery`：默认从直接父级 `bgminfo/series.json` 继承制作组、番名和 Production Profile；显式 CLI 参数仅作为本集覆盖。完整模式验证正式 `<集数>.CHS&JPN.ass` 和顶层 Aegisub 字体包，并检查可选的正式 `<集数>.CHT&JPN.ass`（文件名大小写不敏感）。已有繁体字幕时直接登记并用于 `h264-cht` 和 MKV 繁体字幕轨；没有时才通过配置的台湾繁化服务从简体字幕生成。随后执行非阻断字体诊断，生成所选视频和同完整文件名 Torrent。也可用 `--delivery-scope mkv|mp4|custom` 只生成所选产品；`--delivery-torrents none` 跳过 Torrent。局部完成返回 `partial`，不会伪记为完整本地生产。
- `delivery --step STEP`：真实单步骤执行，choices 为 `validate_subtitles_fonts`、`encode_hevc`、`encode_hardsub_chs`、`encode_hardsub_cht`、`mux_subtitles`、`create_torrents`；`all`/`delivery` 表示完整流程。单步骤只消费 `manifest.json` 已登记的上游 Artifact，不会隐式先跑完整 delivery；缺少依赖时失败。
- `publish --publish-config-json JSON [--confirm-external-action]`：未确认时返回 `awaiting_confirmation`，不调用 R2、SSH、qB 或 Anibt。
- `status [--step STEP]`：读取 `workstation/state` 的可读快照。SQLite 权威状态固定为 `workstation/state/state.sqlite3`。解析后的配置写入 `config.json`；凭证可用性和发布批次可分别冻结为 `credentials-status.json`、`release-batch.json`。

## `run show`

`run show RUN_ID [--workspace .] [--state-dir PATH]` 调用 `SQLiteJobStore.get_run_detail()`，只读返回 Run 及关联 Stage、输入和 Artifact 详情，不提供取消或队列控制。
