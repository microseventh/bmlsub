# 安全边界（当前代码）

[English](../security.md) · [文档首页](README.md)

## 代码强制的数据最小化

`Diagnostic`、`ArtifactRecord.metadata` 和 `ProductionRequest.parameters` 会递归检查字段名，拒绝 password、secret、token、credential、api/access/private key、authorization、cookie 等 secret 标记。该检查防止常见 secret 字段进入状态模型，但不能识别被放在无关字段名下的任意敏感字符串，调用方仍必须遵守边界。

## Secret 所有权

- R2/qB/Anibt：Login Keychain 或显式选择的兼容 env/0600 JSON；
- SSH private key：OpenSSH/ssh-agent/系统管理；
- VPS rclone credential：服务器管理；
- Profile、Stage、Artifact、receipt 只保存脱敏 reference。

## CLI 外部动作确认

Parser 强制以下命令提供 `--confirm-external-action`：credential probe、R2 上传、remote pull、qB 做种、Anibt 发布。该 flag 只控制 CLI 是否允许本次调用；Stage 实现不会保存一个永久授权。Python API 没有这个 flag，嵌入应用必须自行实现确认。

## 文件事务

Writer 使用目标文件系统中的候选、validator、flush/fsync、唯一备份和 `os.replace`。批量输出由 batch writer 协调。源素材登记、ASS analyze 和读取型 API 不修改源文件；normalize/reconstruct/生产/发布 receipt 是新 Artifact 或事务输出。

文件 commit 与 SQLite register 不构成单一原子事务；登记失败时 Stage 不成功，已提交文件保留。当前没有通用跨进程输出锁。

## 网络和远端边界

- 字幕默认 Provider 单次同步请求，无自动重试；
- credential probe 只读，但会访问真实服务；
- R2 upload、remote pull、qB seed、Anibt publish 有真实副作用；
- Stage 复用时，外部 Stage 的 adapter 会按各自契约重新验证远端状态；
- 系统不提供删除 R2 对象、远端文件、qB 任务或 Anibt release 的命令，也不会自动撤回。

## 有界记录

ProcessRunner 限制 stdout/stderr；各 adapter 对 HTTP 状态、JSON 和响应大小做边界处理；receipt 只保留 validator/复用需要的摘要。不要把第三方完整响应或 debug dump 放入自定义 metadata。

## 仓库边界

`.gitignore` 排除缓存、SQLite、日志、备份、credential、媒体、Torrent、receipt、analysis 和字体等生成/私有内容。发布前维护者还需扫描公开树，确认不存在本机路径、私有标识、private key、意外大文件或构建生成物，再构建发布归档。
