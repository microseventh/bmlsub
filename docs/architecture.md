# 架构概览

## 分层

```text
CLI / 交互问答
        ↓
Workstation 编排与独立操作注册表
        ↓
计划、StageRunner、SQLite JobStore
        ↓
Artifact 验证与回执
        ↓
本地媒体工具 / R2 / VPS / qBittorrent / Anibt
```

CLI 只决定入口和确认边界；具体操作负责生成计划、执行步骤、验证输出并登记回执。外部 adapter 不应直接修改 Workstation 状态，状态由统一的 pipeline 和 StageRunner 提交。

## 身份模型

每个 Artifact 有类型、单集、路径、大小、内容哈希和参数/工具指纹。步骤回执同时记录输入 Artifact、输出 Artifact、诊断和可恢复的下一步。当前状态必须同时满足 SQLite、Manifest 和文件系统验证。

## 两条工作流

Workstation 是有序的单集流程，适合从源视频一直推进到发布；独立 Build 是单操作流程，适合普通目录、重试和局部调试。两者共享 Artifact、凭据和回执模型，但 Build 不会隐式启动下游操作。

## 安全边界

Secret Store 与普通 JSON 分离；凭据清单只保存 Profile 元数据。外部动作需要显式确认，`ws end yes` 是唯一自动确认入口。远程删除必须验证旧回执和在线身份，Anibt 因缺少可靠覆盖契约禁止 rebuild。
