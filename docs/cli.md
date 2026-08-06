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
bmlsub rebuild [option]
```

`option` 必须是 `bgminfo`、`ensub`、`trans`、`pubinfo`、`encode`、`torrent`、
`upr2`、`dlvps`、`seed`、`anibt` 之一。`bmlsub ws start` 没有业务参数；
`bmlsub ws end` 的可选位置参数只有 `yes`。`build` 和 `rebuild` 的 option
来自同一份注册表。

## 帮助与退出码

```bash
bmlsub --help
bmlsub ws end --help
bmlsub build --help
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
