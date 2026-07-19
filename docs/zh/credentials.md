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

SSH 凭据配置名称和 OpenSSH Host 别名是两个不同概念。例如，bmlsub Manifest 中的 Profile 可以命名为 `staging-vps-profile`，但其中的 `ssh_alias` 是 `media-vps`，后者才是 `~/.ssh/config` 里的实际 `Host`。向导会明确分别提示“bmlsub 凭据配置名称”和“OpenSSH Host 别名”，并通过 `CredentialService.resolve_ssh()` 解析后分别保存为 `credential_aliases.ssh: staging-vps-profile` 与 `publish.ssh_alias: media-vps`，不得把 Profile 名称当作 Host 别名。

Profile 还可有非空 `label`（≤128）和 `description`（≤512）。Alias 只允许字母、数字和 `._@-`。

## CRUD 事务

`CredentialService` 对 manifest lock file 使用 `fcntl.flock(LOCK_EX)`。Secret profile 更新先准备 Keychain 值，再原子写 manifest；任一步失败时尝试恢复新/旧 Keychain item 和旧 manifest。删除要求显式 `confirmed=True`，并拒绝删除被 remote-pull 或注入的项目 reference checker 引用的 profile。

## `start delivery` 配置问答

当文件交付配置不完整时，交互式执行：

```bash
bmlsub workstation start delivery
```

普通用户通过 Workstation 外部交付向导管理凭证：

```bash
bmlsub workstation start delivery --configure
```

三个用户入口分别是：`bmlsub workstation start`（Workstation 交互式快速模式）、`bmlsub workstation start delivery`（交互式外部交付）和 `bmlsub workstation start delivery -y`（无人值守外部交付）。无人值守模式只读取已有且验证有效的 Profile，不会询问 Secret；缺失或无效时返回 `needs_review`，应重新使用 `--configure` 或独立 credential 命令修复。

使用 `--configure` 时会在 TTY 中强制进入配置向导，即使当前发布 plan 已完整。向导首先检查默认 Credential Manifest 和已有 Profile：available Profile 显示“复用”，不会再次询问 Secret；unavailable 的 secret Profile 显示“修复”，会重新询问完整 secret 并更新 Keychain；也可以选择“新建”。首次没有 Manifest 时，会创建权限为 `0600` 的非敏感 Manifest，并使用默认 namespace `main`（可在问答中修改）。R2 新建或修复会明确询问 `R2 Account ID`、`R2 Access Key ID`、`R2 Secret Access Key` 和可选 endpoint；机密访问密钥使用隐藏输入并存入 macOS Keychain。

向导写入 `series.json` 的只有 bucket、VPS 宿主机目录、qB Docker 容器目录、SSH/rclone/qB 服务参数和四个 credential alias。`publish.remote_root` 严格表示 VPS 宿主机上的平铺目录，例如 `/data/dcapp/qb/downloads`；宿主机上的 R2/rclone/SSH 会把文件保存为 `/data/dcapp/qb/downloads/<filename>`。`publish.qb_save_path` 则严格表示 qBittorrent 容器内由 Web API 使用的对应目录，默认 `/downloads`。两者位于不同文件系统命名空间，不要求字符串相同；部署必须通过 Docker volume（例如 `./downloads:/downloads`）把相同文件名连接起来。R2 object key 的番组/单集分层与这两个目录相互独立。R2 Secret Access Key、qB 密码和 Anibt Token 通过隐藏输入直接写入 macOS Keychain，不写临时 JSON、不回显，也不进入 series、SQLite 或 Artifact。SSH 只引用并验证 `~/.ssh/config` 中的 Host alias，不保存私钥。

重复运行时，已存在且 available 的 Profile 会显示为“复用”并默认优先选择，不会再次询问 Secret 或覆盖 Keychain。已有但 unavailable 的 Profile 会显示为“修复”，可以在同一向导中重新输入 R2/qB/Anibt secret 并写回 Keychain。非交互模式不会询问或接收明文 Secret；`--configure` 只在 TTY 中启用问答，非 TTY 会返回 `run_configuration_in_tty`。

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
