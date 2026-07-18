# 架构（当前实现）

[English](../architecture.md) · [文档首页](README.md)

## 公开入口

```text
bmlsub CLI ─┐
            ├─ Pipeline / CredentialService
Python API ─┘
                    ↓
              domain execution
                    ↓
 StageRunner + SQLiteJobStore + ArtifactWriter/BatchWriter
```

CLI 主要负责 parser、JSON 字符串解析、确认 flag 和 stdout/stderr/退出码；业务方法转发到 `Pipeline`。Credential CLI 直接使用 `CredentialService`。

## 包职责

| 包 | 当前代码职责 |
|---|---|
| `state` | 数据模型、指纹、workspace DB path、SQLite schema/CRUD/query |
| `execution` | Stage 生命周期、错误归一化、argv-only ProcessRunner |
| `artifacts` | 候选文件、validator、备份、原子替换和 batch commit |
| `assets` | source asset inspector、登记、候选匹配 |
| `media` | ffprobe model、视频登记、轨道选择和提取 |
| `transcription` | MLX backend、切片和 transcript validator |
| `ass_analysis` | parser/model/profile、v4 analysis、fonts、normalize、reconstruct |
| `production` | request model/store contract、Profile、ffmpeg/mkvmerge execution |
| `credentials` | 0600 JSON、manifest、Keychain、SSH config、service、probe |
| `release` | torrent/tracker、R2、remote、qB、Anibt clients/Profile/execution |
| `workstation` | 番组元数据继承、三阶段单集编排、真实单步骤 delivery、可读状态/Artifact/批次快照 |
| `pipeline` | 组合上述能力的公开 facade |
| `cli` | argparse 命令树和最终 JSON contract |

## StageRunner

每次调用新建 Run/Stage；输入验证和 `stage_inputs` 登记在复用检查前进行。复用键由 stage name、input/parameter/tool fingerprint 组成，且历史 Artifact 必须再次通过 validator。Adapter 只能返回 succeeded/skipped/needs_review；异常统一为 failed 或 needs_review。

## SQLite 与文件

SQLite 是已迁移 Stage 的状态来源。文件内容不存入 SQLite；Artifact 保存有界身份和 metadata。正式文件先由 writer 提交，再由 StageRunner 登记 Artifact，所以两者间存在明确 transaction seam。

工作站层固定把 SQLite 放在 `<episode>/workstation/state/state.sqlite3`，并导出 `config.json`、`manifest.json`、`summary.json`、逐步骤和 Artifact JSON。`config.json` 保存从直接父级 `bgminfo/series.json` 解析并应用显式覆盖后的最终配置。`credentials-status.json` 是脱敏可用性快照；`release-batch.json` 冻结本轮产品范围、延后产品、Artifact 和验证结论，两者都不替代 SQLite。

`run_delivery_step()` 直接构造并执行指定底层 request/stage：HEVC 成功后把 Artifact ID 写入 manifest，mux 再消费该 HEVC、两份字幕和全部字体。它不会先调用完整 `run_delivery()`。

## 当前锁

Credential manifest 有文件锁；SQLite 和 `os.replace` 分别保护各自操作。没有覆盖全部 Stage 的 job/output lock，也没有 daemon 负责单一调度。因此架构是可靠的单机 Headless Core，不是多进程任务服务器。

## 当前未实现的运行层

没有 GUI、常驻 daemon、队列、取消/暂停、Remote Worker、插件系统或 Web 控制台。它们不是隐藏入口，也没有预建空实现。
