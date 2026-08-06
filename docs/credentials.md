# 凭据与安全存储

## 清单位置

默认读取顺序：

1. `~/Library/Application Support/BMLSub/credentials.json`
2. 兼容读取 `~/.config/bml/credentials.json`

清单只保存 Profile 的类型、非敏感设置和 Keychain 引用。文件必须是当前用户拥有的普通文件，权限为 `0600` 或更严格。

## Secret 存储

R2、qBittorrent 和 Anibt 的密码、Token、Access Key 使用 macOS Keychain 或经过验证的原生系统 Keyring。SSH Profile 只保存 OpenSSH `Host` 别名及有限的连接预期值。`remote_pull` 只引用 SSH Profile 和 rclone remote。

不要把以下内容写入 `series.json`、计划、回执、诊断或普通日志：

- R2 Secret Access Key
- qBittorrent 密码
- Anibt Token
- 私钥或完整第三方响应

## Workstation 配置

首次 `ws end` 会要求选择或创建：

```text
r2
ssh
qbittorrent
anibt
```

配置摘要只保存 alias、bucket、远端目录、qB WebUI Origin/端口等非敏感值。`ws end yes` 只读取已有 Profile，不创建或轮换 Secret。

## 检查清单

修改凭据前先复制清单并保留 `0600` 权限。删除 Profile 前确认没有 `remote_pull` 或系列发布配置引用它；删除 Secret 应使用项目已有的 Profile 删除流程，避免只删 JSON 而遗留钥匙串项目。
