# 独立操作与状态

`build <option>` 和 `rebuild <option>` 共享 option 注册表，但一次只执行一个独立操作，不会隐式串联下游。文档统一使用完整单词 `option`，避免缩写产生歧义。

## Option 名称与职责

### `bgminfo` - Bangumi Information（番组信息）

创建或验证 `bgminfo/series.json`，保存番组标题、制作组、Bangumi/Anime 身份以及 Production/Publish 的非敏感配置。它不读取媒体、不编码，也不执行网络发布。

### `ensub` - English Subtitle Extraction（英文参考字幕提取）

检查视频字幕轨并提取支持的文本参考字幕。它只负责字幕提取，不负责音频提取、Whisper 转录或正式字幕制作。

### `trans` - Transcription（音频提取与转录）

从媒体提取归档音频和 Whisper 使用的 WAV，并按问答选择 direct、chunked 或不转录。它不提取字幕，也不生成正式 ASS。

### `pubinfo` - Publication Information（发布信息配置）

保存 bucket、远端目录、qBittorrent WebUI、发布说明和 Credential Profile alias 等非敏感参数。密码、Token 和 Access Key 仍存放在系统安全存储中。

### `encode` - Encode（视频编码与封装）

执行视频转码、ASS 内嵌、字幕/字体内封或组合生产方案。它只生成媒体产品，不创建 Torrent，也不上传文件。

### `torrent` - Torrent Creation（Torrent 创建）

为选定文件或目录生成 Torrent，登记内容身份、分片和 tracker 信息。它不会上传 Torrent 或自动开始做种。

### `upr2` - Upload to R2（上传至 R2）

`up` 表示 upload，`r2` 表示 Cloudflare R2。该 option 上传本地内容并写入经过验证的 R2 回执，不负责 VPS 拉取。

### `dlvps` - Download to VPS（下载到 VPS）

`dl` 表示 download，`vps` 表示 Virtual Private Server（虚拟专用服务器）。该 option 根据有效 R2 回执将一个精确对象拉取到 VPS，并登记远端文件身份。

### `seed` - Torrent Seeding（Torrent 做种）

将 Torrent 添加到 qBittorrent，校验 VPS 上的现有内容并进入做种状态。它不会重新上传或复制媒体文件。

### `anibt` - Anibt Publication（Anibt 发布）

使用已验证的 Torrent 和番组身份发布 Anibt 条目，可按配置同步 Nyaa。Anibt 是服务名称，不是本项目自定义文件格式；因为远端没有可靠覆盖契约，该 option 不允许 rebuild。

## 状态目录

独立操作从当前目录建立：

```text
.bmlsub/build/state.sqlite3
.bmlsub/build/manifest.json
.bmlsub/build/plans/
.bmlsub/build/receipts/
.bmlsub/backups/
```

Workstation 使用单集目录下的：

```text
workstation/state/manifest.json
workstation/state/summary.json
workstation/state/steps/<phase>.<step>.json
workstation/state/artifacts/<artifact-id>.json
```

## Build

没有 option 时，TTY 显示问答菜单；非 TTY 不猜测输入，返回 `needs_review`。发现输入默认只扫描当前目录第一层，并排除隐藏文件、临时文件、状态目录、回执、备份、目录和符号链接。

确认前会展示输入、输出映射、冲突、产品数量和总大小。计划写入后不可变，回执只有在输出验证通过后才登记。

相同输入、参数和工具身份的当前输出会返回 `skipped`/复用；目标已存在但身份不匹配时，`build` 拒绝覆盖。

## Rebuild

`rebuild` 在替换本地结果前将旧目标移到 `.bmlsub/backups/`，新结果验证成功后再写入新回执。远程操作只有在旧回执与在线身份精确匹配时才允许删除；qBittorrent rebuild 不删除内容文件。

`rebuild anibt` 始终返回 `refused`，因为 Anibt 没有可靠的远端覆盖或删除契约。

## 失败恢复

单个输入失败不会删除同批次其他成功结果。外部批次在第一个错误处停止；下一次运行验证已有回执并从缺失、失败或失效步骤继续。中断时不会留下可执行的 `running` 状态。
