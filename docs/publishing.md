# 发布与断点续跑

## 顺序

`ws end` 的外部顺序固定为：

```text
R2 上传 → VPS 拉取 → qBittorrent 校验/做种 → Anibt 发布
```

每个产品包含视频和 Torrent。下游阶段只能消费上游成功回执，不会在 R2 失败时启动 VPS、qBittorrent 或 Anibt。

## 确认模式

交互式 `ws end`：

- 先展示产品、目标路径、凭据 alias 和外部动作。
- 可逐产品确认 R2、VPS、qBittorrent、Anibt。
- 可选择是否启用 Nyaa 代发。

`ws end yes`：

- 使用保存的发布配置和产品范围。
- 不进入配置问答。
- 自动确认外部动作并启用 Nyaa 代发。
- 配置缺失、身份漂移、回执无效或路径冲突时停止并返回 `needs_review`。

## 回执

R2 回执记录对象键、大小和哈希；VPS 回执记录远端目标；qB 回执记录任务和校验身份；Anibt 回执记录发布 Profile、Torrent 元数据和 API 结果。相同身份的回执可复用，不会重复执行成功操作。

## Anibt 注意事项

发布前必须填写 Anibt 可识别的 Bangumi/Anime 身份。存在 `bgm_id` 或 `anime_id` 只说明配置完整，不保证远端条目存在；HTTP 404 `Anime not found` 表示外部条目身份错误，应修正 `bgminfo/series.json` 后重跑。

Anibt 没有可靠覆盖契约，因此 `rebuild anibt` 永久拒绝。失败发布不会生成成功回执，重跑会从 Anibt 缺失阶段继续。
