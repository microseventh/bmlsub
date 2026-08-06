# 故障排查

## `ws end` 返回 `needs_review`

先查看 JSON 中的 `error.code`、`next_action` 和 `inspection.detected_phase`。常见原因是正式 ASS、字体、本地产品、Torrent 或发布配置缺失。按 `next_action` 完成输入后重跑，不要直接使用 `force`。

## 凭据清单错误

清单损坏会返回 `credential_manifest_invalid`，消息包含路径、行号和列号。检查文件是否为合法 JSON、由当前用户拥有且权限为 `0600`。不要把 Secret 粘贴到普通 JSON；优先从备份恢复非敏感清单，再使用交互配置修复引用。

## Anibt `404 Anime not found`

这通常不是 Token 错误，而是 `bgm_id`/`anime_id` 不存在或与当前番组不匹配。更新 `bgminfo/series.json` 中的身份后重跑 `ws end`。R2、VPS 和 qB 已有成功回执时，不需要重新编码。

## 外部阶段中断

再次运行交互式 `ws end`，确认已有阶段即可继续；如果选择 `n`，程序会返回 `awaiting_confirmation`，不会删除已有回执。需要控制 Nyaa 时不要使用自动启用 Nyaa 的 `ws end yes`。

## 输出目标冲突

`build` 不覆盖身份不同的现有目标。使用对应的 `rebuild <option>`，让程序先备份旧目标并验证新结果。不要手动删除 `.bmlsub`、Workstation Manifest 或回执来绕过冲突。

## 需要提交问题时

提供：命令、`--version`、脱敏后的 JSON 错误、阶段和 `next_action`。移除路径中的隐私信息、Token、密码、Cookie、SSH 主机敏感配置和完整第三方响应。
