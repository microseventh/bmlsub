# 发布链与 Profile（当前实现）

[English](../release.md) · [文档首页](README.md)

## Workstation 交付模式

普通用户先使用 Workstation 交互式快速模式：

```bash
bmlsub workstation start
```

本地产品和 Torrent 完成后，使用交互式外部交付：

```bash
bmlsub workstation start delivery
```

已有验证有效的凭证和配置时，可使用无人值守外部交付：

```bash
bmlsub workstation start delivery -y
```

两种交付模式都按 R2 → VPS 拉取 → qB 做种 → Anibt 发布的顺序评估。交互模式会询问 Anibt 账户是否已经获得 Nyaa 代发白名单；回答“是”会将三种产品全部通过同一次 multipart Torrent 请求同步到 Nyaa，分类固定为 `1_4`，回答“否”则保持仅发布 Anibt。`-y/--yes` 默认启用 Nyaa 同步并自动接受全部外部交付确认。未显式提供 `nyaaDescription` 时，由 Anibt 使用本站 `notes` 作为 Nyaa 说明。两种模式都保留 Stage 指纹、Artifact、receipt 和 live validator 检查，因此有效结果会复用。无人值守模式不是强制重跑；只有显式 `--force` 才要求重新执行 Stage。`--resume` 和 `--restart` 表达恢复意图，但都不会删除远端文件或撤回发布。

执行前会检查 Credential Manifest、macOS Keychain 中的 R2/qB/Anibt payload、SSH identity、公开路径和本地输入。凭证缺失或无效时，无人值守模式返回 `needs_review`，不会要求或输出明文 Secret。

## TorrentProfile

```json
{
  "format": "hybrid",
  "piece_length": null,
  "private": false,
  "comment": "",
  "created_by": "BML",
  "tracker_best_url": "https://ngosang.github.io/trackerslist/trackers_best.txt",
  "tracker_timeout": 15.0
}
```

`format` 仅 `hybrid`/`v1`。显式 piece length 仅允许 64 KiB、256 KiB、1 MiB、4 MiB、8 MiB。libtorrent 是创建、读取和 validator 后端。

```bash
bmlsub release create-torrent \
  --workspace /path/to/workspace --episode-id 01 \
  --content-artifact-id ARTIFACT_ID \
  --profile-json '{"format":"hybrid"}'
```

## R2UploadProfile

必需 `bucket`、`object_key`；默认 content type 为 `application/octet-stream`、access private、multipart threshold/chunk 50 MiB、concurrency 3。Public access 要求 clean HTTPS `public_base_url`。

```bash
bmlsub release upload-r2 \
  --workspace /path/to/workspace --episode-id 01 \
  --artifact-id ARTIFACT_ID \
  --profile-json '{"bucket":"example-bucket","object_key":"releases/01.mkv","content_type":"video/x-matroska"}' \
  --credential-profile r2-main \
  --confirm-external-action
```

Pipeline 要求 manifest 与 profile alias 成对；不传 profile 时解析 env/安全 JSON。上传后 adapter 使用 HEAD 验证远端对象并生成 receipt Artifact。

## RemotePullProfile

全部必需字段：`ssh_alias`、`rclone_remote`、`bucket`、`object_key`、绝对规范化 `target_path`；timeout 默认 3600 秒、范围 1–86400。

```json
{
  "ssh_alias": "media-vps",
  "rclone_remote": "r2",
  "bucket": "example-bucket",
  "object_key": "releases/01.mkv",
  "target_path": "/srv/media/01.mkv"
}
```

如果传 connection manifest/SSH profile，Pipeline 会解析 alias，并拒绝与 Profile 中冲突的 `ssh_alias`。

## QBittorrentSeedProfile

`ssh_alias` 必需。默认 host `127.0.0.1`（只允许远端 loopback）、port 8080、容器内 save path `/downloads`、poll 2 秒/1800 秒、允许 v1 magnet fallback。可选 clean HTTPS `webui_origin`、category、tags。工作站发布时，`publish.remote_root` 是 VPS 宿主机目录，`publish.qb_save_path` 是 qB Docker 容器目录；例如宿主机 `/data/dcapp/qb/downloads` 通过 volume 映射到容器 `/downloads`。

```json
{"ssh_alias":"media-vps","save_path":"/downloads"}
```

添加请求显式发送 `paused=false`、`skip_checking=false`、`sequentialDownload=false`、`firstLastPiecePrio=false` 和 `root_folder=false`，随后优先调用 qB v5 `start`（404 时回退旧版 `resume`）并执行 recheck。升级前若同一 hash/name/size 任务仍错误地使用宿主机 `remote_root`，工作站只删除任务记录（`deleteFiles=false`）后按容器路径重加；任意其他未知 save path 仍会阻断，不自动删除。

Stage 同时消费 torrent、原内容和 remote-content receipt Artifact。成功条件由 adapter 对 qB 任务和内容状态验证。

## AnibtPublishProfile

当前字段名和枚举：

- `anime_id_type`: `bgm|anilist|mal|anidb`，默认 bgm；
- 必需非空 `anime_id`、`title`；
- `resolution`: 2160p/1080p/720p/480p/360p；
- `language`: CHS/CHT/JP/EN 等代码数组；
- `subtitle`: `EXTERNAL|INTERNAL|EMBEDDED|NONE`；
- `format`: `MKV|MP4|AVI|WEBM`；
- 可选 bgm_id、episode_key、version、file_size、trackers、notes；
- `preview`、Nyaa 字段；`use_torrent_file` 必须为 true。

```bash
bmlsub release publish-anibt \
  --workspace /path/to/workspace --episode-id 01 \
  --torrent-artifact-id TORRENT_ARTIFACT_ID \
  --profile-json '{"anime_id_type":"bgm","anime_id":"123456","title":"Example","episode_key":"01","resolution":"1080p","language":["JP","CHS"],"subtitle":"INTERNAL","format":"MKV","preview":true}' \
  --credential-profile anibt-main \
  --confirm-external-action
```

Preview 与正式发布因参数指纹不同，不会互相复用。Receipt 保存受控摘要，不保存 token 或完整响应。

## 工作站发布批次快照

`workstation/state/release-batch.json` 冻结当前本地发布批次，而不是强制要求三种产品同时进入一个批次。快照应记录：

- `batch_scope`：本轮包含的产品键，例如仅 `mkv_hevc`；
- `deferred_products`：明确延后的 `mp4_chs` / `mp4_cht`；
- 产品和中间文件 Artifact；
- 字幕顺序、默认/forced 标志、字体附件数量以及 mkvmerge/ffprobe 验证结果；
- credential 状态快照路径和下一步状态。

未进入批次的 hardsub request 可以继续保持 `pending`，不能伪记为失败。批次可以只包含已经验证的 HEVC 简繁内封 MKV，并将简繁 MP4 延后到存在明确发布需求时再分别单步骤编码。`ready_for_torrent` 不表示已经创建 Torrent 或执行外部发布。

## 当前不提供清理/撤回

代码没有 R2 delete、remote delete、qB remove 或 Anibt withdrawal Stage。失败或后续步骤中止不会自动删除之前已经成功创建的远端资源。
