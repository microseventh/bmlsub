# ADR 0001：第一周可靠性内核边界

- **状态：** 已接受
- **日期：** 2026-07-15

## 背景

`bmlsub` 重建可靠性内核，目的是避免可靠性概念继续与旧 Pipeline 基于路径和文件存在性的状态假设混合。只有真实迁移 Stage 使用到的媒体函数，才会从旧实现中复制或改造。

## 决策

- Day 1 和 Day 2 首先实现共享状态语言和 SQLite 运行账本；
- 默认状态数据库为 `<workspace>/.bmlsub/state.sqlite3`，调用方可以覆盖状态目录；
- 对已迁移 Stage，SQLite 是执行状态来源，文件存在本身不代表成功；
- CLI stdout 只输出最终机器可读 JSON，进度和 Diagnostic 使用 stderr；
- `needs_review` 与 `failed` 不同，它表示系统为了安全拒绝自动决策，需要人工复核；
- ASS 感知转换无法可靠识别目标时，默认进入 `needs_review`；
- 只有显式请求时才能执行整文件繁化；
- 输入、参数、工具和产物身份分别使用独立指纹；
- 核心模型只使用 Python 标准库，不依赖 CLI、HTTP 或媒体工具；
- Metadata 和 Diagnostic 拒绝疑似凭证字段；
- SQLite 不得保存完整字幕正文和凭证。

## 非目标

第一周不实现：

- GUI；
- daemon；
- Remote Worker；
- 通用 Workflow DSL；
- 外部任务队列；
- 完整 Event Sourcing；
- 全媒体阶段一次性迁移。

## Schema 策略

Schema version 1 在一个事务中创建：

- `runs`
- `stages`
- `artifacts`
- 最小 `events`

初始化必须幂等。遇到不支持的版本时直接拒绝，而不是静默修改数据库。
