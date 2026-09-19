# CLI 参考

## 公开命令

```text
usage: bmlsub [-h] [--version] {ws,build,rebuild} ...
```

顶层命令只有：

```text
bmlsub ws start
bmlsub ws end [yes]
bmlsub build [option]
bmlsub build fanhua [file-or-directory]
bmlsub build editsub [file-or-directory]
bmlsub rebuild [option]
```

`option` 必须是 `bgminfo`、`ensub`、`trans`、`pubinfo`、`encode`、`torrent`、
`upr2`、`dlvps`、`seed`、`anibt` 之一。`bmlsub ws start` 没有业务参数；
`bmlsub ws end` 的可选位置参数只有 `yes`。`build` 和 `rebuild` 的 option
来自同一份状态化操作注册表。`fanhua` 和 `editsub` 是额外的 `build`
文件操作，不支持 `rebuild`；路径参数可省略，省略时处理当前目录。

## 字幕繁化

```bash
bmlsub build fanhua
bmlsub build fanhua 'episode.chs&jpn.ass'
bmlsub build fanhua relative/subtitles
bmlsub build fanhua /absolute/path/to/subtitles
```

- 仅支持 `.ass`，扩展名不区分大小写。
- 文件夹仅扫描直属文件，不递归，并自动排除 `.cht.ass` 与
  `.cht&jpn.ass` 成品。
- 保留 ASS 样式、时间轴、标签和非 Events 区段，只繁化可靠识别出的中文字幕。
- 输出写在各源文件旁；`x.chs&jpn.ass` 输出 `x.cht&jpn.ass`，
  `x.chs.ass` 输出 `x.cht.ass`，其他文件名输出 `<stem>.cht.ass`。
- 写入前检查同批输出名冲突，写入临时文件并验证 ASS 结构后再原子替换。

## 字幕文本整理

```bash
bmlsub build editsub
bmlsub build editsub 'episode.ja[cc].srt'
bmlsub build editsub relative/subtitles
bmlsub build editsub /absolute/path/to/subtitles
```

- 支持 `.ass`、`.srt`、`.vtt`，扩展名不区分大小写。
- 文件夹仅扫描直属文件，不递归；文件顺序稳定。
- 相对路径以执行命令时的当前目录为基准。
- 无论输入位于哪里，输出始终写入执行命令时的当前目录。
- 输出固定命名为 `<原文件名 stem>_processed.txt`。
- 使用 SubsRefine 整理字幕后，删除全部 Unicode 标点、装饰符号和控制字符。
- 内部空白统一折叠为日文全角空格 U+3000；删除行首、行尾空格和空白行。
- 输出为无 BOM 的 UTF-8、LF 换行，并在 JSON 中报告行数和验证结果。
- 同批输入若会生成同名输出，会在写入前整体拒绝；已存在的输出只在新结果完整验证后原子替换。

SubsRefine 固定引用版本见
<https://github.com/MingYSub/SubsRefine/tree/c47cec799fb5615d74a6561a7b6a91054c669c4b>，
Copyright (c) 2025 MingYSub，采用 MIT License。完整许可见
`LICENSES/SubsRefine-MIT.txt`，集成来源见 `THIRD_PARTY_NOTICES.md`。该软件
许可不代表已取得输入字幕内容的版权，用户需自行确认有权处理输入文件。

## 帮助与退出码

```bash
bmlsub --help
bmlsub ws end --help
bmlsub build --help
bmlsub build fanhua --help
bmlsub build editsub --help
bmlsub rebuild --help
```

非 TTY 的 `build`/`rebuild` 菜单返回 `needs_review`，不创建状态。JSON 输出只写 stdout；进度和诊断写 stderr。

退出码：

- `0`：成功、复用、等待确认或普通阻断。
- `1`：失败或拒绝操作。
- `2`：`needs_review` 或 parser 用法错误。

旧命令组（`workstation`、`credentials`、`release` 等）和业务 flag 已从公开 parser 删除；它们会在进入状态或网络层前被拒绝。

## Option 对照表

| Option | 英文全称 | 中文含义 | 主要职责 | 外部动作 | 可 rebuild |
| --- | --- | --- | --- | --- | --- |
| `bgminfo` | Bangumi Information | 番组信息 | 创建或验证番组元数据 | 否 | 是 |
| `ensub` | English Subtitle Extraction | 英文参考字幕提取 | 从视频提取文本参考字幕 | 否 | 是 |
| `trans` | Transcription | 音频提取与转录 | 提取音频并运行 Whisper | 否 | 是 |
| `pubinfo` | Publication Information | 发布信息配置 | 保存非敏感发布参数与凭据引用 | 否 | 是 |
| `encode` | Encode | 视频编码与封装 | 转码、字幕内嵌或字幕/字体内封 | 否 | 是 |
| `torrent` | Torrent Creation | Torrent 创建 | 为现有内容生成 Torrent | 否 | 是 |
| `upr2` | Upload to R2 | 上传至 R2 | 上传内容并登记 R2 回执 | 是 | 是 |
| `dlvps` | Download to VPS | 下载到 VPS | 根据 R2 回执将文件拉取到 VPS | 是 | 是 |
| `seed` | Torrent Seeding | Torrent 做种 | 让 qBittorrent 校验内容并做种 | 是 | 是 |
| `anibt` | Anibt Publication | Anibt 发布 | 使用 Torrent 发布 Anibt 条目 | 是 | 否 |
