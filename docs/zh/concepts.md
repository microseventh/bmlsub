# 核心概念与当前边界

[English](../concepts.md) · [文档首页](README.md)

## 状态模型

代码中的状态枚举为：

- Run：`pending`、`running`、`succeeded`、`failed`、`needs_review`、`interrupted`；
- Stage：`pending`、`running`、`succeeded`、`failed`、`skipped`、`stale`、`needs_review`；
- Artifact validation：`discovered`、`unverified`、`valid`、`invalid`、`stale`；
- Asset match：`confirmed`、`inferred`、`ambiguous`、`unmatched`。

`StageResult.reused=true` 只允许与 `status=skipped` 同时出现；`failed` 结果必须包含结构化 `error`；`needs_review` 必须显式设置 review 标志。

## Artifact 和实际输入

每个输入或正式输出都是独立 Artifact，记录路径、大小、mtime、可选内容哈希、来源/参数指纹、验证状态、metadata 和用途。`ArtifactRecord` 会拒绝 metadata 中名称含 password、secret、token、credential、private key、cookie 等 secret 标记的字段。

`ProductionRequest.inputs` 表达请求选择；`stage_inputs` 记录某一次 Stage 最终实际消费的 Artifact、role 和 ordinal。下游失效和审计以实际输入关系为准。

## ProductionRequest

当前 CLI 可创建并执行：

| operation | output profile | 输入 |
|---|---|---|
| `encode` | `hevc-10bit` | 一个视频 |
| `hardsub` | `h264-chs` / `h264-cht` | 一个视频、一份对应语言字幕、可选字体 |
| `mux_subtitle` | `mkv-subtitle` | 一个视频、至少一份有序字幕、可选字体/章节/附件 |

模型中有 `remux` 枚举，但当前 CLI choices 不含 `remux`，执行 Profile 也会拒绝它。

工作站的标准内封链是：直接源视频 → `generated.video.hevc` → `generated.video.muxed`。Mux 的视频输入允许有效的 HEVC Artifact；hardsub 仍必须从直接源/参考视频分叉，不能从 HEVC 中间文件二次编码。字幕输入 ordinal 决定内封顺序，默认工作站顺序为简体、繁体；全部顶层 Aegisub 字体 Artifact 都是 mux 和 hardsub 的真实输入。

## 字体诊断边界

Aegisub Fonts Collector 是字体 family/variant/glyph 完整性的权威边界。BMLSub 会登记字体、分析 ASS、记录 `font-report.json`，并在内封时验证附件数量和身份，但 analysis 的 missing variant/glyph 计数是非阻断诊断。`delivery.validate_subtitles_fonts` 因此可在报告存在时记为成功，同时明确 `blocking=false`、owner 为 Aegisub。

## StageRunner 的实际顺序

1. 创建 Run 和 Stage；
2. 重新验证每个输入 Artifact，失效时标记 stale 并失败；
3. 登记 `stage_inputs`；
4. 未指定 `force` 时按 stage name + 输入/参数/工具指纹查找历史成功；
5. 再验证历史输出，完整时返回 `skipped/reused=true`，否则把旧 Stage 标为 stale；
6. 标记当前 Stage running 并调用 adapter；
7. 登记 adapter 返回的 Artifact；
8. 完成、跳过、要求复核或失败，并结束 Run。

默认 Artifact validator 在存在 `content_hash` 时会验证哈希，否则验证登记的文件身份。

## 文件事务边界

正式文件由 `ArtifactWriter`/`ArtifactBatchWriter` 生成候选、验证、备份并原子替换。文件提交和 SQLite Artifact 登记不是同一个跨系统原子事务：如果文件已经提交但 Artifact 登记失败，Stage 会失败，文件会保留用于诊断/重试，但不会成为可复用成功。

## 当前并发边界

- Credential manifest CRUD 使用 `fcntl.flock` 锁文件；
- SQLite 自己串行化写入；
- 正式文件使用原子替换；
- **没有**通用 episode、Artifact 或输出路径的跨进程任务锁。

因此不要从两个进程同时执行会写入同一正式目标的 Stage。系统通常能避免半文件，但不保证避免重复计算、重复备份或后提交覆盖先提交。

## 当前网络与任务边界

- 默认字幕转换 Provider 是一次同步 `requests.post()`；没有自动重试和退避；网络异常会形成可重试的失败结果。
- 已有文件不会自动推断为历史成功 Stage。
- `run show` 和 `Pipeline.get_run()` 只读；没有取消、暂停、继续、重新排队或进度订阅。
- 没有 GUI、daemon、任务队列和 Remote Worker。
