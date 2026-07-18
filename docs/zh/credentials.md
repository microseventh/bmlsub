# 凭证配置（当前实现）

[English](../credentials.md) · [文档首页](README.md)

## Manifest 发现和文件要求

未显式传路径时，读取优先级是：已存在的 `~/Library/Application Support/BMLSub/credentials.json`，其次是已存在的 `~/.config/bml/credentials.json`；都不存在时返回 Application Support 路径。

Manifest 和一次性 secret JSON 都通过 `load_secure_json()` 读取，必须是当前用户所有、普通非 symlink 文件，权限为 `0600` 或更严格。Manifest schema 固定为 `bmlsub-credentials-v1`，backend 固定为 `macos-keychain`。

## Profile kind 和字段

| kind | 非秘密 settings | Keychain/外部身份 |
|---|---|---|
| `r2` | `keychain_account`（默认 alias） | JSON：`account_id`、`access_key_id`、`secret_access_key`、可选 HTTPS `endpoint` |
| `qbittorrent` | `keychain_account` | JSON：`username`、`password` |
| `anibt` | `keychain_account`、HTTPS `api_url` | JSON：`token` |
| `ssh` | `ssh_alias`，可选 `expected_host/user/port` | OpenSSH config/agent/系统管理的 key |
| `remote_pull` | `ssh_profile`、`rclone_remote` | 引用一个 ssh profile；rclone secret 在服务器 |

Profile 还可有非空 `label`（≤128）和 `description`（≤512）。Alias 只允许字母、数字和 `._@-`。

## CRUD 事务

`CredentialService` 对 manifest lock file 使用 `fcntl.flock(LOCK_EX)`。Secret profile 更新先准备 Keychain 值，再原子写 manifest；任一步失败时尝试恢复新/旧 Keychain item 和旧 manifest。删除要求显式 `confirmed=True`，并拒绝删除被 remote-pull 或注入的项目 reference checker 引用的 profile。

## CLI

```bash
bmlsub credentials list
bmlsub credentials get --profile r2-main
bmlsub credentials status
bmlsub credentials validate
```

创建 SSH profile：

```bash
bmlsub credentials create \
  --alias media-vps \
  --kind ssh \
  --settings-json '{"ssh_alias":"media-vps","expected_host":"media-vps.example.test","expected_user":"deploy","expected_port":22}'
```

Secret kind 必须提供 `--input`：

```bash
bmlsub credentials create \
  --alias r2-main \
  --kind r2 \
  --input /path/to/protected/r2.json
```

`import-json` 导入带 `secret` 子对象的完整 bundle；`upsert-secret` 是仅用于 r2/qb/anibt 的兼容入口。系统不会自动删除一次性输入文件。

## status、validate、probe

- status/list/get：返回 alias、kind、reference、available 和允许公开的 settings；
- validate：读取 Keychain payload 并按严格字段验证，解析并校验 SSH identity，不进行网络请求；
- probe：执行有界只读外部请求。CLI parser 要求 `--confirm-external-action`，Python service 本身没有确认参数。

## 工作站凭证状态快照

番组 `series.json` 的 `publish.credential_aliases` 只保存 alias。工作站可将本集实际引用 alias 的脱敏状态写入 `workstation/state/credentials-status.json`，字段限于 alias、kind、reference、available、允许公开的 settings 和检查时间；不得包含 Keychain payload、token、password 或 access key。

该文件是发布准备快照，不取代 credential manifest，也不表示已经执行网络 probe。公开文档中的 alias 均为示例；实际项目只应记录番组配置引用的脱敏 alias 和 available 状态。

## Release 兼容凭证来源

正常路径是 manifest + profile alias。当前代码仍支持：

- R2：指定名称的环境变量或一个安全 JSON 文件；
- qB：环境变量或安全 JSON 文件；
- Anibt：Python 显式 token、环境变量或安全 JSON 文件；CLI 不提供明文 `--token`，只提供 token env/config/profile。

Manifest/profile 与 file（Anibt 还包括显式 token）互斥。兼容来源不会自动迁移或删除。
